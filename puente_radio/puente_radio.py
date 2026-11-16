"""Puente de radio: conecta la pasarela T-Beam (USB serie) con el panel (WebSocket).

Corre en la computadora del puesto de mando, fuera de Docker, porque Docker
Desktop para Windows no puede pasar el puerto USB a un contenedor (SDS 3.3).

    Pasarela T-Beam --USB serie, JSON Lines--> puente --WebSocket /ws--> servicio de radio del panel

Formato (el mismo que usa el simulador de radio, SDS 7.8):
  * Pasarela -> panel: una línea por trama recibida
        {"hex": "0BADCAFE...", "rssi": -102, "snr": -3.0}
  * Panel -> pasarela: una línea por trama que se debe transmitir
        {"hex": "..."}
  * Puente -> pasarela: latido cada 10 s mientras haya un panel conectado,
    para que la pantalla del T-Beam muestre «Panel conectado»
        {"ping": 1}

Las líneas del puerto serie que no son tramas (mensajes de arranque o de
depuración del firmware) se ignoran. Si se desconecta el USB, el puente cierra
las conexiones WebSocket, para que el panel marque la radio como «sin
conexión», y reintenta abrir el puerto con espera creciente de 1 a 30 s.

Uso:
    python puente_radio.py --listar                  # ver los puertos serie
    python puente_radio.py                           # detecta el T-Beam si solo hay uno
    python puente_radio.py --puerto COM5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Callable

import serial
from serial.tools import list_ports
from websockets.asyncio.server import ServerConnection, broadcast, serve
from websockets.exceptions import ConnectionClosed

log = logging.getLogger('puente_radio')

BAUDIOS = 115_200
PUERTO_WS = 8081
RUTA_WS = '/ws'

#: Carga útil máxima de un paquete LoRa (SX126x/SX127x).
MAX_BYTES_TRAMA = 255

#: Una línea más larga que esto sin salto de línea se descarta (ruido o basura).
MAX_LARGO_LINEA = 4096

#: Códigos de cierre de WebSocket (RFC 6455).
CIERRE_RUTA = 1008        # política: ruta desconocida
CIERRE_SIN_PASARELA = 1013  # inténtalo más tarde

ESPERA_MAX = 30

#: Latido hacia la pasarela mientras haya un panel conectado. El firmware
#: muestra «Panel sin conexión» si pasa más de 25 s sin recibirlo.
INTERVALO_LATIDO = 10
LATIDO = b'{"ping": 1}\n'


# ---------------------------------------------------------------------------
# Traducción de mensajes
# ---------------------------------------------------------------------------

def _hex_valido(valor) -> bool:
    if not isinstance(valor, str) or not valor or len(valor) % 2:
        return False
    if len(valor) // 2 > MAX_BYTES_TRAMA:
        return False
    try:
        bytes.fromhex(valor)
    except ValueError:
        return False
    return True


def _numero(valor) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def mensaje_para_panel(linea: str) -> str | None:
    """Línea del firmware -> mensaje JSON para el panel; None si no es una trama."""
    linea = linea.strip()
    if not linea.startswith('{'):
        return None
    try:
        m = json.loads(linea)
    except json.JSONDecodeError:
        return None
    if not isinstance(m, dict) or not _hex_valido(m.get('hex')):
        return None
    salida = {'hex': m['hex'].upper()}
    for campo in ('rssi', 'snr'):
        if _numero(m.get(campo)):
            salida[campo] = m[campo]
    return json.dumps(salida)


def linea_para_pasarela(mensaje: str | bytes) -> bytes | None:
    """Mensaje del panel -> línea para el firmware; None si es ilegible."""
    try:
        m = json.loads(mensaje)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(m, dict) or not _hex_valido(m.get('hex')):
        return None
    return (json.dumps({'hex': m['hex'].upper()}) + '\n').encode('ascii')


# ---------------------------------------------------------------------------
# Puente
# ---------------------------------------------------------------------------

class Puente:
    """Mantiene el puerto serie abierto y lo comparte con los clientes WebSocket."""

    def __init__(self, abrir_serie: Callable[[], serial.SerialBase],
                 intervalo_latido: float = INTERVALO_LATIDO):
        self._abrir_serie = abrir_serie
        self._intervalo_latido = intervalo_latido
        self._serie: serial.SerialBase | None = None
        self._clientes: set[ServerConnection] = set()
        self._escritura = asyncio.Lock()
        self.listo = asyncio.Event()  # se activa con el puerto abierto (para pruebas)

    @property
    def conectado(self) -> bool:
        return self._serie is not None

    # --- Lado serie ---------------------------------------------------------

    async def mantener_serie(self) -> None:
        espera = 1
        while True:
            try:
                serie = await asyncio.to_thread(self._abrir_serie)
            except (serial.SerialException, OSError) as e:
                log.warning('No se pudo abrir la pasarela (%s); reintento en %s s', e, espera)
                await asyncio.sleep(espera)
                espera = min(espera * 2, ESPERA_MAX)
                continue

            espera = 1
            self._serie = serie
            self.listo.set()
            log.info('Pasarela conectada en %s', serie.name or serie.port)
            try:
                await self._leer(serie)
            except (serial.SerialException, OSError) as e:
                log.warning('Se perdió la pasarela: %s', e)
            finally:
                self._serie = None
                self.listo.clear()
                await asyncio.to_thread(serie.close)
                await self._cerrar_clientes('Pasarela desconectada')
            await asyncio.sleep(espera)

    async def _leer(self, serie: serial.SerialBase) -> None:
        pendiente = b''
        while True:
            # readline() vuelve al vencer el timeout del puerto (1 s) aunque la
            # línea esté incompleta; por eso se acumula hasta el salto de línea.
            fragmento = await asyncio.to_thread(serie.readline)
            if not fragmento:
                continue
            pendiente += fragmento
            if not pendiente.endswith(b'\n'):
                if len(pendiente) > MAX_LARGO_LINEA:
                    log.warning('Línea serie demasiado larga; se descarta')
                    pendiente = b''
                continue
            linea, pendiente = pendiente.decode('utf-8', errors='replace'), b''
            mensaje = mensaje_para_panel(linea)
            if mensaje is None:
                if linea.strip():
                    log.debug('Pasarela: %s', linea.strip())
                continue
            log.info('Recibida  %s', mensaje)
            broadcast(self._clientes, mensaje)

    async def _escribir(self, linea: bytes) -> bool:
        serie = self._serie
        if serie is None:
            return False
        try:
            async with self._escritura:
                await asyncio.to_thread(serie.write, linea)
                await asyncio.to_thread(serie.flush)
        except (serial.SerialException, OSError) as e:
            log.warning('No se pudo escribir en la pasarela: %s', e)
            return False
        return True

    async def latir(self) -> None:
        """Avisa a la pasarela, cada cierto tiempo, que hay un panel conectado."""
        while True:
            if self._clientes and self.conectado:
                await self._escribir(LATIDO)
            await asyncio.sleep(self._intervalo_latido)

    # --- Lado WebSocket -----------------------------------------------------

    async def atender(self, ws: ServerConnection) -> None:
        if ws.request.path != RUTA_WS:
            await ws.close(CIERRE_RUTA, f'Usa {RUTA_WS}')
            return
        if not self.conectado:
            await ws.close(CIERRE_SIN_PASARELA, 'Pasarela sin conexión')
            return

        self._clientes.add(ws)
        log.info('Cliente conectado: %s', ws.remote_address)
        try:
            async for mensaje in ws:
                linea = linea_para_pasarela(mensaje)
                if linea is None:
                    log.warning('Mensaje ilegible del cliente; se descarta')
                    continue
                if not await self._escribir(linea):
                    # Cerrar hace que el panel conserve sus tramas como
                    # pendientes y las reenvíe al reconectar.
                    await ws.close(CIERRE_SIN_PASARELA, 'Pasarela sin conexión')
                    break
                log.info('Enviada   %s', linea.decode().strip())
        except ConnectionClosed:
            pass
        finally:
            self._clientes.discard(ws)
            log.info('Cliente desconectado: %s', ws.remote_address)

    async def _cerrar_clientes(self, motivo: str) -> None:
        clientes = list(self._clientes)
        self._clientes.clear()
        await asyncio.gather(
            *(ws.close(CIERRE_SIN_PASARELA, motivo) for ws in clientes),
            return_exceptions=True,
        )


# ---------------------------------------------------------------------------
# Puerto serie
# ---------------------------------------------------------------------------

def abridor_serie(puerto: str, baudios: int) -> Callable[[], serial.SerialBase]:
    """Devuelve una función que abre el puerto sin reiniciar el ESP32."""

    def abrir() -> serial.SerialBase:
        # Acepta nombres (COM5, /dev/ttyACM0) y URL de pyserial (loop://, rfc2217://).
        serie = serial.serial_for_url(puerto, baudrate=baudios, timeout=1, do_not_open=True)
        # En el T-Beam, DTR y RTS controlan el reinicio y el modo de arranque del
        # ESP32; si se activan al abrir el puerto, la placa se reinicia.
        serie.dtr = False
        serie.rts = False
        serie.open()
        serie.reset_input_buffer()
        return serie

    return abrir


def puertos_usb() -> list:
    return [p for p in list_ports.comports() if p.vid is not None]


def detectar_puerto() -> str:
    candidatos = puertos_usb()
    if len(candidatos) == 1:
        return candidatos[0].device
    if not candidatos:
        sys.exit('No se encontró ningún puerto serie USB. Conecta la pasarela o indica --puerto.')
    lista = '\n'.join(f'  {p.device}: {p.description}' for p in candidatos)
    sys.exit(f'Hay varios puertos serie USB; indica cuál con --puerto:\n{lista}')


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

async def ejecutar(puerto: str, baudios: int, host: str, puerto_ws: int) -> None:
    puente = Puente(abridor_serie(puerto, baudios))
    async with serve(puente.atender, host, puerto_ws):
        log.info('Puente de radio en ws://%s:%s%s  (pasarela: %s a %s baudios)',
                 host, puerto_ws, RUTA_WS, puerto, baudios)
        await asyncio.gather(puente.mantener_serie(), puente.latir())


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description='Puente entre la pasarela T-Beam (USB) y el panel (WebSocket).')
    p.add_argument('--puerto', help='Puerto serie de la pasarela, p. ej. COM5. Si se omite, se detecta.')
    p.add_argument('--baudios', type=int, default=BAUDIOS, help=f'Velocidad del puerto serie (por omisión {BAUDIOS}).')
    p.add_argument('--host', default='0.0.0.0',
                   help='Interfaz del servidor WebSocket (por omisión 0.0.0.0, para que Docker lo alcance).')
    p.add_argument('--puerto-ws', type=int, default=PUERTO_WS, help=f'Puerto del WebSocket (por omisión {PUERTO_WS}).')
    p.add_argument('--listar', action='store_true', help='Muestra los puertos serie disponibles y termina.')
    p.add_argument('-v', '--detalle', action='store_true', help='Muestra también los mensajes de depuración del firmware.')
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.detalle else logging.INFO,
        format='%(asctime)s %(levelname)-7s %(message)s', datefmt='%H:%M:%S',
    )
    logging.getLogger('websockets').setLevel(logging.WARNING)

    if args.listar:
        puertos = list_ports.comports()
        if not puertos:
            print('No hay puertos serie.')
        for q in puertos:
            usb = 'USB' if q.vid is not None else '   '
            print(f'{q.device:<12} {usb}  {q.description}')
        return

    puerto = args.puerto or detectar_puerto()
    try:
        asyncio.run(ejecutar(puerto, args.baudios, args.host, args.puerto_ws))
    except KeyboardInterrupt:
        log.info('Puente detenido')
    except OSError as e:
        sys.exit(f'No se pudo abrir el WebSocket en {args.host}:{args.puerto_ws}: {e}')


if __name__ == '__main__':
    main()
