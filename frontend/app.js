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
    // Add 3D buildings layer for aesthetic
    map.addLayer({
        'id': '3d-buildings',
        'source': 'carto', // This might not have 3D data by default in free carto, 
        // but we'll add our own GeoJSON source on scan
        'type': 'fill-extrusion',
        'paint': {}
    }, 'waterway');
});

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
        
        displayDataOnMap(data);
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

function displayDataOnMap(geojsonData) {
    // Remove existing if present
    if (map.getSource('buildings-risk')) {
        map.removeLayer('buildings-circles');
        map.removeSource('buildings-risk');
    }

    if (currentPopup) currentPopup.remove();

    map.addSource('buildings-risk', {
        type: 'geojson',
        data: geojsonData
    });

    map.addLayer({
        id: 'buildings-circles',
        type: 'circle',
        source: 'buildings-risk',
        paint: {
            'circle-color': ['get', 'color'],
            'circle-radius': [
                'interpolate', ['linear'], ['zoom'],
                10, 2,
                15, 6,
                18, 12
            ],
            'circle-stroke-width': 1,
            'circle-stroke-color': '#ffffff',
            'circle-opacity': 0.8
        }
    });

    // Add interactivity
    map.on('mouseenter', 'buildings-circles', () => {
        map.getCanvas().style.cursor = 'pointer';
    });
    
    map.on('mouseleave', 'buildings-circles', () => {
        map.getCanvas().style.cursor = '';
    });

    map.on('click', 'buildings-circles', (e) => {
        const coords = e.features[0].geometry.coordinates.slice();
        const props = e.features[0].properties;

        const html = `
            <div class="popup-title">${props.type === 'yes' ? 'Building' : props.type}</div>
            <div class="popup-row"><span class="popup-label">Risk Level:</span> <span class="popup-val" style="color:${props.color}">${props.risk_level}</span></div>
            <div class="popup-row"><span class="popup-label">Predicted Dep:</span> <span class="popup-val">${props.predicted_flood_depth}m</span></div>
            <div class="popup-row"><span class="popup-label">Elevation:</span> <span class="popup-val">${props.elevation}m</span></div>
            <div class="popup-row"><span class="popup-label">Dist to Water:</span> <span class="popup-val">${props.distance_to_water}m</span></div>
        `;

        // Ensure that if the map is zoomed out such that multiple
        // copies of the feature are visible, the popup appears over the copy being pointed to.
        while (Math.abs(e.lngLat.lng - coords[0]) > 180) {
            coords[0] += e.lngLat.lng > coords[0] ? 360 : -360;
        }

        if (currentPopup) currentPopup.remove();
        currentPopup = new maplibregl.Popup()
            .setLngLat(coords)
            .setHTML(html)
            .addTo(map);
    });
}

function updateDashboard(meta) {
    document.getElementById('resultsPanel').classList.remove('hidden');
    
    document.getElementById('valRain').innerText = `${meta.rainfall_forecast_mm} mm`;
    document.getElementById('valSafe').innerText = meta.stats["Safe"];
    document.getElementById('valAtRisk').innerText = meta.stats["At-Risk"];
    document.getElementById('valDanger').innerText = meta.stats["Danger"];
    document.getElementById('valDangerCount').innerText = meta.stats["Danger"];
}
