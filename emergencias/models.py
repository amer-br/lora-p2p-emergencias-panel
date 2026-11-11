from django.conf import settings
from django.db import models

from . import protocolo


def hex8(valor: int) -> str:
    """Identificador de 32 bits como se muestra en la app (8 dígitos hex)."""
    return f'{valor:08X}'


class Origen(models.TextChoices):
    RECIBIDO = 'recibido', 'Recibido por radio'
    PROPIO = 'propio', 'Emitido por el panel'


class Nodo(models.Model):
    """Teléfono + módem que se ha escuchado en la red (identificado por Sender_ID)."""

    sender_id = models.BigIntegerField(unique=True)
    alias = models.CharField(max_length=60, blank=True, help_text='Nombre opcional, p. ej. "Brigada 2"')
    visto_por_ultima_vez = models.DateTimeField()
    ultimo_rssi = models.IntegerField(null=True, blank=True)
    ultimo_snr = models.FloatField(null=True, blank=True)
    tramas_recibidas = models.PositiveIntegerField(default=0)
    ultima_lat = models.FloatField(null=True, blank=True)
    ultima_lon = models.FloatField(null=True, blank=True)
    ultima_posicion_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-visto_por_ultima_vez']
        verbose_name_plural = 'nodos'

    def __str__(self):
        return self.alias or hex8(self.sender_id)

    @property
    def id_hex(self):
        return hex8(self.sender_id)


class Incidente(models.Model):
    class Estado(models.TextChoices):
        EMITIDO = 'emitido', 'Emitido'
        EN_ASISTENCIA = 'en_asistencia', 'En asistencia'
        RESUELTO = 'resuelto', 'Resuelto'

    msg_id = models.BigIntegerField(unique=True)
    sender_id = models.BigIntegerField(db_index=True)
    tipo = models.PositiveSmallIntegerField()
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    texto = models.CharField(max_length=64, blank=True)
    hora_emisor = models.DateTimeField(help_text='Hora reportada por el emisor; solo informativa')
    recibido_en = models.DateTimeField(db_index=True)
    origen = models.CharField(max_length=10, choices=Origen.choices, default=Origen.RECIBIDO)
    rssi = models.IntegerField(null=True, blank=True)
    snr = models.FloatField(null=True, blank=True)
    saltos = models.PositiveSmallIntegerField(null=True, blank=True)
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.EMITIDO, db_index=True)
    notas = models.TextField(blank=True, help_text='Notas internas del responsable (no se transmiten)')

    class Meta:
        ordering = ['-recibido_en']

    def __str__(self):
        return f'{self.categoria_nombre} · {hex8(self.msg_id)}'

    @property
    def categoria(self):
        return protocolo.CATEGORIAS.get(self.tipo)

    @property
    def categoria_nombre(self):
        c = self.categoria
        return c.nombre if c else f'Tipo 0x{self.tipo:02X}'

    @property
    def prioridad(self):
        c = self.categoria
        return c.prioridad if c else 'verde'

    @property
    def es_sos(self):
        return self.tipo == protocolo.TIPO_SOS

    @property
    def tiene_ubicacion(self):
        return self.lat is not None and self.lon is not None

    @property
    def id_hex(self):
        return hex8(self.msg_id)

    @property
    def emisor_hex(self):
        return hex8(self.sender_id)


class Acuse(models.Model):
    """Acuse de atención (ACK_EN_CAMINO, ACK_LLEGUE, ACK_RESUELTO)."""

    class Tipo(models.IntegerChoices):
        EN_CAMINO = protocolo.ACK_EN_CAMINO, 'Voy en camino'
        LLEGUE = protocolo.ACK_LLEGUE, 'Llegué'
        RESUELTO = protocolo.ACK_RESUELTO, 'Resuelto'

    msg_id = models.BigIntegerField(unique=True)
    sender_id = models.BigIntegerField()
    tipo = models.PositiveSmallIntegerField(choices=Tipo.choices)
    ref_msg_id = models.BigIntegerField(db_index=True)
    # Puede llegar antes que el incidente; se enlaza cuando este llegue.
    incidente = models.ForeignKey(Incidente, null=True, blank=True, on_delete=models.CASCADE, related_name='acuses')
    hora_emisor = models.DateTimeField()
    recibido_en = models.DateTimeField()
    origen = models.CharField(max_length=10, choices=Origen.choices, default=Origen.RECIBIDO)

    class Meta:
        ordering = ['recibido_en']

    def __str__(self):
        return f'{self.get_tipo_display()} de {hex8(self.sender_id)}'

    @property
    def emisor_hex(self):
        return hex8(self.sender_id)


