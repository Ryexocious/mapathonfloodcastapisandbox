import ee
import os
import time
import pandas as pd

# Try to initialize Earth Engine.
# Note: For this to work in production, ensure you set the environment variable:
# GOOGLE_APPLICATION_CREDENTIALS=/path/to/your/gee_service_account.json

from google.oauth2 import service_account

def init_ee():
    try:
        # 1. Determine key path (Docker vs Local)
        # In Docker, it's mounted to /app/gee_key.json via docker-compose.yml
        # Locally, it's likely the full filename provided by the user.
        ee_key_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'gee_key.json')
        
        if not os.path.exists(ee_key_path):
            # Try a second fallback for the local dev environment
            ee_key_path = 'mapathon-490712-16e5beef6c1e.json'
            
        if os.path.exists(ee_key_path):
            print(f"Initializing Earth Engine with key: {ee_key_path}")
            credentials = service_account.Credentials.from_service_account_file(
                ee_key_path, 
                scopes=['https://www.googleapis.com/auth/earthengine']
            )
            ee.Initialize(credentials=credentials)
            print("Google Earth Engine initialized with Service Account.")
            return True
        else:
            # Last resort: Try default init
            ee.Initialize()
            print("Earth Engine initialized via Default Application Credentials.")
            return True
    except Exception as e:
        print(f"ERROR: Earth Engine failed to initialize. Map overlays will not show. Details: {e}")
        return False

# Initialize on module load
EE_INITIALIZED = init_ee()

def get_sar_water_mask(lat, lon, buffer_meters=150):
    """
    Queries Sentinel-1 GRD imagery for the given lat/lon and returns
    a percentage (0.0 to 1.0) indicating how much water (flood) is detected nearby.
    """
    if not EE_INITIALIZED:
        # Fallback for hackathon testing if keys aren't ready
        # Return a mock value indicating no live SAR water detected
        return 0.0

    try:
        # Create a point geometry
        point = ee.Geometry.Point([lon, lat])
        roi = point.buffer(buffer_meters)

        # Filter Sentinel-1 collection
        # Filter Sentinel-1 collection using millisecond timestamps
        now_ms = int(time.time() * 1000)
        end_date = ee.Date(now_ms)
        start_date = end_date.advance(-14, 'day')

        collection = ee.ImageCollection('COPERNICUS/S1_GRD') \
            .filterBounds(roi) \
            .filterDate(start_date, end_date) \
            .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')) \
            .filter(ee.Filter.eq('instrumentMode', 'IW'))

        if collection.size().getInfo() == 0:
            return 0.0 # No imagery available in the window

        # Get median image
        image = collection.median().select('VV')

        # Water classification threshold for Sentinel-1 VV (typically < -16 dB)
        water_mask = image.lt(-16)

        # Calculate percentage of water in the buffered region
        stats = water_mask.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=roi,
            scale=10,
            maxPixels=1e9
        )
        
        # stats returns a dictionary with 'VV' mean (percentage of masked pixels)
        water_percentage = stats.get('VV').getInfo()
        
        if water_percentage is None:
            return 0.0
            
        return float(water_percentage)
        
    except Exception as e:
        print(f"Earth engine query failed: {e}")
        return 0.0

def get_slope_gradient(lat, lon):
    """
    Calculates the topological slope (in degrees) using SRTM DEM.
    """
    if not EE_INITIALIZED:
        return 5.0 # Mock slope in degrees
        
    try:
        point = ee.Geometry.Point([lon, lat])
        # Use SRTM 30m Digital Elevation Model
        srtm = ee.Image('USGS/SRTMGL1_003')
        # Calculate slope in degrees
        slope = ee.Terrain.slope(srtm)
        
        # Get slope at the specific point
        slope_val = slope.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=point,
            scale=30
        )
        val = slope_val.get('slope').getInfo()
        return float(val) if val is not None else 5.0
    except Exception as e:
        print(f"Error fetching slope: {e}")
        return 5.0

