// Initialize MapLibre GL JS
const map = new maplibregl.Map({
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json', // Dark theme basemap
    center: [91.8687, 24.8949], // Sylhet, Bangladesh
    zoom: 13,
    pitch: 45,
    bearing: -17.6
});

let currentPopup = null;

map.on('load', () => {
    // 1. Initialize Sources
    map.addSource('hazard-zones', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addSource('hazard-points', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addSource('buildings-risk', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });

    // 2. Add Regional Heatmap / Grid Layer (Flood Hub Style)
    map.addLayer({
        id: 'hazard-heatmap',
        type: 'heatmap',
        source: 'hazard-points',
        maxzoom: 15,
        paint: {
            'heatmap-weight': ['interpolate', ['linear'], ['get', 'level'], 0, 0, 1, 0.5, 2, 1],
            'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 0, 3, 15, 8], // Boosted intensity
            'heatmap-color': [
                'interpolate', ['linear'], ['heatmap-density'],
                0, 'rgba(0,0,0,0)',
                0.1, '#d29922', 
                0.3, '#ff4d00', 
                0.5, '#da3633', 
                0.8, '#a50f15'  
            ],
            'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 0, 5, 10, 25, 15, 40], // Larger radius
            'heatmap-opacity': 0.9
        }
    });

    // Helper to keep our layers on top
    const bringToFront = () => {
        const layers = ['hazard-heatmap', 'flood-layer', 'landslide-layer', 'buildings-circles'];
        layers.forEach(layerId => {
            if (map.getLayer(layerId)) map.moveLayer(layerId);
        });
    };
    map.on('render', bringToFront);

    // 3. Flood Polygons (Blue Blocks)
    map.addLayer({
        id: 'flood-layer',
        type: 'fill',
        source: 'hazard-zones',
        filter: ['==', ['get', 'type'], 'flood_zone'],
        paint: { 
            'fill-color': '#007cff', 
            'fill-opacity': 0.5, 
            'fill-outline-color': '#ffffff' 
        }
    });

    // 4. Landslide Polygons (Orange Blocks)
    map.addLayer({
        id: 'landslide-layer',
        type: 'fill',
        source: 'hazard-zones',
        filter: ['==', ['get', 'type'], 'landslide_zone'],
        paint: { 
            'fill-color': '#ff4d00', 
            'fill-opacity': 0.5, 
            'fill-outline-color': '#ffffff' 
        }
    });

    // 3. Add Building Layer (Points)
    map.addLayer({
        id: 'buildings-circles',
        type: 'circle',
        source: 'buildings-risk',
        paint: {
            'circle-color': ['get', 'color'],
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 2, 15, 6, 18, 12],
            'circle-stroke-width': 1,
            'circle-stroke-color': '#ffffff',
            'circle-opacity': 0.8
        }
    });

    // 4. Hook up Passive Monitoring
    syncViewportHazards();
    map.on('moveend', syncViewportHazards);

    // 5. Interactivity
    map.on('mouseenter', 'buildings-circles', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', 'buildings-circles', () => map.getCanvas().style.cursor = '');
    map.on('click', 'buildings-circles', handleBuildingClick);
});

async function syncViewportHazards() {
    // Lowered zoom limit to 3 for national overview
    if (map.getZoom() < 3) return; 

    const bounds = map.getBounds();
    const url = `http://localhost:8000/api/hazards?min_lat=${bounds.getSouth()}&min_lon=${bounds.getWest()}&max_lat=${bounds.getNorth()}&max_lon=${bounds.getEast()}`;
    
    try {
        const res = await fetch(url);
        if (!res.ok) throw new Error("Hazards API offline");
        const data = await res.json();
        console.log("Regional hazards fetched:", data.features.length, "points,", (data.hazard_zones?.features?.length || 0), "blocks");
        
        map.getSource('hazard-points').setData(data); 
        if (data.hazard_zones) {
            map.getSource('hazard-zones').setData(data.hazard_zones);
        }
        
        updateRegionalDashboard(data.metadata);
        updateDashboard(data.metadata, true); // true = regional sync

    } catch (e) {
        console.warn("Viewport sync issue:", e);
        updateRegionalDashboard({ regional_status: "Backend Disconnected", has_active_threat: false });
    }
}

function updateRegionalDashboard(meta) {
    const title = document.querySelector('.sidebar h3');
    if (title) {
        title.innerText = `Regional Status: ${meta.regional_status || 'Scanning...'}`;
        title.style.color = meta.has_active_threat ? '#da3633' : '#3fb950';
    }
}

document.getElementById('scanBtn').addEventListener('click', async () => {
    const coordsStr = document.getElementById('coordsInput').value;
    const radius = document.getElementById('radiusInput').value || 1500;
    
    const parts = coordsStr.split(',');
    if (parts.length !== 2) {
        alert("Please enter valid Lat, Lon format. Example: 24.8949, 91.8687");
        return;
    }
    
    const lat = parseFloat(parts[0].trim());
    const lon = parseFloat(parts[1].trim());
    
    if (isNaN(lat) || isNaN(lon)) {
        alert("Invalid coordinates.");
        return;
    }

    setLoading(true);

    try {
        // Fly to location
        map.flyTo({ center: [lon, lat], zoom: 14.5 });

        // Call our FastAPI backend
        const response = await fetch(`http://localhost:8000/api/scan?lat=${lat}&lon=${lon}&radius=${radius}`);
        if (!response.ok) throw new Error("API call failed");
        
        const data = await response.json();
        console.log("Scan complete:", data.features.length, "buildings found.");
        
        // Update both layers
        map.getSource('buildings-risk').setData(data);
        if (data.hazard_zones) {
            map.getSource('hazard-zones').setData(data.hazard_zones);
        }
        
        updateDashboard(data.metadata);

    } catch (error) {
        console.error(error);
        alert("Error during scan. Check if backend is running.");
    } finally {
        setLoading(false);
    }
});

function setLoading(isLoading) {
    const btn = document.getElementById('scanBtn');
    const btnText = document.getElementById('btnText');
    const loader = document.getElementById('btnLoader');
    
    if (isLoading) {
        btn.disabled = true;
        btnText.innerText = "Analyzing Map...";
        loader.classList.remove('hidden');
    } else {
        btn.disabled = false;
        btnText.innerText = "Run AI Safety Classifier";
        loader.classList.add('hidden');
    }
}

function handleBuildingClick(e) {
    const coords = e.features[0].geometry.coordinates.slice();
    const props = e.features[0].properties;

    const html = `
        <div class="popup-title">${props.type === 'yes' ? 'Building' : props.type}</div>
        <div class="popup-row"><span class="popup-label">Overall Risk:</span> <span class="popup-val" style="color:${props.color}; font-weight:bold;">${props.risk_level}</span></div>
        <hr style="margin: 8px 0; border: 0; border-top: 1px solid #eee;">
        <div class="popup-row"><span class="popup-label">🌊 Flood:</span> <span class="popup-val">${props.flood_risk} (${props.predicted_flood_depth}m)</span></div>
        <div class="popup-row"><span class="popup-label">🌀 Cyclone:</span> <span class="popup-val">${props.cyclone_risk} (${props.max_wind_kmh}km/h)</span></div>
        <div class="popup-row"><span class="popup-label">⛰️ Landslide:</span> <span class="popup-val">${props.landslide_risk} (${props.slope_degrees}°)</span></div>
        <hr style="margin: 8px 0; border: 0; border-top: 1px solid #eee;">
        <div class="popup-row"><span class="popup-label">Elevation:</span> <span class="popup-val">${props.elevation}m</span></div>
        <div class="popup-row"><span class="popup-label">Dist to Water:</span> <span class="popup-val">${props.distance_to_water}m</span></div>
    `;

    while (Math.abs(e.lngLat.lng - coords[0]) > 180) {
        coords[0] += e.lngLat.lng > coords[0] ? 360 : -360;
    }

    if (currentPopup) currentPopup.remove();
    currentPopup = new maplibregl.Popup()
        .setLngLat(coords)
        .setHTML(html)
        .addTo(map);
}

function updateDashboard(meta, isRegional = false) {
    document.getElementById('resultsPanel').classList.remove('hidden');
    
    document.getElementById('valRain').innerText = `${meta.rainfall_forecast_mm} mm`;
    document.getElementById('valWind').innerText = `${meta.max_wind_speed_kmh} km/h`;
    
    if (meta.river_discharge_m3s !== undefined) {
        document.getElementById('valRiver').innerText = `${meta.river_discharge_m3s} m³/s`;
    }

    // Only update counts for building scans, don't overwrite with N/A from regional sync
    if (!isRegional && meta.stats) {
        document.getElementById('valSafe').innerText = meta.stats.Safe;
        document.getElementById('valAtRisk').innerText = meta.stats['At-Risk'];
        document.getElementById('valDanger').innerText = meta.stats.Danger;
        document.getElementById('valDangerCount').innerText = meta.stats.Danger;
    }
}
