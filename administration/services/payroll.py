from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Mapping, Sequence
import calendar

from django.db.models import Prefetch, Q

from personal.models import (
    OperatorRestPeriod,
    OperatorSalary,
    RestPeriodStatus,
    Role,
    ShiftAssignment,
    UserProfile,
)

ALLOWED_PAID_RESTS_PER_PERIOD = 2

MONTH_LABELS = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}


class PayrollComputationError(Exception):
    """Raised when the payroll summary cannot be generated."""


@dataclass(frozen=True)
class PayrollPeriodInfo:
    start_date: date
    end_date: date
    label: str
    month_name: str
    year: int
    days: int
    half_label: str


def resolve_payroll_period(start_date: date, end_date: date) -> PayrollPeriodInfo:
    """Validate that the range represents a quincena and return metadata."""

    if start_date > end_date:
        raise PayrollComputationError("La fecha inicial debe ser anterior o igual a la final.")

    if start_date.year != end_date.year or start_date.month != end_date.month:
        raise PayrollComputationError("La liquidación debe realizarse dentro del mismo mes calendario.")

    month_last_day = calendar.monthrange(start_date.year, start_date.month)[1]
    period_days = (end_date - start_date).days + 1
    half_label: str

    if start_date.day == 1 and end_date.day == 15:
        half_label = "Primera quincena"
    elif start_date.day == 16 and end_date.day == month_last_day:
        half_label = "Segunda quincena"
    else:
        raise PayrollComputationError("El rango debe corresponder a una quincena (1-15 o 16-fin de mes).")

    month_name = MONTH_LABELS.get(start_date.month, start_date.strftime("%B").lower())
    label = f"{half_label} de {month_name} {start_date.year}"

    return PayrollPeriodInfo(
        start_date=start_date,
        end_date=end_date,
        label=label,
        month_name=month_name,
        year=start_date.year,
        days=period_days,
        half_label=half_label,
    )


@dataclass(frozen=True)
class PayrollOverrideData:
    operator_id: int
    amount: Decimal | None
    note: str


@dataclass(frozen=True)
class PayrollShiftDetail:
    date: date
    position_label: str
    is_overtime: bool


@dataclass(frozen=True)
class PayrollRestDetail:
    token: str
    date: date
    status: str
    notes: str
    is_extra: bool
    is_bonified: bool


@dataclass(frozen=True)
class PayrollEntry:
    operator: UserProfile
    role: Role | None
    role_label: str
    payment_type: str
    payment_type_label: str
    salary_amount: Decimal
    worked_days: int
    rest_days: int
    rest_details: Sequence[PayrollRestDetail]
    shift_details: Sequence[PayrollShiftDetail]
    extra_rest_count: int
    bonified_extra_count: int
    discounted_extra_count: int
    base_amount: Decimal
    deduction_amount: Decimal
    suggested_amount: Decimal
    final_amount: Decimal
    override_amount: Decimal | None
    override_note: str
    extra_rest_tokens: Sequence[str]


@dataclass(frozen=True)
class RolePayrollSummary:
    role: Role | None
    role_label: str
    collaborator_count: int
    total_amount: Decimal


@dataclass(frozen=True)
class PayrollSummary:
    period: PayrollPeriodInfo
    entries: Sequence[PayrollEntry]
    totals_by_role: Sequence[RolePayrollSummary]
    overall_total: Decimal


