from __future__ import annotations

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
