from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence

from django.core.exceptions import ValidationError

from personal.models import OperatorSalary, UserProfile

ParsedSalarySequence = Sequence["ParsedSalaryInput"]


@dataclass(frozen=True)
class ParsedSalaryInput:
    id: int | None
    amount: Decimal
    payment_type: str
    effective_from: date
    effective_until: date | None


def _coerce_decimal(value: object, index: int) -> Decimal:
    if value in ("", None):
        raise ValidationError(f"Debes ingresar un monto para el salario #{index}.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValidationError(f"El monto ingresado en el salario #{index} es inválido.")
    if amount <= 0:
        raise ValidationError(f"El monto del salario #{index} debe ser mayor a cero.")
    return amount


def _coerce_date(value: object, field_label: str, index: int) -> date:
    if not value:
        raise ValidationError(f"Debes definir la fecha de {field_label} para el salario #{index}.")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise ValidationError(f"La fecha de {field_label} del salario #{index} es inválida.")


def _coerce_id(raw_id: object, index: int) -> int | None:
    if raw_id in ("", None):
        return None
    try:
        value = int(raw_id)
    except (TypeError, ValueError):
        raise ValidationError(f"El identificador del salario #{index} es inválido.")
    if value <= 0:
        raise ValidationError(f"El identificador del salario #{index} es inválido.")
    return value


def parse_salary_entries(raw_entries: object) -> list[ParsedSalaryInput]:
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValidationError("Debes registrar al menos un salario para el colaborador.")

    parsed: list[ParsedSalaryInput] = []
    for position, entry in enumerate(raw_entries, start=1):
        if not isinstance(entry, dict):
            raise ValidationError(f"Los datos del salario #{position} son inválidos.")
        salary_id = _coerce_id(entry.get("id"), position)
        amount = _coerce_decimal(entry.get("amount"), position)
        payment_type = entry.get("payment_type")
        if payment_type not in OperatorSalary.PaymentType.values:
            raise ValidationError(f"Selecciona un esquema de pago válido para el salario #{position}.")
        effective_from = _coerce_date(entry.get("effective_from"), "inicio", position)
        effective_until_value = entry.get("effective_until")
        effective_until = None
        if effective_until_value not in ("", None):
            effective_until = _coerce_date(effective_until_value, "fin", position)
            if effective_until < effective_from:
                raise ValidationError(
                    f"La fecha de fin debe ser posterior a la de inicio en el salario #{position}."
                )
        parsed.append(
            ParsedSalaryInput(
                id=salary_id,
                amount=amount.quantize(Decimal("0.01")),
                payment_type=str(payment_type),
                effective_from=effective_from,
                effective_until=effective_until,
            )
        )

    sorted_entries = sorted(parsed, key=lambda item: (item.effective_from, item.effective_until or date.max))
    for previous, current in zip(sorted_entries, sorted_entries[1:]):
        previous_end = previous.effective_until or date.max
        if current.effective_from <= previous_end:
            raise ValidationError(
                "Las fechas de vigencia de los salarios no pueden superponerse. Revisa las fechas ingresadas."
            )

    today = UserProfile.colombia_today()
    has_active = any(
        entry.effective_from <= today and (entry.effective_until is None or entry.effective_until >= today)
        for entry in parsed
    )
    if not has_active:
        raise ValidationError("El colaborador debe tener al menos un salario vigente para la fecha actual.")

    return sorted(parsed, key=lambda item: (item.effective_from, item.id or 0))


def apply_salary_entries(operator: UserProfile, entries: ParsedSalarySequence) -> Iterable[OperatorSalary]:
    if not entries:
        raise ValidationError("Debes registrar al menos un salario para el colaborador.")

    existing = {
        salary.id: salary
        for salary in OperatorSalary.objects.select_for_update().filter(operator=operator)
    }
    desired_ids: set[int] = set()
    to_update: list[OperatorSalary] = []
    to_create: list[OperatorSalary] = []

    for entry in entries:
        if entry.id:
            salary = existing.get(entry.id)
            if not salary:
                raise ValidationError("Intentas actualizar un salario que no existe.")
            salary.amount = entry.amount
            salary.payment_type = entry.payment_type
            salary.effective_from = entry.effective_from
            salary.effective_until = entry.effective_until
            to_update.append(salary)
            desired_ids.add(entry.id)
        else:
            to_create.append(
                OperatorSalary(
                    operator=operator,
                    amount=entry.amount,
                    payment_type=entry.payment_type,
                    effective_from=entry.effective_from,
                    effective_until=entry.effective_until,
                )
            )

    removed_ids = [salary_id for salary_id in existing.keys() if salary_id and salary_id not in desired_ids]
    if removed_ids:
        OperatorSalary.objects.filter(pk__in=removed_ids).delete()
    if to_update:
        OperatorSalary.objects.bulk_update(
            to_update,
            ["amount", "payment_type", "effective_from", "effective_until"],
        )
    if to_create:
        OperatorSalary.objects.bulk_create(to_create)

    return OperatorSalary.objects.filter(operator=operator).order_by("effective_from", "id")


def ensure_active_salary(operator: UserProfile, reference_date: date | None = None) -> None:
    reference = reference_date or UserProfile.colombia_today()
    if not operator.salary_records.active_on(reference).exists():  # type: ignore[attr-defined]
        raise ValidationError("El colaborador debe tener un salario activo para la fecha indicada.")
