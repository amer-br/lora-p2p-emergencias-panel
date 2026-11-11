"""Pruebas del puente de radio sin hardware.

    python -m unittest test_puente_radio      (desde esta carpeta)
"""

import asyncio
import json
import threading
import unittest

import serial
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import puente_radio as pr

ALERTA = '0BADCAFE1234ABCD0512' + '00' * 34 + 'BEEF'  # 56 B; el puente no valida el CRC


class TraduccionTest(unittest.TestCase):
    def test_trama_de_la_pasarela(self):
        m = json.loads(pr.mensaje_para_panel('{"hex": "0bad", "rssi": -102, "snr": -3.5}\r\n'))
        self.assertEqual(m, {'hex': '0BAD', 'rssi': -102, 'snr': -3.5})

    def test_sin_metadatos_de_radio(self):
        self.assertEqual(json.loads(pr.mensaje_para_panel('{"hex":"AB"}')), {'hex': 'AB'})

    def test_metadatos_invalidos_se_omiten(self):
        m = json.loads(pr.mensaje_para_panel('{"hex":"AB","rssi":"fuerte","snr":true}'))
        self.assertEqual(m, {'hex': 'AB'})

    def test_lineas_que_no_son_tramas(self):
        for linea in ['', 'ets Jun  8 2016 00:22:57', 'rst:0x1 (POWERON_RESET)',
                      '{"hex": "ABC"}', '{"hex": "ZZ"}', '{"hex": ""}', '{"rssi": -90}',
                      '{roto', '[1, 2]', '{"hex": "' + 'AB' * 256 + '"}']:
            self.assertIsNone(pr.mensaje_para_panel(linea), linea)

    def test_mensaje_del_panel(self):
        self.assertEqual(pr.linea_para_pasarela('{"hex": "0bad"}'), b'{"hex": "0BAD"}\n')

    def test_mensaje_del_panel_ilegible(self):
        for m in ['no es json', '{"hex": 5}', '{"hex": "A"}', b'\xff\xfe', '{}']:
            self.assertIsNone(pr.linea_para_pasarela(m), m)


class SerieDesconectable:
    """Puerto serie falso: entrega líneas y simula que se desconecta el USB."""

    def __init__(self, lineas=()):
        self.name = 'FALSO'
        self.lineas = list(lineas)
        self.escrito = []
        self.desconectar = threading.Event()

    def readline(self):
        if self.desconectar.wait(0.05):
            raise serial.SerialException('dispositivo desconectado')
        return self.lineas.pop(0) if self.lineas else b''

    def write(self, datos):
        self.escrito.append(datos)

    def flush(self):
        pass

    def close(self):
        pass


class PuenteTest(unittest.IsolatedAsyncioTestCase):
    async def iniciar(self, abrir):
        self.puente = pr.Puente(abrir)
        self.servidor = await serve(self.puente.atender, '127.0.0.1', 0)
        self.url = f'ws://127.0.0.1:{self.servidor.sockets[0].getsockname()[1]}/ws'
        self.tarea = asyncio.create_task(self.puente.mantener_serie())

    async def asyncTearDown(self):
        self.tarea.cancel()
        self.servidor.close()
        await self.servidor.wait_closed()

    async def recibir(self, ws):
        return json.loads(await asyncio.wait_for(ws.recv(), 3))

    async def test_ida_y_vuelta_con_puerto_en_bucle(self):
        # loop:// devuelve lo que se escribe: la trama que manda el panel
        # regresa como si la pasarela la hubiera escuchado por radio.
        await self.iniciar(pr.abridor_serie('loop://', pr.BAUDIOS))
        await asyncio.wait_for(self.puente.listo.wait(), 3)
        async with connect(self.url) as panel, connect(self.url) as otro:
            await panel.send(json.dumps({'hex': ALERTA.lower()}))
            self.assertEqual(await self.recibir(panel), {'hex': ALERTA})
            self.assertEqual(await self.recibir(otro), {'hex': ALERTA})

    async def test_trama_de_la_pasarela_llega_al_panel(self):
        serie = SerieDesconectable([b'Arranque del firmware\r\n', b'{"hex":"AB",', b'"rssi":-90}\n'])
        await self.iniciar(lambda: serie)
        await asyncio.wait_for(self.puente.listo.wait(), 3)
        async with connect(self.url) as panel:
            self.assertEqual(await self.recibir(panel), {'hex': 'AB', 'rssi': -90})
            await panel.send('{"hex": "CD"}')
            await panel.send('ilegible')
            await panel.send('{"hex": "EF"}')
            for _ in range(50):
                if len(serie.escrito) == 2:
                    break
                await asyncio.sleep(0.02)
        self.assertEqual(serie.escrito, [b'{"hex": "CD"}\n', b'{"hex": "EF"}\n'])

    async def test_sin_pasarela_rechaza_la_conexion(self):
        def abrir():
            raise serial.SerialException('no existe COM99')

        await self.iniciar(abrir)
        async with connect(self.url) as panel:
            with self.assertRaises(ConnectionClosed) as ctx:
                await asyncio.wait_for(panel.recv(), 3)
        self.assertEqual(ctx.exception.rcvd.code, pr.CIERRE_SIN_PASARELA)

    async def test_desconectar_el_usb_cierra_al_panel_y_reconecta(self):
        series = [SerieDesconectable(), SerieDesconectable([b'{"hex":"AB"}\n'])]
        abiertas = []

        def abrir():
            abiertas.append(series[len(abiertas)])
            return abiertas[-1]

        await self.iniciar(abrir)
        await asyncio.wait_for(self.puente.listo.wait(), 3)
        async with connect(self.url) as panel:
            series[0].desconectar.set()
            with self.assertRaises(ConnectionClosed) as ctx:
                await asyncio.wait_for(panel.recv(), 3)
        self.assertEqual(ctx.exception.rcvd.code, pr.CIERRE_SIN_PASARELA)

        # El puente vuelve a abrir el puerto por sí solo.
        for _ in range(100):
            if self.puente.conectado and len(abiertas) == 2:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(self.puente.conectado)

    async def test_ruta_desconocida(self):
        await self.iniciar(pr.abridor_serie('loop://', pr.BAUDIOS))
        async with connect(self.url.replace('/ws', '/otra')) as ws:
            with self.assertRaises(ConnectionClosed) as ctx:
                await asyncio.wait_for(ws.recv(), 3)
        self.assertEqual(ctx.exception.rcvd.code, pr.CIERRE_RUTA)


if __name__ == '__main__':
    unittest.main()
