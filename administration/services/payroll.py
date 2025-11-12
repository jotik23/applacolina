from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Mapping, Sequence
import calendar

from django.db import models
from django.db.models import Prefetch

from personal.models import (
    OperatorRestPeriod,
    OperatorSalary,
    PositionJobType,
    RestPeriodStatus,
    ShiftAssignment,
    UserProfile,
)

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
class PayrollIdleDetail:
    token: str
    date: date
    is_bonified: bool


@dataclass(frozen=True)
class PayrollEntry:
    operator: UserProfile
    job_type: str | None
    job_type_label: str
    farm_id: int | None
    farm_label: str
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
    non_worked_details: Sequence[PayrollIdleDetail]
    non_worked_count: int
    bonified_non_worked_count: int
    discounted_non_worked_count: int
    base_amount: Decimal
    deduction_amount: Decimal
    suggested_amount: Decimal
    final_amount: Decimal
    override_amount: Decimal | None
    override_note: str
    extra_rest_tokens: Sequence[str]


@dataclass(frozen=True)
class JobTypePayrollSummary:
    job_type: str | None
    job_type_label: str
    collaborator_count: int
    total_amount: Decimal


@dataclass(frozen=True)
class FarmPayrollSummary:
    farm_id: int | None
    farm_label: str
    collaborator_count: int
    total_amount: Decimal


@dataclass(frozen=True)
class PayrollSummary:
    period: PayrollPeriodInfo
    entries: Sequence[PayrollEntry]
    totals_by_job_type: Sequence[JobTypePayrollSummary]
    totals_by_farm: Sequence[FarmPayrollSummary]
    overall_total: Decimal


