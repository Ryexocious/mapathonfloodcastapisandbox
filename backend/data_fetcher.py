import requests
import json
import math

def fetch_weather_forecast(lat, lon):
    """
    Fetch 7-day precipitation forecast and wind forecast from Open-Meteo.
    Returns (total precipitation in mm, maximum wind speed in km/h).
    """
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=precipitation,wind_speed_10m&timezone=auto"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        
        hourly = data.get("hourly", {})
        precip_list = hourly.get("precipitation", [])
        wind_list = hourly.get("wind_speed_10m", [])
        
        total_precip = sum(precip_list) if precip_list else 100.0
        max_wind = max(wind_list) if wind_list else 20.0
        
        return total_precip, max_wind
    except Exception as e:
        print(f"Error fetching weather: {e}")
        return 100.0, 20.0 # fallback defaults

def fetch_overpass_data(lat, lon, radius=1000):
    """
    Fetch building geometries and waterways within a radius using Overpass API.
    """
    overpass_url = "http://overpass-api.de/api/interpreter"
    
    # Query for buildings and waterways around lat, lon
    overpass_query = f"""
    [out:json][timeout:25];
    (
      way["building"](around:{radius},{lat},{lon});
      relation["building"](around:{radius},{lat},{lon});
      way["waterway"](around:{radius},{lat},{lon});
      way["natural"="water"](around:{radius},{lat},{lon});
    );
    out body;
    >;
    out skel qt;
    """
    
    try:
        response = requests.post(overpass_url, data={'data': overpass_query}, timeout=30)
        return response.json()
    except Exception as e:
        print(f"Error fetching OSM data: {e}")
        return None

def calculate_distance(lat1, lon1, lat2, lon2):
    # Haversine formula
    R = 6371000 # radius of Earth in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = math.sin(delta_phi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def process_osm_data(osm_data, lat, lon):
    """
    Parse OSM JSON, build geometries (approximate centroids), and calculate distances to waterways.
    """
    elements = osm_data.get('elements', [])
    nodes = {node['id']: (node['lat'], node['lon']) for node in elements if node['type'] == 'node'}
    
    buildings = []
    waterways = []
    
    for element in elements:
        if element['type'] == 'way':
            tags = element.get('tags', {})
            # Calculate approx centroid from first node
            if 'nodes' in element and len(element['nodes']) > 0:
                first_node_id = element['nodes'][0]
                if first_node_id in nodes:
                    n_lat, n_lon = nodes[first_node_id]
                    if 'building' in tags:
                        buildings.append({
                            'id': element['id'],
                            'lat': n_lat,
                            'lon': n_lon,
                            'type': tags.get('building', 'yes'),
                            'name': tags.get('name', 'Unknown')
                        })
                    elif 'waterway' in tags or tags.get('natural') == 'water':
                        waterways.append((n_lat, n_lon))
                        
    # Ensure there is at least a theoretical water source if OSM is empty for waterways
    if not waterways:
        waterways = [(lat, lon + 0.005)] # Synthetic river nearby
        
    def find_nearest_water_dist(b_lat, b_lon):
        min_dist = float('inf')
        for w_lat, w_lon in waterways:
            d = calculate_distance(b_lat, b_lon, w_lat, w_lon)
            if d < min_dist:
                min_dist = d
        return min_dist

    # Calculate distance to water and mock elevation
    for b in buildings:
        dist = find_nearest_water_dist(b['lat'], b['lon'])
        b['distance_to_water'] = dist
        # MOCK ELEVATION: closer to water -> lower elevation
        # 0m to 10m base depending on distance, plus random noise
        # This replaces OpenTopography for demo purposes without API key
        b['elevation'] = min(15.0, (dist / 100.0) + 1.0)
        
    return buildings
