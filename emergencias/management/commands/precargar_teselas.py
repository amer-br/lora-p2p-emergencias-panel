import time

from django.core.management.base import BaseCommand

from emergencias import teselas


def _teselas_del_campus():
    for z in range(teselas.ZOOM_MIN, teselas.ZOOM_MAX + 1):
        x0, x1, y0, y1 = teselas.rango(z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                yield z, x, y


class Command(BaseCommand):
    help = ('Descarga una sola vez las teselas del campus (zoom 15 a 19, ~300 teselas) '
            'para que el mapa funcione sin internet.')

    def handle(self, *args, **opciones):
        nuevas = existentes = 0
        for z, x, y in _teselas_del_campus():
            if teselas.en_cache(z, x, y):
                existentes += 1
                continue
            if teselas.descargar(z, x, y) is None:
                self.stderr.write('Sin conexión con OpenStreetMap; se detiene la precarga. '
                                  'Vuelve a ejecutarla con internet para completar lo que falta.')
                break
            nuevas += 1
            time.sleep(0.2)  # ritmo pausado, según la política de uso de OSM
        total = sum(1 for _ in _teselas_del_campus())
        self.stdout.write(f'Teselas nuevas: {nuevas} · ya en caché: {existentes} · total del campus: {total}')
