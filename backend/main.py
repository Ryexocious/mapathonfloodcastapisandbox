from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from data_fetcher import fetch_weather_forecast, fetch_overpass_data, process_osm_data
from ml_model import predict_flood_depths
from earth_engine import get_sar_water_mask, get_slope_gradient
app = FastAPI(title="Resilience-Mesh API")

# Allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/scan")
def scan_area(lat: float, lon: float, radius: int = 1500):
    """
    Scan a region, get building vulnerabilities, and return GeoJSON.
    """
    # 1. Fetch Weather (Open-Meteo)
    rainfall_mm, max_wind_kmh = fetch_weather_forecast(lat, lon)
    
    # 2. Fetch OSM Data (Buildings, waterways)
    osm_data = fetch_overpass_data(lat, lon, radius)
    if not osm_data:
        raise HTTPException(status_code=500, detail="Failed to fetch OSM infrastructure data.")
        
    # Process building nodes and get distances/elevation summaries
    buildings = process_osm_data(osm_data, lat, lon)
    
    if not buildings:
        return {"type": "FeatureCollection", "features": [], "stats": {"total": 0}}

    # 3. Predict flood depth using ML Model
    ml_features = []
    for b in buildings:
        # 3.a Mocking soil moisture for the proof of concept (based on rainfall proxy)
        soil_moisture = min(1.0, 0.4 + (rainfall_mm / 200.0))
        
        # 3.b Real-time Google Earth Engine SAR call 
        # (It degrades gracefully to mocked 0.0 if GEE isn't auth'd or fails)
        sar_water = get_sar_water_mask(b['lat'], b['lon'], buffer_meters=150)
        
        ml_features.append({
            'elevation': b['elevation'],
            'distance_to_water': b['distance_to_water'],
            'rainfall': rainfall_mm,
            'sar_water_presence': sar_water,
            'soil_moisture': soil_moisture
        })
        
    predictions = predict_flood_depths(ml_features)
    
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
        slope_degrees = get_slope_gradient(b['lat'], b['lon'])
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
            "total_buildings": len(buildings),
            "stats": stats
        }
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
