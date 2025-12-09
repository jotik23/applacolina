from __future__ import annotations

from django import forms
from django.utils.dateparse import parse_date


class AppDateInput(forms.DateInput):
    """HTML date input that keeps ISO formatting regardless of localization."""

    input_type = "date"
    default_format = "%Y-%m-%d"

    def __init__(self, attrs: dict[str, str] | None = None, format: str | None = None) -> None:
        final_format = format or self.default_format
        final_attrs = dict(attrs) if attrs else None
        super().__init__(attrs=final_attrs, format=final_format)

    def format_value(self, value):
        if isinstance(value, str):
            parsed = parse_date(value)
            if parsed is not None:
                value = parsed
        return super().format_value(value)
