import csv
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Case, IntegerField, Value, When
from django.http import HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import protocolo, servicios, teselas
from .forms import AvisoForm, CensoForm, NotasForm, ReporteForm
from .models import Acuse, EstadoRadio, Incidente, Nodo, Recurso, TramaSaliente, hex8

_ORDEN_PRIORIDAD = {'rojo': 0, 'amarillo': 1, 'verde': 2}


def _por_prioridad(qs):
    """Ordena rojo → amarillo → verde y, dentro de cada nivel, lo más reciente primero."""
    orden = Case(
        *[When(tipo=t, then=Value(_ORDEN_PRIORIDAD[c.prioridad])) for t, c in protocolo.CATEGORIAS.items()],
        default=Value(3), output_field=IntegerField(),
    )
    return qs.annotate(orden_prioridad=orden).order_by('orden_prioridad', '-recibido_en')


def _activos():
    return _por_prioridad(Incidente.objects.exclude(estado=Incidente.Estado.RESUELTO))


def _contexto_tablero():
    activos = list(_activos()[:50])
    for i in activos:
        i.atencion = servicios.resumen_atencion(i)
    return {
        'activos': activos,
        'radio': EstadoRadio.actual(),
        'censo': servicios.censo_vigente(),
        'pendientes': TramaSaliente.objects.filter(estado=TramaSaliente.Estado.PENDIENTE).count(),
        'nodos': Nodo.objects.exclude(sender_id=settings.PANEL_SENDER_ID)[:8],
        'total_nodos': Nodo.objects.exclude(sender_id=settings.PANEL_SENDER_ID).count(),
    }


@login_required
def tablero(request):
    return render(request, 'emergencias/tablero.html', {
        **_contexto_tablero(), 'limites': settings.LIMITES_CAMPUS,
    })


@login_required
def tablero_fragmento(request):
    """Columna lateral del tablero; la página la recarga cada pocos segundos."""
    return render(request, 'emergencias/_tablero_lateral.html', _contexto_tablero())


@login_required
def mapa_datos(request):
    incidentes = [
        {
            'url': reverse('incidente_detalle', args=[i.pk]), 'lat': i.lat, 'lon': i.lon,
            'categoria': i.categoria_nombre, 'clave': i.categoria.clave if i.categoria else '',
            'prioridad': i.prioridad, 'texto': i.texto, 'estado': i.get_estado_display(),
            'propio': i.origen == 'propio',
        }
        for i in _activos() if i.tiene_ubicacion
    ]
    recursos = list(Recurso.objects.filter(activo=True).values('nombre', 'tipo', 'descripcion', 'lat', 'lon'))
    for r in recursos:
        r['tipo_nombre'] = Recurso.Tipo(r['tipo']).label
    nodos = [
        {'id': n.id_hex, 'nombre': str(n), 'lat': n.ultima_lat, 'lon': n.ultima_lon,
         'hora': n.ultima_posicion_en.isoformat()}
        for n in Nodo.objects.exclude(ultima_lat=None).exclude(sender_id=settings.PANEL_SENDER_ID)
    ]
    return JsonResponse({'incidentes': incidentes, 'recursos': recursos, 'nodos': nodos})


@login_required
def tesela(request, z, x, y):
    """Tesela del campus desde la caché local; 404 si no hay red ni caché (se ve la cuadrícula)."""
    datos = teselas.obtener(z, x, y)
    if datos is None:
        return HttpResponseNotFound()
    resp = HttpResponse(datos, content_type='image/png')
    resp['Cache-Control'] = 'private, max-age=604800'
    return resp


@login_required
def incidentes(request):
    qs = Incidente.objects.all()
    estado = request.GET.get('estado', 'activos')
    if estado == 'activos':
        qs = qs.exclude(estado=Incidente.Estado.RESUELTO)
    elif estado in Incidente.Estado.values:
        qs = qs.filter(estado=estado)
    tipo = request.GET.get('tipo')
    if tipo and tipo.isdigit():
        qs = qs.filter(tipo=int(tipo))
    return render(request, 'emergencias/incidentes.html', {
        'incidentes': _por_prioridad(qs)[:200],
        'estado': estado, 'tipo': tipo or '',
        'estados': Incidente.Estado.choices,
        'categorias': protocolo.CATEGORIAS.values(),
    })


