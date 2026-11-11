"""Teselas del mapa con caché en disco, para que el mapa funcione sin internet.

Es el equivalente de la caché de flutter_map en la app: la primera vez que se
ve una tesela se descarga de OpenStreetMap y se guarda; después se sirve del
disco. Solo se aceptan teselas que cubren el campus, así que el panel no
puede usarse como proxy general de OSM.
"""

from __future__ import annotations

import logging
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings

log = logging.getLogger(__name__)

ZOOM_MIN = 15
ZOOM_MAX = 19  # mismo maxNativeZoom que la app
USER_AGENT = 'PanelCampusLoRa/1.0 (tesis UAZ; panel de emergencias del campus)'

# Tras un fallo de red no se reintenta durante un rato; así, sin internet, cada
# petición responde al instante en lugar de esperar el timeout.
_PAUSA_SIN_RED_S = 60
_sin_red_hasta = 0.0


def _a_tesela(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def rango(z: int) -> tuple[int, int, int, int]:
    """(x_min, x_max, y_min, y_max) de las teselas que cubren el campus."""
    l = settings.LIMITES_CAMPUS
    x0, y0 = _a_tesela(l['norte'], l['oeste'], z)
    x1, y1 = _a_tesela(l['sur'], l['este'], z)
    return x0, x1, y0, y1


def dentro_del_campus(z: int, x: int, y: int) -> bool:
    if not ZOOM_MIN <= z <= ZOOM_MAX:
        return False
    x0, x1, y0, y1 = rango(z)
    return x0 <= x <= x1 and y0 <= y <= y1


def _ruta(z: int, x: int, y: int) -> Path:
    return Path(settings.TESELAS_DIR) / str(z) / str(x) / f'{y}.png'


def en_cache(z: int, x: int, y: int) -> bool:
    return _ruta(z, x, y).is_file()


def descargar(z: int, x: int, y: int) -> bytes | None:
    global _sin_red_hasta
    if time.monotonic() < _sin_red_hasta:
        return None
    url = settings.TESELAS_ORIGEN.format(z=z, x=x, y=y)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=3) as resp:
            datos = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.warning('No se pudo descargar la tesela %s/%s/%s: %s', z, x, y, e)
        _sin_red_hasta = time.monotonic() + _PAUSA_SIN_RED_S
        return None
    ruta = _ruta(z, x, y)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(f'.{os.getpid()}.tmp')
    temporal.write_bytes(datos)
    temporal.replace(ruta)  # escritura atómica: nunca se sirve una tesela a medias
    return datos


def obtener(z: int, x: int, y: int) -> bytes | None:
    """Bytes PNG de la tesela, o None si está fuera del campus o no hay red ni caché."""
    if not dentro_del_campus(z, x, y):
        return None
    ruta = _ruta(z, x, y)
    if ruta.is_file():
        return ruta.read_bytes()
    return descargar(z, x, y)
