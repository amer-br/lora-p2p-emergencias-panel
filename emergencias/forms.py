from django import forms
from django.conf import settings

from . import protocolo
from .models import Incidente


def _en_campus(lat, lon):
    l = settings.LIMITES_CAMPUS
    return l['sur'] <= lat <= l['norte'] and l['oeste'] <= lon <= l['este']


class AvisoForm(forms.Form):
    tipo = forms.TypedChoiceField(
        label='Categoría', coerce=int,
        choices=[(c.tipo, f'{c.nombre} ({c.prioridad})') for c in protocolo.CATEGORIAS.values()],
    )
    texto = forms.CharField(
        label='Mensaje', required=False, max_length=protocolo.MAX_BYTES_TEXTO,
        help_text=f'Máximo {protocolo.MAX_BYTES_TEXTO} bytes (los acentos cuentan doble). El SOS no lleva texto.',
    )
    lat = forms.FloatField(label='Latitud', required=False, widget=forms.HiddenInput)
    lon = forms.FloatField(label='Longitud', required=False, widget=forms.HiddenInput)

    def clean(self):
        datos = super().clean()
        texto = datos.get('texto') or ''
        if datos.get('tipo') == protocolo.TIPO_SOS:
            datos['texto'] = ''
        elif len(texto.encode('utf-8')) > protocolo.MAX_BYTES_TEXTO:
            self.add_error('texto', f'El mensaje ocupa {len(texto.encode())} bytes; el máximo es {protocolo.MAX_BYTES_TEXTO}.')
        lat, lon = datos.get('lat'), datos.get('lon')
        if (lat is None) != (lon is None):
            raise forms.ValidationError('Indica latitud y longitud juntas, o ninguna.')
        if lat is not None and not _en_campus(lat, lon):
            raise forms.ValidationError('La ubicación está fuera de los límites del campus.')
        return datos


class NotasForm(forms.ModelForm):
    class Meta:
        model = Incidente
        fields = ['notas']
        widgets = {'notas': forms.Textarea(attrs={'rows': 3})}


class CensoForm(forms.Form):
    motivo = forms.CharField(label='Motivo', max_length=120, required=False,
                             widget=forms.TextInput(attrs={'placeholder': 'p. ej. Simulacro 15 de octubre'}))


class ReporteForm(forms.Form):
    """Periodo del reporte del simulacro (CU-20)."""

    _formato = '%Y-%m-%dT%H:%M'
    desde = forms.DateTimeField(
        label='Desde', input_formats=[_formato],
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format=_formato))
    hasta = forms.DateTimeField(
        label='Hasta', input_formats=[_formato],
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format=_formato))

    def clean(self):
        datos = super().clean()
        if datos.get('desde') and datos.get('hasta') and datos['desde'] >= datos['hasta']:
            raise forms.ValidationError('El inicio del periodo debe ser anterior al final.')
        return datos