class ReporteSafety(models.Model):
    """Reporte "Estoy a salvo" (Safety Check)."""

    msg_id = models.BigIntegerField(unique=True)
    sender_id = models.BigIntegerField(db_index=True)
    estado = models.PositiveSmallIntegerField(default=protocolo.A_SALVO)
    hora_emisor = models.DateTimeField()
    recibido_en = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['-recibido_en']
        verbose_name = 'reporte "Estoy a salvo"'
        verbose_name_plural = 'reportes "Estoy a salvo"'

    def __str__(self):
        return f'{hex8(self.sender_id)} a salvo'


class Censo(models.Model):
    """Inicio de un censo de evacuación (CU-07 A1). El vigente es el más reciente."""

    iniciado_en = models.DateTimeField()
    iniciado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    motivo = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['-iniciado_en']
        verbose_name_plural = 'censos'

    def __str__(self):
        return f'Censo desde {self.iniciado_en:%d/%m/%Y %H:%M}'


class Recurso(models.Model):
    class Tipo(models.TextChoices):
        PUNTO_REUNION = 'PUNTO_REUNION', 'Punto de reunión'
        REFUGIO = 'REFUGIO', 'Refugio'
        SALIDA_EVACUACION = 'SALIDA_EVACUACION', 'Salida de evacuación'
        BOTIQUIN = 'BOTIQUIN', 'Botiquín'
        DEA = 'DEA', 'DEA'

    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    nombre = models.CharField(max_length=80, unique=True)
    descripcion = models.CharField(max_length=200, blank=True)
    lat = models.FloatField()
    lon = models.FloatField()
    activo = models.BooleanField(default=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['tipo', 'nombre']

    def __str__(self):
        return self.nombre


class TramaSaliente(models.Model):
    """Cola de tramas que el panel quiere transmitir; el servicio de radio las envía."""

    class Estado(models.TextChoices):
        PENDIENTE = 'pendiente', 'Pendiente'
        ENVIADA = 'enviada', 'Enviada'

    bytes_hex = models.CharField(max_length=128)
    descripcion = models.CharField(max_length=120)
    creada_en = models.DateTimeField(auto_now_add=True)
    creada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE, db_index=True)
    enviada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-creada_en']
        verbose_name = 'trama saliente'
        verbose_name_plural = 'tramas salientes'

    def __str__(self):
        return self.descripcion


class TramaRecibida(models.Model):
    """Bitácora de radio: toda trama escuchada, válida o no (diagnóstico, CU-05)."""

    recibida_en = models.DateTimeField(db_index=True)
    bytes_hex = models.CharField(max_length=512)
    rssi = models.IntegerField(null=True, blank=True)
    snr = models.FloatField(null=True, blank=True)
    valida = models.BooleanField()
    resumen = models.CharField(max_length=160)

    class Meta:
        ordering = ['-recibida_en']
        verbose_name = 'trama recibida'
        verbose_name_plural = 'bitácora de radio'

    def __str__(self):
        return self.resumen


class EstadoRadio(models.Model):
    """Fila única con el estado de la conexión del servicio de radio."""

    conectado = models.BooleanField(default=False)
    url = models.CharField(max_length=200, blank=True)
    actualizado_en = models.DateTimeField(null=True, blank=True)
    ultimo_error = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = 'estado de la radio'
        verbose_name_plural = 'estado de la radio'

    @classmethod
    def actual(cls):
        return cls.objects.get_or_create(pk=1)[0]
