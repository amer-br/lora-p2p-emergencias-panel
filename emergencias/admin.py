from django.contrib import admin

from .models import (
    Acuse, Censo, EstadoRadio, Incidente, Nodo, Recurso, ReporteSafety,
    TramaRecibida, TramaSaliente,
)


class SoloLecturaMixin:
    """Los datos que llegan por radio no se editan a mano."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Recurso)
class RecursoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'tipo', 'descripcion', 'lat', 'lon', 'activo', 'actualizado_en']
    list_filter = ['tipo', 'activo']
    list_editable = ['activo']
    search_fields = ['nombre', 'descripcion']


@admin.register(Nodo)
class NodoAdmin(admin.ModelAdmin):
    list_display = ['id_hex', 'alias', 'visto_por_ultima_vez', 'ultimo_rssi', 'ultimo_snr', 'tramas_recibidas']
    list_editable = ['alias']
    search_fields = ['alias']
    readonly_fields = ['sender_id', 'visto_por_ultima_vez', 'ultimo_rssi', 'ultimo_snr', 'tramas_recibidas',
                       'ultima_lat', 'ultima_lon', 'ultima_posicion_en']

    def has_add_permission(self, request):
        return False

    @admin.display(description='Sender_ID')
    def id_hex(self, obj):
        return obj.id_hex


class AcuseInline(admin.TabularInline):
    model = Acuse
    extra = 0
    can_delete = False
    fields = ['emisor_hex', 'tipo', 'recibido_en', 'origen']
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Incidente)
class IncidenteAdmin(admin.ModelAdmin):
    list_display = ['id_hex', 'categoria_nombre', 'texto', 'estado', 'origen', 'emisor_hex', 'recibido_en', 'saltos']
    list_filter = ['estado', 'tipo', 'origen']
    search_fields = ['texto', 'notas']
    date_hierarchy = 'recibido_en'
    inlines = [AcuseInline]
    readonly_fields = ['msg_id', 'sender_id', 'tipo', 'lat', 'lon', 'texto', 'hora_emisor', 'recibido_en',
                       'origen', 'rssi', 'snr', 'saltos', 'estado']
    fields = readonly_fields + ['notas']

    def has_add_permission(self, request):
        return False

    @admin.display(description='Msg_ID')
    def id_hex(self, obj):
        return obj.id_hex

    @admin.display(description='Categoría')
    def categoria_nombre(self, obj):
        return obj.categoria_nombre

    @admin.display(description='Emisor')
    def emisor_hex(self, obj):
        return obj.emisor_hex


@admin.register(ReporteSafety)
class ReporteSafetyAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ['msg_id', 'sender_id', 'estado', 'recibido_en']
    date_hierarchy = 'recibido_en'


@admin.register(Censo)
class CensoAdmin(admin.ModelAdmin):
    list_display = ['iniciado_en', 'iniciado_por', 'motivo']


@admin.register(TramaSaliente)
class TramaSalienteAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ['descripcion', 'estado', 'creada_en', 'enviada_en', 'creada_por']
    list_filter = ['estado']


@admin.register(TramaRecibida)
class TramaRecibidaAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ['recibida_en', 'resumen', 'valida', 'rssi', 'snr']
    list_filter = ['valida']
    date_hierarchy = 'recibida_en'


@admin.register(EstadoRadio)
class EstadoRadioAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ['conectado', 'url', 'actualizado_en', 'ultimo_error']