def build_payroll_summary(
    *,
    period: PayrollPeriodInfo,
    bonified_rest_tokens: Iterable[str] | None = None,
    bonified_idle_tokens: Iterable[str] | None = None,
    overrides: Mapping[int, PayrollOverrideData] | None = None,
) -> PayrollSummary:
    bonified_tokens = {token for token in (bonified_rest_tokens or []) if token}
    bonified_idle_tokens = {token for token in (bonified_idle_tokens or []) if token}
    override_map = overrides or {}

    assignments = list(
        ShiftAssignment.objects.filter(date__range=(period.start_date, period.end_date))
        .select_related("operator", "position", "position__farm")
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

    date_list = [period.start_date + timedelta(days=offset) for offset in range(period.days)]

    operator_ids: set[int] = set()
    for assignment in assignments:
        if assignment.operator_id:
            operator_ids.add(assignment.operator_id)
    for rest in rest_periods:
        if rest.operator_id:
            operator_ids.add(rest.operator_id)

    salary_operator_ids = set(
        OperatorSalary.objects.filter(
            effective_from__lte=period.end_date,
        )
        .filter(models.Q(effective_until__isnull=True) | models.Q(effective_until__gte=period.start_date))
        .values_list("operator_id", flat=True)
    )
    operator_ids.update(salary_operator_ids)

    if not operator_ids:
        return PayrollSummary(
            period=period,
            entries=[],
            totals_by_job_type=[],
            totals_by_farm=[],
            overall_total=Decimal("0.00"),
        )

    operators = list(
        UserProfile.objects.filter(pk__in=operator_ids)
        .prefetch_related(
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
    assignment_dates_by_operator: dict[int, set[date]] = defaultdict(set)
    job_type_counter: dict[int, Counter[str]] = defaultdict(Counter)
    farm_counter: dict[int, Counter[int | None]] = defaultdict(Counter)
    farm_label_map: dict[int, str] = {}
    for assignment in assignments:
        if assignment.operator_id in assignments_by_operator:
            assignments_by_operator[assignment.operator_id].append(assignment)
            position = assignment.position
            if position:
                job_type_value = position.job_type or PositionJobType.PRODUCTION
                job_type_counter[assignment.operator_id][job_type_value] += 1
                farm_id = position.farm_id
                farm_counter[assignment.operator_id][farm_id] += 1
                if farm_id and position.farm:
                    farm_label_map[farm_id] = position.farm.name
            assignment_dates_by_operator[assignment.operator_id].add(assignment.date)

    rest_days_by_operator: dict[int, list[PayrollRestDetail]] = {pk: [] for pk in relevant_operator_ids}

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
                    is_extra=False,
                    is_bonified=False,
                )
            )
            current += timedelta(days=1)

    entries: list[PayrollEntry] = []

    job_type_by_operator: dict[int, str | None] = {}
    job_type_label_by_operator: dict[int, str] = {}
    farm_id_by_operator: dict[int, int | None] = {}
    farm_label_by_operator: dict[int, str] = {}

    for operator_id in relevant_operator_ids:
        job_type_value = _resolve_primary_job_type(job_type_counter.get(operator_id))
        job_type_by_operator[operator_id] = job_type_value
        job_type_label_by_operator[operator_id] = _job_type_label(job_type_value)
        farm_id_value = _resolve_primary_farm(farm_counter.get(operator_id))
        farm_id_by_operator[operator_id] = farm_id_value
        farm_label_by_operator[operator_id] = _farm_label(farm_id_value, farm_label_map)

    sorted_operator_ids = sorted(
        relevant_operator_ids,
        key=lambda pk: (
            job_type_label_by_operator.get(pk, "Sin tipo").lower(),
            (operator_map.get(pk).apellidos or "").lower(),
            (operator_map.get(pk).nombres or "").lower(),
            pk,
        ),
    )

    for operator_id in sorted_operator_ids:
        operator = operator_map.get(operator_id)
        if not operator or operator.is_staff:
            continue

        job_type_value = job_type_by_operator.get(operator_id)
        job_type_label = job_type_label_by_operator.get(operator_id, "Sin tipo")
        farm_id_value = farm_id_by_operator.get(operator_id)
        farm_label = farm_label_by_operator.get(operator_id, "Otros")
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

        raw_rest_details = rest_days_by_operator.get(operator_id, [])
        raw_rest_details.sort(key=lambda item: item.date)
        allowed_paid_rests = _allowed_paid_rest_days(salary.rest_days_per_week, period.days)
        rest_details: list[PayrollRestDetail] = []
        extra_rest_count = 0
        bonified_extra_count = 0
        non_bonified_extra = 0
        extra_tokens: list[str] = []
        for index, detail in enumerate(raw_rest_details):
            is_extra = index >= allowed_paid_rests
            is_bonified = is_extra and detail.token in bonified_tokens
            rest_detail = PayrollRestDetail(
                token=detail.token,
                date=detail.date,
                status=detail.status,
                notes=detail.notes,
                is_extra=is_extra,
                is_bonified=is_bonified,
            )
            rest_details.append(rest_detail)
            if is_extra:
                extra_rest_count += 1
                extra_tokens.append(detail.token)
                if is_bonified:
                    bonified_extra_count += 1
                else:
                    non_bonified_extra += 1

        rest_days = len(rest_details)
        worked_days = len(shift_details)

        rest_dates = {detail.date for detail in rest_details}
        assignment_dates = assignment_dates_by_operator.get(operator_id, set())
        non_worked_details: list[PayrollIdleDetail] = []
        for target_date in date_list:
            if not operator.is_active_on(target_date):
                continue
            if target_date in rest_dates or target_date in assignment_dates:
                continue
            token = f"idle:{operator_id}:{target_date.isoformat()}"
            is_bonified_idle = token in bonified_idle_tokens
            non_worked_details.append(
                PayrollIdleDetail(
                    token=token,
                    date=target_date,
                    is_bonified=is_bonified_idle,
                )
            )

        non_worked_count = len(non_worked_details)
        bonified_non_worked_count = sum(1 for detail in non_worked_details if detail.is_bonified)
        discounted_non_worked_count = non_worked_count - bonified_non_worked_count

        if salary.payment_type == OperatorSalary.PaymentType.MONTHLY:
            base_amount = (salary.amount / Decimal("2"))
            per_day_value = (base_amount / Decimal(period.days)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            deduction_units = non_bonified_extra + discounted_non_worked_count
            deduction = (per_day_value * Decimal(deduction_units)).quantize(
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
                job_type=job_type_value,
                job_type_label=job_type_label,
                farm_id=farm_id_value,
                farm_label=farm_label,
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
                non_worked_details=non_worked_details,
                non_worked_count=non_worked_count,
                bonified_non_worked_count=bonified_non_worked_count,
                discounted_non_worked_count=discounted_non_worked_count,
                base_amount=base_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                deduction_amount=deduction,
                suggested_amount=suggested,
                final_amount=final_amount,
                override_amount=override_amount,
                override_note=override_note,
                extra_rest_tokens=extra_tokens,
            )
        )

    job_type_totals_raw: dict[str | None, dict[str, Any]] = {}
    farm_totals_raw: dict[str, dict[str, Any]] = {}

    for entry in entries:
        job_type_key = entry.job_type or "__none__"
        job_bucket = job_type_totals_raw.setdefault(
            job_type_key,
            {
                "job_type": entry.job_type,
                "job_type_label": entry.job_type_label,
                "collaborator_count": 0,
                "total_amount": Decimal("0.00"),
            },
        )
        job_bucket["collaborator_count"] += 1
        job_bucket["total_amount"] += entry.final_amount

        farm_key = str(entry.farm_id) if entry.farm_id is not None else "__none__"
        farm_bucket = farm_totals_raw.setdefault(
            farm_key,
            {
                "farm_id": entry.farm_id,
                "farm_label": entry.farm_label,
                "collaborator_count": 0,
                "total_amount": Decimal("0.00"),
            },
        )
        farm_bucket["collaborator_count"] += 1
        farm_bucket["total_amount"] += entry.final_amount

    totals_by_job_type = [
        JobTypePayrollSummary(
            job_type=bucket["job_type"],
            job_type_label=bucket["job_type_label"],
            collaborator_count=bucket["collaborator_count"],
            total_amount=bucket["total_amount"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )
        for bucket in job_type_totals_raw.values()
    ]
    totals_by_job_type.sort(key=lambda item: item.job_type_label.lower())

    totals_by_farm = [
        FarmPayrollSummary(
            farm_id=bucket["farm_id"],
            farm_label=bucket["farm_label"],
            collaborator_count=bucket["collaborator_count"],
            total_amount=bucket["total_amount"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )
        for bucket in farm_totals_raw.values()
    ]
    totals_by_farm.sort(key=lambda item: item.farm_label.lower())
    overall_total = sum((entry.final_amount for entry in entries), Decimal("0.00")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return PayrollSummary(
        period=period,
        entries=entries,
        totals_by_job_type=totals_by_job_type,
        totals_by_farm=totals_by_farm,
        overall_total=overall_total,
    )


def _allowed_paid_rest_days(rest_days_per_week: int | None, period_days: int) -> int:
    if rest_days_per_week is None:
        rest_days_per_week = 1
    baseline = max(0, rest_days_per_week)
    if period_days <= 0:
        return 0
    return int((baseline * period_days) // 7)


def _resolve_primary_job_type(counter: Counter[str] | None) -> str | None:
    if not counter:
        return None
    return max(counter.items(), key=lambda item: (item[1], item[0] or ""))[0]


def _resolve_primary_farm(counter: Counter[int | None] | None) -> int | None:
    if not counter:
        return None
    return max(counter.items(), key=lambda item: (item[1], item[0] if item[0] is not None else -1))[0]


def _job_type_label(value: str | None) -> str:
    if value:
        try:
            return PositionJobType(value).label
        except ValueError:
            return value
    return "Sin tipo"


def _farm_label(farm_id: int | None, farm_label_map: Mapping[int, str]) -> str:
    if farm_id is None:
        return "Otros"
    return farm_label_map.get(farm_id, "Otros")


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