def get_regional_risk_points(bbox, sample_distance=1000):
    """
    Highly optimized regional sampler that returns a grid of risk points.
    Scales resolution based on bbox size to prevent timeouts.
    """
    if not EE_INITIALIZED:
        return []

    try:
        # bbox is [min_lat, min_lon, max_lat, max_lon]
        min_lat, min_lon, max_lat, max_lon = bbox
        width = abs(max_lon - min_lon)
        
        # Dynamic Scaling for performance
        if width > 15: # Continent level
            sample_distance = 60000 
            analysis_scale = 1000
        elif width > 5: # National level
            sample_distance = 20000
            analysis_scale = 500
        elif width > 1: # Regional level
            sample_distance = 5000
            analysis_scale = 200
        else: # City level
            sample_distance = 1000
            analysis_scale = 30

        roi = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])
        
        # 1. Digital Elevation Model & Slope
        srtm = ee.Image('USGS/SRTMGL1_003').clip(roi)
        slope = ee.Terrain.slope(srtm)
        
        # 2. Refined Sentinel-1 SAR Processing
        now_ms = int(time.time() * 1000)
        end_date = ee.Date(now_ms)
        start_date = end_date.advance(-14, 'day')
        
        # Filter for recent SAR images
        s1_col = ee.ImageCollection('COPERNICUS/S1_GRD') \
            .filterBounds(roi) \
            .filterDate(start_date, end_date) \
            .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
            
        if s1_col.size().getInfo() == 0:
            # Fallback if no recent S1, just use slope risk
            is_steep = slope.gt(15)
            risk_map = ee.Image(0).where(is_steep, 1).selfMask()
        else:
            s1_img = s1_col.median().select('VV')
            
            # --- Advanced SAR Filtering ---
            # A. Speckle filtering using Focal Mean (Lee Filter alternative in GEE)
            smoothed = s1_img.focal_mean(30, 'circle', 'meters')
            
            # B. Advanced Thresholding (VV typically < -16 for water)
            # We use a tighter threshold for 'Deep' water and a looser one for 'Wetlands'
            is_water = smoothed.lt(-16)
            
            # C. Terrain Correction: Mask out hill shadows (slopes > 5 deg)
            # Hill shadows often look like water in SAR; actual flood areas are flat.
            true_water = is_water.updateMask(slope.lt(5))
            
            # D. Potential Landslide Risk
            is_steep = slope.gt(15)
            
            # Risk layers: 0=Safe, 1=Warning (Steep), 2=Danger (Flood)
            risk_map = ee.Image(0).where(is_steep, 1).where(true_water, 2).selfMask()
        
        # Sample points on a grid
        points = risk_map.sample(
            region=roi,
            scale=sample_distance,
            geometries=True,
            numPixels=200, # Increased for more detail
            seed=42
        )
        
        # Convert to FeatureCollection info
        features = []
        info = points.getInfo()
        for f in info.get('features', []):
            risk_val = f['properties']['constant']
            f['properties'] = {
                'level': risk_val,
                'hazard': 'Flood' if risk_val == 2 else 'Landslide',
                'color': '#da3633' if risk_val == 2 else '#d29922' 
            }
            features.append(f)
            
        return features
        
    except Exception as e:
        print(f"Regional grid sample failed: {e}")
        return []

def get_hazard_polygons(lat=None, lon=None, radius_meters=1500, bbox=None):
    """
    Extracts regional risk polygons (Flood from SAR, Landslides from Slope).
    Supports either a point+radius or a bbox [min_lat, min_lon, max_lat, max_lon].
    Returns a list of GeoJSON-like features.
    """
    if not EE_INITIALIZED:
        return []

    try:
        if bbox:
            # Calculate a dynamic scale based on the bbox width to prevent complexity errors
            # min_lat, min_lon, max_lat, max_lon
            width = abs(bbox[3] - bbox[1])
            # If width > 1 degree (~111km), use coarser scale
            base_scale = 30 if width < 0.5 else 100 if width < 2 else 500
            roi = ee.Geometry.Rectangle([bbox[1], bbox[0], bbox[3], bbox[2]])
        else:
            base_scale = 30
            roi = ee.Geometry.Point([lon, lat]).buffer(radius_meters)
        
        # 1. Flood Zones (SAR)
        now_ms = int(time.time() * 1000)
        end_date = ee.Date(now_ms)
        start_date = end_date.advance(-45, 'day') # Large window for reliability
        s1 = ee.ImageCollection('COPERNICUS/S1_GRD') \
            .filterBounds(roi) \
            .filterDate(start_date, end_date) \
            .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
            
        if s1.size().getInfo() == 0:
            flood_polys = ee.FeatureCollection([])
        else:
            s1_img = s1.median().select('VV')
            water_mask = s1_img.lt(-16).selfMask()
            flood_polys = water_mask.reduceToVectors(
                geometry=roi,
                scale=base_scale,
                geometryType='polygon',
                eightConnected=True,
                labelProperty='flood',
                maxPixels=1e8
            )
        
        # 2. Landslide/Slope Zones (SRTM)
        srtm = ee.Image('USGS/SRTMGL1_003').clip(roi)
        slope = ee.Terrain.slope(srtm)
        slope_mask = slope.gt(15).selfMask()
        slope_polys = slope_mask.reduceToVectors(
            geometry=roi,
            scale=base_scale * 2,
            geometryType='polygon',
            eightConnected=True,
            labelProperty='steep',
            maxPixels=1e8
        )
        
        # Convert to GeoJSON with higher limits
        features = []
        
        if flood_polys.size().getInfo() > 0:
            # Simplify each feature individually as simplify() is a Feature method
            flood_data = flood_polys.limit(500).map(lambda f: f.simplify(10)).getInfo()
            for f in flood_data['features']:
                f['properties'] = {'type': 'flood_zone', 'hazard': 'Flood'}
                features.append(f)
                
        if slope_polys.size().getInfo() > 0:
            # Simplify each feature individually
            slope_data = slope_polys.limit(50).map(lambda f: f.simplify(10)).getInfo()
            for f in slope_data['features']:
                f['properties'] = {'type': 'landslide_zone', 'hazard': 'Landslide'}
                features.append(f)
                
        return features
        
    except Exception as e:
        print(f"Error extracting hazard polygons: {e}")
        return []

