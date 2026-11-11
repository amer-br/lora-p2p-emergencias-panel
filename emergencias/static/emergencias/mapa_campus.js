// Mapa del campus con el mismo aspecto que el radar de la app
// (Aplicacion/lib/ui/radar_screen.dart): fondo verde claro, cuadrícula de
// referencia de ~110 m que se ve donde no hay teselas, contorno del campus y
// teselas de OSM servidas desde la caché del panel (funcionan sin internet).
window.MapaCampus = (function () {
  const PRIORIDADES = [
    ['rojo', 'Prioridad roja'], ['amarillo', 'Prioridad amarilla'], ['verde', 'Prioridad verde'],
  ];
  const RECURSOS = [
    ['PUNTO_REUNION', 'Punto de reunión'], ['REFUGIO', 'Refugio'],
    ['SALIDA_EVACUACION', 'Salida de evacuación'], ['BOTIQUIN', 'Botiquín'], ['DEA', 'DEA'],
  ];
  // Mismos íconos que iconoCategoria() e iconoRecurso() de lib/ui/tema.dart.
  const ICONO_CATEGORIA = { sos: 'sos', incendio: 'fuego', medica: 'medica', estructural: 'estructural' };

  function limitesDe(div) {
    const d = div.dataset;
    return L.latLngBounds([+d.sur, +d.oeste], [+d.norte, +d.este]);
  }

  function cuadricula(b) {
    const paso = 0.001;
    const capa = L.layerGroup();
    const linea = { color: '#a5d6a7', weight: 1, interactive: false };
    for (let k = Math.ceil(b.getSouth() / paso); k <= Math.floor(b.getNorth() / paso); k++) {
      L.polyline([[k * paso, b.getWest()], [k * paso, b.getEast()]], linea).addTo(capa);
    }
    for (let k = Math.ceil(b.getWest() / paso); k <= Math.floor(b.getEast() / paso); k++) {
      L.polyline([[b.getSouth(), k * paso], [b.getNorth(), k * paso]], linea).addTo(capa);
    }
    return capa;
  }

  function crear(div) {
    const limites = limitesDe(div);
    const mapa = L.map(div, {
      maxBounds: limites, maxBoundsViscosity: 1, maxZoom: 20, zoomSnap: 0.25, attributionControl: true,
    });
    mapa.fitBounds(limites);
    mapa.setMinZoom(mapa.getZoom()); // la vista completa queda siempre dentro del campus

    // La cuadrícula va debajo de las teselas: se ve donde no hay red ni caché.
    mapa.createPane('cuadricula').style.zIndex = 150;
    cuadricula(limites).eachLayer((l) => { l.options.pane = 'cuadricula'; l.addTo(mapa); });
    L.tileLayer(div.dataset.teselas, {
      maxZoom: 20, maxNativeZoom: 19, minNativeZoom: 15, bounds: limites,
      attribution: '&copy; colaboradores de OpenStreetMap',
    }).addTo(mapa);
    L.polygon([limites.getSouthWest(), limites.getNorthWest(), limites.getNorthEast(), limites.getSouthEast()],
      { color: '#1b5e20', weight: 2, fill: false, interactive: false }).addTo(mapa);
    return mapa;
  }

  function iconoIncidente(prioridad, clave, seleccionado) {
    const ico = ICONO_CATEGORIA[clave] || 'reporte';
    return L.divIcon({
      className: '', iconSize: [44, 44], iconAnchor: [22, 22], popupAnchor: [0, -20],
      html: `<div class="mk-incidente p-${prioridad}${seleccionado ? ' sel' : ''}"><span><i class="ico ico-${ico}"></i></span></div>`,
    });
  }

  function iconoRecurso(tipo) {
    return L.divIcon({
      className: '', iconSize: [32, 32], iconAnchor: [16, 16], popupAnchor: [0, -14],
      html: `<div class="mk-recurso r-${tipo}"><i class="ico ico-${tipo.toLowerCase()}"></i></div>`,
    });
  }

  function iconoNodo() {
    return L.divIcon({ className: '', iconSize: [16, 16], iconAnchor: [8, 8], html: '<div class="mk-nodo"></div>' });
  }

  function leyenda(mapa) {
    const control = L.control({ position: 'bottomleft' });
    control.onAdd = () => {
      const div = L.DomUtil.create('div', 'leyenda');
      div.innerHTML =
        PRIORIDADES.map(([p, t]) => `<div><span class="muestra p-${p}"></span>${t}</div>`).join('') +
        RECURSOS.map(([r, t]) => `<div><span class="muestra r-${r}"></span>${t}</div>`).join('') +
        '<div><span class="muestra nodo"></span>Última posición de un nodo</div>';
      L.DomEvent.disableClickPropagation(div);
      return div;
    };
    control.addTo(mapa);
  }

  return { crear, iconoIncidente, iconoRecurso, iconoNodo, leyenda, limitesDe };
})();
