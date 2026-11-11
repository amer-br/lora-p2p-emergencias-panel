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
