from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

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


def _coerce_decimal(value):
    if isinstance(value, Decimal):
        return value
    if value in (None, '', '—', '...'):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _resolve_decimals(decimals) -> int:
    try:
        return max(int(decimals), 0)
    except (TypeError, ValueError):
        return 1


def _format_decimal(number: Decimal, decimals_int: int) -> str:
    quantize_exp = Decimal("1").scaleb(-decimals_int) if decimals_int else Decimal("1")
    quantized = number.quantize(quantize_exp, rounding=ROUND_HALF_UP)
    return format(quantized, f",.{decimals_int}f")


@register.filter(name="cartons")
def format_cartons(value, decimals: int = 1):
    """Format carton amounts with thousand separators and fallback dash."""
    number = _coerce_decimal(value)
    if number is None:
        return "—"

    decimals_int = _resolve_decimals(decimals)

    return _format_decimal(number, decimals_int)


@register.filter(name="cartons_dash_zero")
def format_cartons_dash_zero(value, decimals: int = 1):
    """Format carton values but show '-' whenever the quantity equals zero."""
    number = _coerce_decimal(value)
    if number is None:
        return "—"

    decimals_int = _resolve_decimals(decimals)
    if number == Decimal("0"):
        return "-"

    return _format_decimal(number, decimals_int)
