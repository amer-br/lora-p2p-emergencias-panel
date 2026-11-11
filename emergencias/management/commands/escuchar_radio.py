"""Servicio de radio: mantiene la conexión con la red LoRa.

Recibe tramas por WebSocket (simulador de radio o puente con el módem real),
las pasa al procesador y transmite las tramas salientes que el responsable
encola desde el panel. Si se pierde la conexión, reintenta y las tramas
pendientes se envían al reconectar.
"""

import asyncio
import json
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from emergencias import servicios
from emergencias.models import EstadoRadio, TramaSaliente

log = logging.getLogger('emergencias.radio')


@sync_to_async
def _marcar_estado(conectado: bool, error: str = ''):
    estado = EstadoRadio.actual()
    estado.conectado = conectado
    estado.url = settings.RADIO_URL
    estado.actualizado_en = timezone.now()
    estado.ultimo_error = error[:200]
    estado.save()


@sync_to_async
def _procesar(mensaje: str):
    m = json.loads(mensaje)
    servicios.procesar_trama(bytes.fromhex(m['hex']), m.get('rssi'), m.get('snr'))


@sync_to_async
def _pendientes():
    return list(TramaSaliente.objects.filter(estado=TramaSaliente.Estado.PENDIENTE).order_by('creada_en')[:20])


@sync_to_async
def _marcar_enviada(trama: TramaSaliente):
    trama.estado = TramaSaliente.Estado.ENVIADA
    trama.enviada_en = timezone.now()
    trama.save(update_fields=['estado', 'enviada_en'])


class Command(BaseCommand):
    help = 'Conecta el panel a la red LoRa (simulador o módem) y procesa las tramas.'

    def handle(self, *args, **opciones):
        asyncio.run(self._ejecutar())

    async def _ejecutar(self):
        espera = 1
        while True:
            try:
                async with connect(settings.RADIO_URL, open_timeout=5) as ws:
                    log.info('Conectado a la radio en %s', settings.RADIO_URL)
                    await _marcar_estado(True)
                    espera = 1
                    tareas = [asyncio.create_task(self._recibir(ws)), asyncio.create_task(self._transmitir(ws))]
                    hechas, pendientes = await asyncio.wait(tareas, return_when=asyncio.FIRST_COMPLETED)
                    for t in pendientes:
                        t.cancel()
                    for t in hechas:
                        t.result()  # propaga la excepción de la tarea que terminó
                log.warning('La radio cerró la conexión; reintento en %ss', espera)
                await _marcar_estado(False, 'Conexión cerrada por la radio')
            except (OSError, WebSocketException, asyncio.TimeoutError) as e:
                log.warning('Sin conexión con la radio (%s); reintento en %ss', e, espera)
                await _marcar_estado(False, str(e) or type(e).__name__)
            await asyncio.sleep(espera)
            espera = min(espera * 2, 30)

    async def _recibir(self, ws):
        async for mensaje in ws:
            try:
                await _procesar(mensaje)
            except (ValueError, KeyError) as e:
                log.warning('Mensaje de radio ilegible: %s', e)

    async def _transmitir(self, ws):
        while True:
            for trama in await _pendientes():
                await ws.send(json.dumps({'hex': trama.bytes_hex}))
                await _marcar_enviada(trama)
                log.info('Transmitida: %s', trama.descripcion)
            await _marcar_estado(True)
            await asyncio.sleep(1)
