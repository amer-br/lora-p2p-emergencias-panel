import json
from pathlib import Path

from django.core.management.base import BaseCommand

from emergencias.models import Recurso

RUTA = Path(__file__).resolve().parents[2] / 'fixtures' / 'recursos.json'


class Command(BaseCommand):
    help = 'Carga los recursos del mapa (copia de Aplicacion/assets/data/recursos.json) si no existen.'

    def handle(self, *args, **opciones):
        nuevos = 0
        for r in json.loads(RUTA.read_text(encoding='utf-8')):
            _, creado = Recurso.objects.get_or_create(nombre=r['nombre'], defaults=dict(
                tipo=r['tipo'], descripcion=r.get('descripcion', ''), lat=r['lat'], lon=r['lon'],
            ))
            nuevos += creado
        self.stdout.write(f'Recursos nuevos: {nuevos}')