def build_payroll_summary(
    *,
    period: PayrollPeriodInfo,
    bonified_rest_tokens: Iterable[str] | None = None,
    overrides: Mapping[int, PayrollOverrideData] | None = None,
) -> PayrollSummary:
    bonified_tokens = {token for token in (bonified_rest_tokens or []) if token}
    override_map = overrides or {}

    assignments = list(
        ShiftAssignment.objects.filter(date__range=(period.start_date, period.end_date))
        .select_related("operator", "position")
        .order_by("date")
    )

    rest_periods = list(
        OperatorRestPeriod.objects.filter(
            start_date__lte=period.end_date,
            end_date__gte=period.start_date,
            status__in=[
                RestPeriodStatus.PLANNED,
                RestPeriodStatus.APPROVED,
                RestPeriodStatus.CONFIRMED,
            ],
        )
        .select_related("operator")
        .order_by("start_date")
    )

    operator_ids: set[int] = set()
    for assignment in assignments:
        if assignment.operator_id:
            operator_ids.add(assignment.operator_id)
    for rest in rest_periods:
        if rest.operator_id:
            operator_ids.add(rest.operator_id)

    if not operator_ids:
        return PayrollSummary(
            period=period,
            entries=[],
            totals_by_role=[],
            overall_total=Decimal("0.00"),
        )

    operators = list(
        UserProfile.objects.filter(pk__in=operator_ids)
        .prefetch_related(
            "roles",
            Prefetch(
                "salary_records",
                queryset=OperatorSalary.objects.order_by("-effective_from", "-id"),
            ),
        )
    )
    operator_map = {operator.pk: operator for operator in operators}

    relevant_operator_ids = [
        pk
        for pk in operator_ids
        if pk in operator_map and not operator_map[pk].is_staff
    ]

    if not relevant_operator_ids:
        return PayrollSummary(
            period=period,
            entries=[],
            totals_by_role=[],
            overall_total=Decimal("0.00"),
        )

    salary_map: dict[int, OperatorSalary] = {}
    for operator in operators:
        selected = _select_salary_for_period(operator, period.start_date, period.end_date)
        if selected:
            salary_map[operator.pk] = selected

    missing_salary = [
        operator_map[pk]
        for pk in relevant_operator_ids
        if pk not in salary_map
    ]
    if missing_salary:
        names = ", ".join(operator.get_full_name() or operator.cedula for operator in missing_salary)
        raise PayrollComputationError(
            f"Configura un salario vigente para: {names}."
        )

    assignments_by_operator: dict[int, list[ShiftAssignment]] = {pk: [] for pk in relevant_operator_ids}
    for assignment in assignments:
        if assignment.operator_id in assignments_by_operator:
            assignments_by_operator[assignment.operator_id].append(assignment)

    rest_days_by_operator: dict[int, list[PayrollRestDetail]] = {pk: [] for pk in relevant_operator_ids}
    extra_tokens_by_operator: dict[int, list[str]] = {pk: [] for pk in relevant_operator_ids}

    for rest in rest_periods:
        operator_id = rest.operator_id
        if operator_id not in rest_days_by_operator:
            continue
        start = max(rest.start_date, period.start_date)
        end = min(rest.end_date, period.end_date)
        current = start
        while current <= end:
            token = f"{operator_id}:{current.isoformat()}"
            rest_days_by_operator[operator_id].append(
                PayrollRestDetail(
                    token=token,
                    date=current,
                    status=rest.get_status_display(),
                    notes=rest.notes or "",
                    is_extra=False,  # set later after sorting
                    is_bonified=False,
                )
            )
            current += timedelta(days=1)

    for operator_id, details in rest_days_by_operator.items():
        details.sort(key=lambda item: item.date)
        for index, detail in enumerate(details):
            is_extra = index >= ALLOWED_PAID_RESTS_PER_PERIOD
            is_bonified = is_extra and detail.token in bonified_tokens
            details[index] = PayrollRestDetail(
                token=detail.token,
                date=detail.date,
                status=detail.status,
                notes=detail.notes,
                is_extra=is_extra,
                is_bonified=is_bonified,
            )
            if is_extra:
                extra_tokens_by_operator.setdefault(operator_id, []).append(detail.token)

    entries: list[PayrollEntry] = []

    role_label_by_operator: dict[int, str] = {}
    for pk, operator in operator_map.items():
        role = _primary_role(operator)
        role_label_by_operator[pk] = role.get_name_display() if role else "Sin rol"

    sorted_operator_ids = sorted(
        relevant_operator_ids,
        key=lambda pk: (
            role_label_by_operator.get(pk, "Sin rol").lower(),
            (operator_map.get(pk).apellidos or "").lower(),
            (operator_map.get(pk).nombres or "").lower(),
            pk,
        ),
    )

    for operator_id in sorted_operator_ids:
        operator = operator_map.get(operator_id)
        if not operator or operator.is_staff:
            continue

        role = _primary_role(operator)
        role_label = role.get_name_display() if role else "Sin rol"
        salary = salary_map[operator_id]
        payment_type_label = OperatorSalary.PaymentType(salary.payment_type).label

        shift_details = [
            PayrollShiftDetail(
                date=assignment.date,
                position_label=assignment.position.name if assignment.position_id else "",
                is_overtime=assignment.is_overtime,
            )
            for assignment in sorted(assignments_by_operator.get(operator_id, []), key=lambda a: a.date)
        ]

        rest_details = rest_days_by_operator.get(operator_id, [])
        worked_days = len(shift_details)
        rest_days = len(rest_details)
        extra_tokens = extra_tokens_by_operator.get(operator_id, [])
        extra_rest_count = sum(1 for detail in rest_details if detail.is_extra)
        bonified_extra_count = sum(1 for detail in rest_details if detail.is_extra and detail.is_bonified)
        non_bonified_extra = sum(1 for detail in rest_details if detail.is_extra and not detail.is_bonified)

        if salary.payment_type == OperatorSalary.PaymentType.MONTHLY:
            base_amount = (salary.amount / Decimal("2"))
            per_day_value = (base_amount / Decimal(period.days)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            deduction = (per_day_value * Decimal(non_bonified_extra)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            base_amount = (salary.amount * Decimal(worked_days))
            deduction = Decimal("0.00")

        suggested = (base_amount - deduction).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if suggested < Decimal("0.00"):
            suggested = Decimal("0.00")

        override = override_map.get(operator_id)
        final_amount = suggested
        override_amount = None
        override_note = ""
        if override and override.amount is not None:
            override_amount = override.amount
            final_amount = override.amount
            override_note = override.note

        entries.append(
            PayrollEntry(
                operator=operator,
                role=role,
                role_label=role_label,
                payment_type=salary.payment_type,
                payment_type_label=payment_type_label,
                salary_amount=salary.amount,
                worked_days=worked_days,
                rest_days=rest_days,
                rest_details=rest_details,
                shift_details=shift_details,
                extra_rest_count=extra_rest_count,
                bonified_extra_count=bonified_extra_count,
                discounted_extra_count=non_bonified_extra,
                base_amount=base_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                deduction_amount=deduction,
                suggested_amount=suggested,
                final_amount=final_amount,
                override_amount=override_amount,
                override_note=override_note,
                extra_rest_tokens=extra_tokens,
            )
        )

    role_totals: dict[str, RolePayrollSummary] = {}
    for entry in entries:
        role_key = entry.role.pk if entry.role else "__none__"
        summary = role_totals.get(role_key)
        if summary is None:
            role_totals[role_key] = RolePayrollSummary(
                role=entry.role,
                role_label=entry.role_label,
                collaborator_count=1,
                total_amount=entry.final_amount,
            )
        else:
            role_totals[role_key] = RolePayrollSummary(
                role=summary.role,
                role_label=summary.role_label,
                collaborator_count=summary.collaborator_count + 1,
                total_amount=(summary.total_amount + entry.final_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            )

    totals_by_role = sorted(role_totals.values(), key=lambda item: item.role_label.lower())
    overall_total = sum((entry.final_amount for entry in entries), Decimal("0.00")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return PayrollSummary(
        period=period,
        entries=entries,
        totals_by_role=totals_by_role,
        overall_total=overall_total,
    )


def _select_salary_for_period(operator: UserProfile, start_date: date, end_date: date) -> OperatorSalary | None:
    """Return the latest salary that covers at least part of the range."""

    salaries = list(operator.salary_records.all())
    for salary in salaries:
        if salary.effective_from > end_date:
            continue
        if salary.effective_until and salary.effective_until < start_date:
            continue
        return salary
    return None


def _primary_role(operator: UserProfile | None) -> Role | None:
    if not operator:
        return None
    roles = list(operator.roles.all())
    if not roles:
        return None
    roles.sort(key=lambda obj: obj.name)
    return roles[0]
