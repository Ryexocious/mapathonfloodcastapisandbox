from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from data_fetcher import fetch_weather_forecast, fetch_overpass_data, process_osm_data, fetch_river_discharge
from ml_model import predict_flood_depths
from earth_engine import get_sar_water_mask, get_slope_gradient, get_hazard_polygons, get_regional_risk_points, get_batch_risk_data
app = FastAPI(title="Resilience-Mesh API")

# Allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    return {"status": "alive", "engine": "EarthEngine + Open-Meteo"}

@app.get("/api/hazards")
def get_regional_hazards(min_lat: float, min_lon: float, max_lat: float, max_lon: float):
    """
    Fetch regional hazard grid points for the viewport (Flood Hub style).
    """
    bbox = [min_lat, min_lon, max_lat, max_lon]
    width = abs(max_lon - min_lon)
    
    # Adjust resolution based on how far zoomed out we are
    sample_res = 500 if width < 0.2 else 2000 if width < 1 else 10000
    
    risk_points = get_regional_risk_points(bbox, sample_distance=sample_res)
    
    # Polygons (Color Blocks) are now visible even when zoomed out to regional levels
    hazard_zones = []
    if width < 2.5: # increased from 0.5 for country-wide 'blocks'
        hazard_zones = get_hazard_polygons(bbox=bbox)
    
    # Fetch weather and river discharge for the CENTER of the viewport
    center_lat = (min_lat + max_lat) / 2
    center_lon = (min_lon + max_lon) / 2
    rainfall, wind = fetch_weather_forecast(center_lat, center_lon)
    river = fetch_river_discharge(center_lat, center_lon)
    
    status = "Low Risk" 
    has_flood = any('flood' in str(p.get('properties', {}).get('hazard', '')).lower() for p in risk_points)
    has_landslide = any('landslide' in str(p.get('properties', {}).get('hazard', '')).lower() for p in risk_points)
    
    if has_flood and has_landslide:
        status = "CRITICAL: Multiple Hazards"
    elif has_flood:
        status = "Active Flood Zone"
    elif has_landslide:
        status = "Steep Slope Hazard"
    elif rainfall > 50:
        status = "Inbound Heavy Rainfall"
        
    return {
        "type": "FeatureCollection",
        "features": risk_points, 
        "hazard_zones": hazard_zones,
        "metadata": {
            "regional_status": status,
            "has_active_threat": (has_flood or has_landslide or rainfall > 100),
            "rainfall_forecast_mm": round(rainfall, 2),
            "max_wind_speed_kmh": round(wind, 2),
            "river_discharge_m3s": round(river, 2),
            "stats": {"Safe": "N/A", "At-Risk": "N/A", "Danger": "N/A"}
        }
    }