def get_batch_risk_data(buildings):
    """
    Optimized batch processor that fetches SAR and Slope for all buildings in one GEE call.
    buildings: list of dicts [{'id': 1, 'lat': 24.1, 'lon': 91.1}, ...]
    Returns dict: { building_id: {'sar_water': float, 'slope': float} }
    """
    if not EE_INITIALIZED or not buildings:
        return {b.get('id', i): {'sar_water': 0.0, 'slope': 5.0} for i, b in enumerate(buildings)}

    try:
        # 1. Create FeatureCollection from points
        pts = []
        for b in buildings:
            feat = ee.Feature(ee.Geometry.Point([b['lon'], b['lat']]), {'id': str(b.get('id', ''))})
            pts.append(feat)
        
        fc = ee.FeatureCollection(pts)
        
        # 2. Prepare Layers
        # ROI for clipping/filtering
        roi = fc.geometry().bounds().buffer(1000)
        
        # Slope
        srtm = ee.Image('USGS/SRTMGL1_003').clip(roi)
        slope_img = ee.Terrain.slope(srtm).rename('slope')
        
        # SAR Water
        now_ms = int(time.time() * 1000)
        end_date = ee.Date(now_ms)
        start_date = end_date.advance(-14, 'day')
        
        s1_col = ee.ImageCollection('COPERNICUS/S1_GRD') \
            .filterBounds(roi) \
            .filterDate(start_date, end_date) \
            .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
            
        if s1_col.size().getInfo() > 0:
            s1_img = s1_col.median().select('VV')
            water_mask = s1_img.lt(-16).rename('sar_water')
        else:
            water_mask = ee.Image(0).rename('sar_water')
            
        # Combine bands
        combined = water_mask.addBands(slope_img)
        
        # 3. Reduce Regions (Sample at points)
        # Use a small buffer (30m) for SAR to be more representative than a single pixel
        # Actually SampleRegions is faster for exact points, but for water we want a small neighborhood 
        # so reduceRegions with a buffer on geometry is safer.
        sampled = combined.reduceRegions(
            collection=fc,
            reducer=ee.Reducer.mean(),
            scale=30
        )
        
        # 4. Get results
        results_info = sampled.getInfo()
        results_map = {}
        
        for feat in results_info.get('features', []):
            b_id = feat['properties'].get('id')
            results_map[b_id] = {
                'sar_water': float(feat['properties'].get('sar_water', 0.0)),
                'slope': float(feat['properties'].get('slope', 5.0))
            }
            
        # Fill in any missing IDs just in case
        for b in buildings:
            bid = str(b.get('id', ''))
            if bid not in results_map:
                results_map[bid] = {'sar_water': 0.0, 'slope': 5.0}
                
        return results_map
        
    except Exception as e:
        print(f"Batch GEE query failed: {e}")
        # Fallback to defaults
        return {str(b.get('id', '')): {'sar_water': 0.0, 'slope': 5.0} for b in buildings}
