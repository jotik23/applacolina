from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _format_with_thousands(value: Decimal, decimals: int) -> str:
    decimals = max(decimals, 0)
    formatted = format(value, f",.{decimals}f")
    if decimals == 0:
        return formatted.replace(",", ".")
    integer_part, fraction_part = formatted.split(".")
    integer_part = integer_part.replace(",", ".")
    return f"{integer_part},{fraction_part}"


@register.filter(name="number_format")
def number_format(value: object, decimals: int = 0) -> str:
    """Format numbers using dots as thousand separators and comma decimals."""
    try:
        decimals_int = int(decimals)
    except (TypeError, ValueError):
        decimals_int = 0
    number = _to_decimal(value)
    return _format_with_thousands(number, decimals_int)


@register.filter(name="peso")
def peso(value: object, decimals: int = 0) -> str:
    """Format numbers as Colombian peso amounts."""
    formatted_number = number_format(value, decimals)
    return f"$ {formatted_number}"