@login_required
def incidente_detalle(request, pk):
    incidente = get_object_or_404(Incidente, pk=pk)
    notas = NotasForm(request.POST or None, instance=incidente)
    if request.method == 'POST' and notas.is_valid():
        notas.save()
        messages.success(request, 'Notas guardadas.')
        return redirect(incidente_detalle, pk=pk)
    acuses_panel = set(incidente.acuses.filter(sender_id=settings.PANEL_SENDER_ID).values_list('tipo', flat=True))
    return render(request, 'emergencias/incidente_detalle.html', {
        'i': incidente,
        'acuses': incidente.acuses.all(),
        'atencion': servicios.resumen_atencion(incidente),
        'notas': notas,
        'panel_id': settings.PANEL_SENDER_ID,
        'puede_resolver': servicios.puede_resolver(incidente),
        'ya_en_camino': protocolo.ACK_EN_CAMINO in acuses_panel,
        'ya_llegue': protocolo.ACK_LLEGUE in acuses_panel,
        'ACK': Acuse.Tipo,
    })


@login_required
@require_POST
def incidente_acuse(request, pk):
    incidente = get_object_or_404(Incidente, pk=pk)
    try:
        tipo = Acuse.Tipo(int(request.POST['tipo']))
        servicios.emitir_acuse(incidente, tipo, request.user)
        messages.success(request, f'Acuse "{tipo.label}" enviado a la red.')
    except (KeyError, ValueError):
        messages.error(request, 'Tipo de acuse no válido.')
    except servicios.AccionNoPermitida as e:
        messages.error(request, str(e))
    return redirect(incidente_detalle, pk=pk)


@login_required
def emitir_aviso(request):
    form = AvisoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        incidente = servicios.emitir_aviso(d['tipo'], d['texto'], d['lat'], d['lon'], request.user)
        messages.success(request, f'Aviso "{incidente.categoria_nombre}" encolado para transmisión.')
        return redirect(incidente_detalle, pk=incidente.pk)
    return render(request, 'emergencias/aviso.html', {
        'form': form, 'limites': settings.LIMITES_CAMPUS,
        'categorias': {t: {'clave': c.clave, 'prioridad': c.prioridad} for t, c in protocolo.CATEGORIAS.items()},
    })


@login_required
def censo(request):
    form = CensoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        servicios.iniciar_censo(request.user, form.cleaned_data['motivo'])
        messages.success(request, 'Se inició un nuevo censo; los reportes anteriores ya no cuentan.')
        return redirect(censo)
    resumen = servicios.censo_vigente()
    a_salvo = {n.sender_id: n for n in Nodo.objects.filter(sender_id__in=resumen.a_salvo)}
    return render(request, 'emergencias/censo.html', {
        'resumen': resumen, 'form': form,
        'personas': [a_salvo.get(s) or f'{s:08X}' for s in resumen.a_salvo],
    })


def _periodo_reporte(request):
    """Periodo pedido o, por omisión, desde el inicio del censo vigente (o 24 h atrás) hasta ahora."""
    ahora = timezone.now()
    censo = servicios.censo_vigente()
    form = ReporteForm(request.GET or None, initial={
        'desde': timezone.localtime(censo.desde or ahora - timedelta(hours=24)),
        'hasta': timezone.localtime(ahora),
    })
    if form.is_bound and form.is_valid():
        return form, form.cleaned_data['desde'], form.cleaned_data['hasta']
    return form, form.initial['desde'], form.initial['hasta']


@login_required
def reporte(request):
    form, desde, hasta = _periodo_reporte(request)
    return render(request, 'emergencias/reporte.html', {
        'form': form, 'r': servicios.reporte_simulacro(desde, hasta),
        'consulta': request.GET.urlencode(),
    })


@login_required
def reporte_csv(request):
    """Una fila por incidente, para analizar el simulacro en una hoja de cálculo."""
    _, desde, hasta = _periodo_reporte(request)
    r = servicios.reporte_simulacro(desde, hasta)
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = (
        f'attachment; filename="reporte_{timezone.localtime(desde):%Y%m%d_%H%M}.csv"')
    resp.write('\ufeff')  # BOM: Excel abre bien los acentos
    w = csv.writer(resp)
    w.writerow(['msg_id', 'categoria', 'prioridad', 'estado', 'recibido_en', 'emisor',
                'saltos', 'rssi', 'snr', 'seg_primer_acuse', 'seg_llegada', 'seg_resolucion',
                'respondedores', 'texto'])
    for f in r.filas:
        i = f.incidente
        w.writerow([hex8(i.msg_id), i.categoria_nombre, i.prioridad, i.estado,
                    timezone.localtime(i.recibido_en).isoformat(timespec='seconds'),
                    hex8(i.sender_id), i.saltos, i.rssi, i.snr,
                    f.primer_acuse, f.llegada, f.resolucion, f.respondedores, i.texto])
    return resp
