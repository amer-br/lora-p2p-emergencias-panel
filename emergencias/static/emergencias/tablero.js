// Tablero: mapa del campus con incidentes, recursos y nodos; se actualiza solo.
(function () {
  const INTERVALO_MS = 5000;

  const divMapa = document.getElementById('mapa');
  const lateral = document.getElementById('lateral');
  const mapa = MapaCampus.crear(divMapa);
  MapaCampus.leyenda(mapa);

  const capas = {
    'Incidentes activos': L.layerGroup().addTo(mapa),
    'Recursos': L.layerGroup().addTo(mapa),
    'Última posición de nodos': L.layerGroup().addTo(mapa),
  };
  L.control.layers(null, capas, { position: 'topright' }).addTo(mapa);

  const escapar = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function pintar(datos) {
    Object.values(capas).forEach((c) => c.clearLayers());
    datos.recursos.forEach((r) => {
      L.marker([r.lat, r.lon], { icon: MapaCampus.iconoRecurso(r.tipo), zIndexOffset: 0 })
        .bindPopup(`<b>${escapar(r.nombre)}</b><br>${escapar(r.tipo_nombre)}<br>${escapar(r.descripcion)}`)
        .addTo(capas['Recursos']);
    });
    datos.nodos.forEach((n) => {
      L.marker([n.lat, n.lon], { icon: MapaCampus.iconoNodo(), zIndexOffset: -100 })
        .bindPopup(`Nodo <b>${escapar(n.nombre)}</b><br>Posición del ${new Date(n.hora).toLocaleString()}`)
        .addTo(capas['Última posición de nodos']);
    });
    datos.incidentes.forEach((i) => {
      L.marker([i.lat, i.lon], { icon: MapaCampus.iconoIncidente(i.prioridad, i.clave), zIndexOffset: 1000 })
        .bindPopup(
          `<b>${escapar(i.categoria)}</b> · ${escapar(i.estado)}${i.propio ? ' · aviso del panel' : ''}` +
          `<br>${escapar(i.texto || '(sin texto)')}<br><a href="${escapar(i.url)}">Abrir incidente</a>`,
        ).addTo(capas['Incidentes activos']);
    });
  }

  let ultimoMapa = '';

  async function actualizar() {
    try {
      const [mapaResp, lateralResp] = await Promise.all([
        fetch(divMapa.dataset.url, { credentials: 'same-origin' }),
        fetch(lateral.dataset.url, { credentials: 'same-origin' }),
      ]);
      if (mapaResp.ok) {
        // Redibujar solo si cambió, para no cerrar la ventana emergente abierta.
        const texto = await mapaResp.text();
        if (texto !== ultimoMapa) {
          ultimoMapa = texto;
          pintar(JSON.parse(texto));
        }
      }
      if (lateralResp.ok) lateral.innerHTML = await lateralResp.text();
    } catch (e) {
      console.warn('No se pudo actualizar el tablero', e);
    }
  }

  actualizar();
  setInterval(actualizar, INTERVALO_MS);
})();
