"""Procesador de tramas entrantes y acciones del responsable del campus.

Reglas de la SRS: deduplicación por Msg_ID, estado consolidado del
incidente a partir de sus acuses (tabla de estados de F6–F8), regla de quién
puede marcar un incidente como resuelto (CU-09), avisos del puesto de mando
(CU-18), censo de evacuación por Sender_ID distinto (CU-19) y reporte del
simulacro (CU-20).
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import protocolo
from .models import (
    Acuse, Censo, Incidente, Nodo, Origen, ReporteSafety, TramaRecibida,
    TramaSaliente, hex8,
)

log = logging.getLogger(__name__)


def _desde_unix(unix: int) -> datetime:
    return datetime.fromtimestamp(unix, tz=dt_timezone.utc)


def _saltos(ttl: int) -> int:
    return max(protocolo.TTL_MAX - ttl, 0)


# --------------------------------------------------------------------------
# Tramas entrantes
# --------------------------------------------------------------------------

@transaction.atomic
def procesar_trama(bytes_: bytes, rssi: int | None = None, snr: float | None = None,
                   ahora: datetime | None = None) -> protocolo.Trama | None:
    """Valida, registra y consolida una trama escuchada por radio."""
    ahora = ahora or timezone.now()
    trama = protocolo.decodificar(bytes_)
    TramaRecibida.objects.create(
        recibida_en=ahora, bytes_hex=bytes_.hex().upper(), rssi=rssi, snr=snr,
        valida=trama is not None, resumen=_resumir(trama, len(bytes_)),
    )
    if trama is None:
        return None

    _registrar_nodo(trama, rssi, snr, ahora)
    if isinstance(trama, protocolo.TramaAlerta):
        _guardar_incidente(trama, ahora, Origen.RECIBIDO, rssi, snr)
    elif isinstance(trama, protocolo.TramaAcuse):
        _guardar_acuse(trama, ahora, Origen.RECIBIDO)
    else:
        ReporteSafety.objects.get_or_create(msg_id=trama.msg_id, defaults=dict(
            sender_id=trama.sender_id, estado=trama.estado,
            hora_emisor=_desde_unix(trama.unix_time), recibido_en=ahora,
        ))
    return trama


def _resumir(trama, longitud: int) -> str:
    match trama:
        case None:
            return f'Trama inválida ({longitud} B): longitud, CRC o Type desconocido'
        case protocolo.TramaAlerta():
            cat = protocolo.CATEGORIAS[trama.tipo].nombre
            return f'{cat} de {hex8(trama.sender_id)}: {trama.texto or "(sin texto)"}'[:160]
        case protocolo.TramaAcuse():
            nombre = Acuse.Tipo(trama.tipo).label
            return f'Acuse "{nombre}" de {hex8(trama.sender_id)} para {hex8(trama.ref_msg_id)}'
        case protocolo.TramaSafetyCheck():
            return f'Estoy a salvo: {hex8(trama.sender_id)}'


def _registrar_nodo(trama, rssi, snr, ahora):
    nodo, _ = Nodo.objects.select_for_update().get_or_create(
        sender_id=trama.sender_id, defaults={'visto_por_ultima_vez': ahora})
    nodo.visto_por_ultima_vez = ahora
    nodo.ultimo_rssi = rssi
    nodo.ultimo_snr = snr
    nodo.tramas_recibidas += 1
    if isinstance(trama, protocolo.TramaAlerta) and trama.lat is not None:
        nodo.ultima_lat, nodo.ultima_lon, nodo.ultima_posicion_en = trama.lat, trama.lon, ahora
    nodo.save()


def _guardar_incidente(t: protocolo.TramaAlerta, ahora, origen, rssi=None, snr=None):
    incidente, nuevo = Incidente.objects.get_or_create(msg_id=t.msg_id, defaults=dict(
        sender_id=t.sender_id, tipo=t.tipo, lat=t.lat, lon=t.lon, texto=t.texto,
        hora_emisor=_desde_unix(t.unix_time), recibido_en=ahora, origen=origen,
        rssi=rssi, snr=snr, saltos=_saltos(t.ttl) if origen == Origen.RECIBIDO else 0,
    ))
    if nuevo:
        # Acuses que llegaron antes que la alerta.
        Acuse.objects.filter(ref_msg_id=t.msg_id, incidente=None).update(incidente=incidente)
        consolidar_estado(incidente)
    return incidente


def _guardar_acuse(t: protocolo.TramaAcuse, ahora, origen):
    incidente = Incidente.objects.filter(msg_id=t.ref_msg_id).first()
    _, nuevo = Acuse.objects.get_or_create(msg_id=t.msg_id, defaults=dict(
        sender_id=t.sender_id, tipo=t.tipo, ref_msg_id=t.ref_msg_id,
        incidente=incidente, hora_emisor=_desde_unix(t.unix_time),
        recibido_en=ahora, origen=origen,
    ))
    if nuevo and incidente:
        consolidar_estado(incidente)


# --------------------------------------------------------------------------
# Estado consolidado del incidente
# --------------------------------------------------------------------------

@dataclass
class ResumenAtencion:
    en_camino: int
    en_sitio: int


def consolidar_estado(incidente: Incidente) -> None:
    """Emitido → En asistencia → Resuelto. Resuelto es terminal."""
    tipos = set(incidente.acuses.values_list('tipo', flat=True))
    if protocolo.ACK_RESUELTO in tipos:
        nuevo = Incidente.Estado.RESUELTO
    elif tipos & {protocolo.ACK_EN_CAMINO, protocolo.ACK_LLEGUE}:
        nuevo = Incidente.Estado.EN_ASISTENCIA
    else:
        nuevo = Incidente.Estado.EMITIDO
    if incidente.estado != Incidente.Estado.RESUELTO and incidente.estado != nuevo:
        incidente.estado = nuevo
        incidente.save(update_fields=['estado'])


def resumen_atencion(incidente: Incidente) -> ResumenAtencion:
    """Contadores por Sender_ID distinto según su acuse más avanzado."""
    mas_avanzado: dict[int, int] = {}
    for sender, tipo in incidente.acuses.exclude(tipo=protocolo.ACK_RESUELTO).values_list('sender_id', 'tipo'):
        mas_avanzado[sender] = max(mas_avanzado.get(sender, 0), tipo)
    valores = list(mas_avanzado.values())
    return ResumenAtencion(
        en_camino=valores.count(protocolo.ACK_EN_CAMINO),
        en_sitio=valores.count(protocolo.ACK_LLEGUE),
    )


# --------------------------------------------------------------------------
# Acciones del responsable (generan tramas salientes)
# --------------------------------------------------------------------------

class AccionNoPermitida(Exception):
    pass


def _encolar(trama, descripcion: str, usuario) -> TramaSaliente:
    return TramaSaliente.objects.create(
        bytes_hex=protocolo.codificar(trama).hex().upper(),
        descripcion=descripcion[:120], creada_por=usuario,
    )


def puede_resolver(incidente: Incidente) -> bool:
    """CU-09: solo el emisor original o quien ya acusó "Llegué"."""
    panel = settings.PANEL_SENDER_ID
    return incidente.sender_id == panel or incidente.acuses.filter(
        sender_id=panel, tipo=protocolo.ACK_LLEGUE).exists()


@transaction.atomic
def emitir_acuse(incidente: Incidente, tipo: int, usuario=None) -> Acuse:
    if incidente.estado == Incidente.Estado.RESUELTO:
        raise AccionNoPermitida('El incidente ya está resuelto.')
    if tipo == protocolo.ACK_RESUELTO and not puede_resolver(incidente):
        raise AccionNoPermitida('Para marcarlo como resuelto, primero confirma la llegada al sitio ("Llegué").')
    trama = protocolo.TramaAcuse(
        msg_id=protocolo.generar_id32(), sender_id=settings.PANEL_SENDER_ID, tipo=tipo,
        ref_msg_id=incidente.msg_id, unix_time=int(timezone.now().timestamp()),
    )
    _encolar(trama, f'Acuse "{Acuse.Tipo(tipo).label}" para {incidente}', usuario)
    _guardar_acuse(trama, timezone.now(), Origen.PROPIO)
    incidente.refresh_from_db()
    return incidente.acuses.get(msg_id=trama.msg_id)


@transaction.atomic
def emitir_aviso(tipo: int, texto: str, lat: float | None, lon: float | None, usuario=None) -> Incidente:
    """Difunde una alerta del responsable a toda la red."""
    trama = protocolo.TramaAlerta(
        msg_id=protocolo.generar_id32(), sender_id=settings.PANEL_SENDER_ID, tipo=tipo,
        unix_time=int(timezone.now().timestamp()), lat=lat, lon=lon, texto=texto,
    )
    protocolo.codificar(trama)  # valida longitud del texto antes de guardar
    incidente = _guardar_incidente(trama, timezone.now(), Origen.PROPIO)
    _encolar(trama, f'Aviso {incidente.categoria_nombre}: {texto or "(sin texto)"}', usuario)
    return incidente


# --------------------------------------------------------------------------
# Censo de evacuación
# --------------------------------------------------------------------------

@dataclass
class ResumenCenso:
    censo: Censo | None
    desde: datetime | None
    a_salvo: list[int]
    nodos_conocidos: int

    @property
    def sin_reporte(self) -> int:
        return max(self.nodos_conocidos - len(self.a_salvo), 0)


def censo_vigente() -> ResumenCenso:
    censo = Censo.objects.first()
    desde = censo.iniciado_en if censo else None
    reportes = ReporteSafety.objects.filter(estado=protocolo.A_SALVO)
    nodos = Nodo.objects.exclude(sender_id=settings.PANEL_SENDER_ID)
    if desde:
        reportes = reportes.filter(recibido_en__gte=desde)
        nodos = nodos.filter(visto_por_ultima_vez__gte=desde)
    a_salvo = sorted(set(reportes.values_list('sender_id', flat=True)))
    return ResumenCenso(censo, desde, a_salvo, nodos.count())


def iniciar_censo(usuario=None, motivo: str = '') -> Censo:
    return Censo.objects.create(iniciado_en=timezone.now(), iniciado_por=usuario, motivo=motivo)


# --------------------------------------------------------------------------
# Reporte del simulacro (CU-20)
# --------------------------------------------------------------------------
#
# Todos los tiempos se miden con la hora local del panel (recibido_en), no
# con la hora que reporta cada emisor, porque los relojes de los teléfonos
# pueden estar desfasados (SDS 7.6). Solo cuenta lo que la pasarela escuchó:
# un acuse que no llegó al panel no aparece en el reporte.

@dataclass
class FilaReporte:
    incidente: Incidente
    primer_acuse: float | None   # segundos desde que el panel recibió el incidente
    llegada: float | None
    resolucion: float | None
    respondedores: int           # Sender_ID distintos que acusaron, sin contar al emisor


@dataclass
class EstadisticaTiempo:
    casos: int
    mediana: float | None
    p95: float | None
    maximo: float | None


@dataclass
class ReporteSimulacro:
    desde: datetime
    hasta: datetime
    filas: list[FilaReporte]
    por_prioridad: dict[str, int]
    atendidos: int
    resueltos: int
    sin_atencion: int
    tiempo_primer_acuse: EstadisticaTiempo
    tiempo_llegada: EstadisticaTiempo
    tiempo_resolucion: EstadisticaTiempo
    avisos_emitidos: int
    acuses_del_panel: int
    a_salvo: int
    nodos_escuchados: int
    tramas_validas: int
    tramas_invalidas: int

    @property
    def total(self) -> int:
        return len(self.filas)

    def _porcentaje(self, n: int) -> float | None:
        return round(100 * n / self.total, 1) if self.total else None

    @property
    def pct_atendidos(self) -> float | None:
        return self._porcentaje(self.atendidos)

    @property
    def pct_resueltos(self) -> float | None:
        return self._porcentaje(self.resueltos)


def _segundos_desde(inicio: datetime, fin: datetime | None) -> float | None:
    # Un acuse puede llegar antes que su incidente (huérfano): cuenta como 0 s.
    return None if fin is None else max((fin - inicio).total_seconds(), 0.0)


def _p95(valores: list[float]) -> float:
    """Percentil 95 por rango más cercano (sin interpolar)."""
    ordenados = sorted(valores)
    return ordenados[max(math.ceil(0.95 * len(ordenados)) - 1, 0)]


def _estadistica(valores: list[float | None]) -> EstadisticaTiempo:
    v = [x for x in valores if x is not None]
    if not v:
        return EstadisticaTiempo(0, None, None, None)
    return EstadisticaTiempo(len(v), statistics.median(v), _p95(v), max(v))


def reporte_simulacro(desde: datetime, hasta: datetime) -> ReporteSimulacro:
    """Métricas de coordinación de la respuesta entre [desde, hasta]."""
    panel = settings.PANEL_SENDER_ID
    incidentes = (Incidente.objects
                  .filter(recibido_en__gte=desde, recibido_en__lte=hasta, origen=Origen.RECIBIDO)
                  .prefetch_related('acuses').order_by('recibido_en'))
    filas = []
    for i in incidentes:
        acuses = sorted(i.acuses.all(), key=lambda a: a.recibido_en)

        def primero(*tipos):
            return next((a.recibido_en for a in acuses if a.tipo in tipos), None)

        filas.append(FilaReporte(
            incidente=i,
            primer_acuse=_segundos_desde(i.recibido_en, primero(*protocolo.TIPOS_ACUSE)),
            llegada=_segundos_desde(i.recibido_en, primero(protocolo.ACK_LLEGUE)),
            resolucion=_segundos_desde(i.recibido_en, primero(protocolo.ACK_RESUELTO)),
            respondedores=len({a.sender_id for a in acuses if a.sender_id != i.sender_id}),
        ))

    por_prioridad = {'rojo': 0, 'amarillo': 0, 'verde': 0}
    for f in filas:
        por_prioridad[f.incidente.prioridad] = por_prioridad.get(f.incidente.prioridad, 0) + 1

    tramas = TramaRecibida.objects.filter(recibida_en__gte=desde, recibida_en__lte=hasta)
    return ReporteSimulacro(
        desde=desde, hasta=hasta, filas=filas, por_prioridad=por_prioridad,
        atendidos=sum(1 for f in filas if f.primer_acuse is not None),
        resueltos=sum(1 for f in filas if f.incidente.estado == Incidente.Estado.RESUELTO),
        sin_atencion=sum(1 for f in filas if f.incidente.estado == Incidente.Estado.EMITIDO),
        tiempo_primer_acuse=_estadistica([f.primer_acuse for f in filas]),
        tiempo_llegada=_estadistica([f.llegada for f in filas]),
        tiempo_resolucion=_estadistica([f.resolucion for f in filas]),
        avisos_emitidos=Incidente.objects.filter(
            origen=Origen.PROPIO, recibido_en__gte=desde, recibido_en__lte=hasta).count(),
        acuses_del_panel=Acuse.objects.filter(
            sender_id=panel, origen=Origen.PROPIO, recibido_en__gte=desde, recibido_en__lte=hasta).count(),
        a_salvo=ReporteSafety.objects.filter(
            estado=protocolo.A_SALVO, recibido_en__gte=desde, recibido_en__lte=hasta,
        ).values('sender_id').distinct().count(),
        nodos_escuchados=Nodo.objects.exclude(sender_id=panel).filter(
            visto_por_ultima_vez__gte=desde).count(),
        tramas_validas=tramas.filter(valida=True).count(),
        tramas_invalidas=tramas.filter(valida=False).count(),
    )
