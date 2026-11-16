from django import template

from ..models import hex8 as _hex8

register = template.Library()


@register.filter
def hex8(valor):
    """Muestra un identificador de 32 bits como en la app: 8 dígitos hex."""
    try:
        return _hex8(int(valor))
    except (TypeError, ValueError):
        return valor


@register.filter
def duracion(segundos):
    """Segundos como «45 s», «3 min 20 s» o «1 h 05 min»; «—» si no hay dato."""
    if segundos is None:
        return '—'
    s = int(round(segundos))
    if s < 60:
        return f'{s} s'
    if s < 3600:
        return f'{s // 60} min {s % 60:02d} s'
    return f'{s // 3600} h {s % 3600 // 60:02d} min'
