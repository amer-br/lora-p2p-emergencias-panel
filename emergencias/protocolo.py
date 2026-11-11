"""Manejo de protocolo: tramas binarias big-endian de la red LoRa.

Es la contraparte en Python de Aplicacion/lib/protocol/tramas.dart; ambos
lados deben producir exactamente los mismos bytes.

Alerta / SOS (56 B):
  Msg_ID 4 | Sender_ID 4 | TTL 1 | Type 1 | Lat 4 | Lon 4 | Unix 4 | Texto 32 | CRC 2
Acuse (20 B, SRS 3.1.4):
  Msg_ID 4 | Sender_ID 4 | TTL 1 | Type 1 | Ref_Msg_ID 4 | Unix 4 | CRC 2
Safety Check (17 B):
  Msg_ID 4 | Sender_ID 4 | TTL 1 | Type 1 | Estado 1 | Unix 4 | CRC 2

Lat/Lon van como grados × 10⁷ en int32; 0x7FFFFFFF en ambos significa "sin
posición". El CRC-16/CCITT-FALSE se calcula con el byte TTL en 0 para que los
nodos intermedios puedan decrementarlo sin recalcularlo.
"""

from __future__ import annotations

import math
import secrets
import struct
from dataclasses import dataclass

TTL_MAX = 5
MAX_BYTES_TEXTO = 32

TIPO_SOS = 0x01
ACK_EN_CAMINO = 0x02
ACK_LLEGUE = 0x03
ACK_RESUELTO = 0x04
SAFETY_CHECK = 0x05
TIPOS_ACUSE = (ACK_EN_CAMINO, ACK_LLEGUE, ACK_RESUELTO)

A_SALVO = 0x01

LONGITUD_ALERTA = 56
LONGITUD_ACUSE = 20
LONGITUD_SAFETY = 17

_CENTINELA_GPS = 0x7FFFFFFF
_OFFSET_TTL = 8
_OFFSET_TYPE = 9


@dataclass(frozen=True)
class Categoria:
    tipo: int
    clave: str
    nombre: str
    prioridad: str  # 'rojo' | 'amarillo' | 'verde'


# Catálogo de categorías (SRS S10, lib/domain/categoria.dart).
CATEGORIAS = {
    c.tipo: c
    for c in [
        Categoria(TIPO_SOS, 'sos', 'SOS', 'rojo'),
        Categoria(0x11, 'incendio', 'Incendio', 'rojo'),
        Categoria(0x12, 'medica', 'Médica', 'amarillo'),
        Categoria(0x13, 'estructural', 'Estructural', 'verde'),
    ]
}


def crc16(datos: bytes) -> int:
    """CRC-16/CCITT-FALSE (polinomio 0x1021, valor inicial 0xFFFF)."""
    crc = 0xFFFF
    for byte in datos:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def _e7(grados: float) -> int:
    """Grados × 10⁷ redondeados lejos de cero, igual que double.round() de Dart."""
    return int(math.copysign(math.floor(abs(grados) * 1e7 + 0.5), grados))


def generar_id32() -> int:
    """Identificador de 32 bits distinto de cero."""
    return secrets.randbelow(0xFFFFFFFF) + 1


@dataclass(frozen=True)
class TramaAlerta:
    msg_id: int
    sender_id: int
    tipo: int
    unix_time: int
    lat: float | None = None
    lon: float | None = None
    texto: str = ''
    ttl: int = TTL_MAX

    @property
    def es_sos(self) -> bool:
        return self.tipo == TIPO_SOS


@dataclass(frozen=True)
class TramaAcuse:
    msg_id: int
    sender_id: int
    tipo: int
    ref_msg_id: int
    unix_time: int
    ttl: int = TTL_MAX


@dataclass(frozen=True)
class TramaSafetyCheck:
    msg_id: int
    sender_id: int
    estado: int
    unix_time: int
    ttl: int = TTL_MAX


Trama = TramaAlerta | TramaAcuse | TramaSafetyCheck


def _con_crc(cuerpo: bytearray) -> bytes:
    sin_ttl = bytearray(cuerpo)
    sin_ttl[_OFFSET_TTL] = 0
    return bytes(cuerpo) + struct.pack('>H', crc16(sin_ttl))


def _crc_valido(b: bytes) -> bool:
    sin_ttl = bytearray(b[:-2])
    sin_ttl[_OFFSET_TTL] = 0
    return struct.unpack('>H', b[-2:])[0] == crc16(sin_ttl)


def codificar(trama: Trama) -> bytes:
    if isinstance(trama, TramaAlerta):
        texto = b'' if trama.es_sos else trama.texto.encode('utf-8')
        if len(texto) > MAX_BYTES_TEXTO:
            raise ValueError(f'El texto excede {MAX_BYTES_TEXTO} bytes UTF-8')
        con_gps = trama.lat is not None and trama.lon is not None
        cuerpo = bytearray(struct.pack(
            '>IIBBiiI',
            trama.msg_id, trama.sender_id, trama.ttl, trama.tipo,
            _e7(trama.lat) if con_gps else _CENTINELA_GPS,
            _e7(trama.lon) if con_gps else _CENTINELA_GPS,
            trama.unix_time,
        ))
        cuerpo += texto.ljust(MAX_BYTES_TEXTO, b'\x00')
        return _con_crc(cuerpo)
    if isinstance(trama, TramaAcuse):
        return _con_crc(bytearray(struct.pack(
            '>IIBBII', trama.msg_id, trama.sender_id, trama.ttl, trama.tipo,
            trama.ref_msg_id, trama.unix_time)))
    return _con_crc(bytearray(struct.pack(
        '>IIBBBI', trama.msg_id, trama.sender_id, trama.ttl, SAFETY_CHECK,
        trama.estado, trama.unix_time)))


def decodificar(b: bytes) -> Trama | None:
    """Devuelve None si la trama tiene longitud o CRC inválidos o un Type
    desconocido (se ignora por compatibilidad hacia adelante)."""
    if len(b) <= _OFFSET_TYPE:
        return None
    tipo = b[_OFFSET_TYPE]
    if tipo == SAFETY_CHECK:
        if len(b) != LONGITUD_SAFETY or not _crc_valido(b):
            return None
        msg_id, sender_id, ttl, _, estado, unix = struct.unpack('>IIBBBI', b[:15])
        return TramaSafetyCheck(msg_id, sender_id, estado, unix, ttl)
    if tipo in TIPOS_ACUSE:
        if len(b) != LONGITUD_ACUSE or not _crc_valido(b):
            return None
        msg_id, sender_id, ttl, _, ref, unix = struct.unpack('>IIBBII', b[:18])
        return TramaAcuse(msg_id, sender_id, tipo, ref, unix, ttl)
    if tipo in CATEGORIAS:
        if len(b) != LONGITUD_ALERTA or not _crc_valido(b):
            return None
        msg_id, sender_id, ttl, _, lat_e7, lon_e7, unix = struct.unpack('>IIBBiiI', b[:22])
        sin_gps = lat_e7 == _CENTINELA_GPS and lon_e7 == _CENTINELA_GPS
        texto = b[22:22 + MAX_BYTES_TEXTO].rstrip(b'\x00').decode('utf-8', errors='replace')
        return TramaAlerta(
            msg_id, sender_id, tipo, unix,
            None if sin_gps else lat_e7 / 1e7,
            None if sin_gps else lon_e7 / 1e7,
            texto, ttl,
        )
    return None
