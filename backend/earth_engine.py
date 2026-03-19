import ee
import os

# Try to initialize Earth Engine.
# Note: For this to work in production, ensure you set the environment variable:
# GOOGLE_APPLICATION_CREDENTIALS=/path/to/your/gee_service_account.json

def init_ee():
    try:
        # Tries to authenticate via standard Google Cloud Environment Variable.
        # If running locally without env vars, you will need to authenticate manually.
        ee.Initialize()
        print("Google Earth Engine initialized successfully.")
        return True
    except Exception as e:
        print(f"Warning: Earth Engine failed to initialize. Make sure GOOGLE_APPLICATION_CREDENTIALS is set in terminal. Error: {e}")
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
        # Getting the most recent images from the last 14 days
        end_date = ee.Date(ee.Date.now())
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
