// Switzerland cutout shared by /map and the venue page's mini map.
// The border GeoJSON's first ring is the country outline, the rest are
// foreign enclaves (Büsingen, Campione). One even-odd polygon — world
// rectangle, minus Switzerland, plus the enclaves — painted in paper hides
// every tile outside the border; tiles only load once the mask is in place.
// Styling hooks (.ch-mask, .ch-border) live in _partials/ch_map_styles.html.
window.addSwissCutout = function (map, geojsonUrl, tileOptions) {
    return fetch(geojsonUrl)
        .then((r) => r.json())
        .then((geo) => {
            const rings = geo.coordinates.map(
                (ring) => ring.map(([lon, lat]) => [lat, lon])
            );
            const world = [[-89, -180], [-89, 180], [89, 180], [89, -180]];
            L.polygon([world, ...rings], {
                stroke: false,
                fillOpacity: 1,
                className: 'ch-mask',
                interactive: false
            }).addTo(map);
            L.polygon(rings, {
                fill: false,
                weight: 2,
                className: 'ch-border',
                interactive: false
            }).addTo(map);

            L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', Object.assign({
                maxZoom: 19,
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            }, tileOptions || {})).addTo(map);
        });
};