@app.get("/api/scan")
def scan_area(lat: float, lon: float, radius: int = 1500):
    """
    Scan a region, get building vulnerabilities, and return GeoJSON.
    """
    # 1. Fetch Weather (Open-Meteo) & River Discharge
    rainfall_mm, max_wind_kmh = fetch_weather_forecast(lat, lon)
    river_m3s = fetch_river_discharge(lat, lon)
    
    # 2. Fetch OSM Data (Buildings, waterways)
    osm_data = fetch_overpass_data(lat, lon, radius)
    if not osm_data:
        raise HTTPException(status_code=500, detail="Failed to fetch OSM infrastructure data.")
        
    # Process building nodes and get distances/elevation summaries
    buildings = process_osm_data(osm_data, lat, lon)
    
    # 2.b Limit building count to prevent backend/GEE overflow in dense areas
    if len(buildings) > 1000:
        print(f"WARNING: Area too dense ({len(buildings)} buildings). Limiting to 1,000 for performance.")
        buildings = buildings[:1000]
    
    if not buildings:
        return {"type": "FeatureCollection", "features": [], "stats": {"total": 0}}

    # 3. Batch GEE Risk Analysis (One optimized call for all buildings)
    # This replaces individual per-building GEE calls (get_sar_water_mask, get_slope_gradient)
    batch_risk = get_batch_risk_data(buildings)

    # 4. Predict flood depth using ML Model
    ml_features = []
    for b in buildings:
        # 4.a Mocking soil moisture for the proof of concept (based on rainfall proxy)
        soil_moisture = min(1.0, 0.4 + (rainfall_mm / 200.0))
        
        # 4.b Use batch GEE results
        bid = str(b.get('id', ''))
        risk_vals = batch_risk.get(bid, {'sar_water': 0.0, 'slope': 5.0})
        sar_water = risk_vals['sar_water']
        
        ml_features.append({
            'elevation': b['elevation'],
            'distance_to_water': b['distance_to_water'],
            'rainfall': rainfall_mm,
            'sar_water_presence': sar_water,
            'soil_moisture': soil_moisture,
            'river_discharge': river_m3s
        })
        
    predictions = predict_flood_depths(ml_features)
    
    # 3.c Fetch Regional Hazard Polygons (Flood Hub Style)
    hazard_zones = get_hazard_polygons(lat, lon, radius)
    
    # 4. Construct GeoJSON Response
    features = []
    stats = {"Safe": 0, "At-Risk": 0, "Danger": 0}
    
    for i, b in enumerate(buildings):
        depth = predictions[i]
        
        # Determine Flood Risk
        if depth < 0.3:
            flood_status = "Safe"
        elif depth < 1.0:
            flood_status = "At-Risk"
        else:
            flood_status = "Danger"
            
        # Determine Cyclone Risk
        if max_wind_kmh < 60:
            cyclone_status = "Safe"
        elif max_wind_kmh < 90:
            cyclone_status = "At-Risk"
        else:
            if b.get('type') in ['residential', 'hut', 'tin', 'shed', 'house', 'yes']: # OSM tags
                cyclone_status = "Danger"
            else:
                cyclone_status = "At-Risk"
                
        # Determine Landslide Risk
        bid = str(b.get('id', ''))
        slope_degrees = batch_risk.get(bid, {}).get('slope', 5.0)
        
        if slope_degrees > 20 and rainfall_mm > 100:
            landslide_status = "Danger"
        elif slope_degrees > 10 and rainfall_mm > 50:
            landslide_status = "At-Risk"
        else:
            landslide_status = "Safe"
            
        # Overall Unified Risk
        risk_levels = [flood_status, cyclone_status, landslide_status]
        if "Danger" in risk_levels:
            overall_risk = "Danger"
            color = "#dc3545"
        elif "At-Risk" in risk_levels:
            overall_risk = "At-Risk"
            color = "#ffc107"
        else:
            overall_risk = "Safe"
            color = "#28a745"
            
        stats[overall_risk] += 1
        
        # Using circle points for simple Leaflet/MapLibre visualization of 'buildings'
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [b['lon'], b['lat']] # GeoJSON is lon, lat
            },
            "properties": {
                "id": b['id'],
                "name": b['name'],
                "type": b['type'],
                "elevation": round(b['elevation'], 2),
                "distance_to_water": round(b['distance_to_water'], 2),
                "predicted_flood_depth": round(depth, 2),
                "flood_risk": flood_status,
                "cyclone_risk": cyclone_status,
                "landslide_risk": landslide_status,
                "slope_degrees": round(slope_degrees, 2),
                "max_wind_kmh": round(max_wind_kmh, 2),
                "risk_level": overall_risk,
                "color": color
            }
        }
        features.append(feature)
        
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "rainfall_forecast_mm": round(rainfall_mm, 2),
            "max_wind_speed_kmh": round(max_wind_kmh, 2),
            "river_discharge_m3s": round(river_m3s, 2),
            "total_buildings": len(buildings),
            "stats": stats
        },
        "hazard_zones": {
            "type": "FeatureCollection",
            "features": hazard_zones if isinstance(hazard_zones, list) else []
        }
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
