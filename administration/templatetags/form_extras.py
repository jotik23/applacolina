from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def add_class(field, css: str):
    if not css:
        return field
    existing = field.field.widget.attrs.get('class', '')
    merged = f"{existing} {css}".strip()
    field.field.widget.attrs['class'] = merged
    return field


@register.filter
def add_attr(field, arg: str):
    if not arg or '=' not in arg:
        return field
    key, value = arg.split('=', 1)
    field.field.widget.attrs[key.strip()] = value.strip()
    return field


@register.filter
def dict_get(value, key):
    if isinstance(value, dict):
        return value.get(key)
    return None


@register.filter
def multiply(value, arg):
    """Multiply two numeric values safely for template usage."""
    try:
        left = Decimal(str(value or 0))
        right = Decimal(str(arg or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')
    return left * right


@register.filter(name='cop_currency')
def cop_currency(value):
    """Format a number using Colombian thousand/decimal separators."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value
    quantized = amount.quantize(Decimal('0.01'))
    sign = '-' if quantized < 0 else ''
    absolute = abs(quantized)
    integer_part, decimal_part = f"{absolute:.2f}".split('.')
    integer_display = format(int(integer_part), ',').replace(',', '.')
    return f"{sign}{integer_display},{decimal_part}"
