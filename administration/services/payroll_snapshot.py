from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from personal.models import UserProfile

from .payroll import (
    FarmPayrollSummary,
    JobTypePayrollSummary,
    PayrollIdleDetail,
    PayrollOverrideData,
    PayrollPeriodInfo,
    PayrollRestDetail,
    PayrollShiftDetail,
    PayrollSummary,
    PayrollEntry,
)

SNAPSHOT_VERSION = 1


def serialize_payroll_summary(summary: PayrollSummary) -> dict[str, Any]:
    return {
        "version": SNAPSHOT_VERSION,
        "period": _serialize_period(summary.period),
        "entries": [_serialize_entry(entry) for entry in summary.entries],
        "totals_by_job_type": [_serialize_total(total) for total in summary.totals_by_job_type],
        "totals_by_farm": [_serialize_farm_total(total) for total in summary.totals_by_farm],
        "overall_total": _serialize_decimal(summary.overall_total),
    }


def deserialize_payroll_summary(payload: Mapping[str, Any]) -> PayrollSummary:
    period_data = payload.get("period")
    if not period_data:
        raise ValueError("Payload is missing period information.")
    period = PayrollPeriodInfo(
        start_date=date.fromisoformat(period_data["start_date"]),
        end_date=date.fromisoformat(period_data["end_date"]),
        label=period_data["label"],
        month_name=period_data["month_name"],
        year=int(period_data["year"]),
        days=int(period_data["days"]),
        half_label=period_data["half_label"],
    )
    entry_payloads: Sequence[Mapping[str, Any]] = payload.get("entries", [])
    operator_ids = [entry["operator"]["id"] for entry in entry_payloads if "operator" in entry]
    operator_map = _load_operator_map(operator_ids)
    entries = [_deserialize_entry(entry, operator_map) for entry in entry_payloads]

    job_totals = [
        JobTypePayrollSummary(
            job_type=total["job_type"],
            job_type_label=total["job_type_label"],
            collaborator_count=int(total["collaborator_count"]),
            total_amount=Decimal(total["total_amount"]),
        )
        for total in payload.get("totals_by_job_type", [])
    ]
    farm_totals = [
        FarmPayrollSummary(
            farm_id=total["farm_id"],
            farm_label=total["farm_label"],
            collaborator_count=int(total["collaborator_count"]),
            total_amount=Decimal(total["total_amount"]),
        )
        for total in payload.get("totals_by_farm", [])
    ]
    overall_total = Decimal(payload.get("overall_total", "0"))
    return PayrollSummary(
        period=period,
        entries=entries,
        totals_by_job_type=job_totals,
        totals_by_farm=farm_totals,
        overall_total=overall_total,
    )


def _serialize_period(period: PayrollPeriodInfo) -> dict[str, Any]:
    return {
        "start_date": period.start_date.isoformat(),
        "end_date": period.end_date.isoformat(),
        "label": _serialize_text(period.label),
        "month_name": _serialize_text(period.month_name),
        "year": period.year,
        "days": period.days,
        "half_label": _serialize_text(period.half_label),
    }


def _serialize_entry(entry: PayrollEntry) -> dict[str, Any]:
    return {
        "operator": {
            "id": entry.operator.pk,
            "cedula": entry.operator.cedula,
            "nombres": entry.operator.nombres,
            "apellidos": entry.operator.apellidos,
        },
        "job_type": entry.job_type,
        "job_type_label": _serialize_text(entry.job_type_label),
        "farm_id": entry.farm_id,
        "farm_label": _serialize_text(entry.farm_label),
        "payment_type": entry.payment_type,
        "payment_type_label": _serialize_text(entry.payment_type_label),
        "salary_amount": _serialize_decimal(entry.salary_amount),
        "worked_days": entry.worked_days,
        "rest_days": entry.rest_days,
        "extra_rest_count": entry.extra_rest_count,
        "bonified_extra_count": entry.bonified_extra_count,
        "discounted_extra_count": entry.discounted_extra_count,
        "non_worked_count": entry.non_worked_count,
        "bonified_non_worked_count": entry.bonified_non_worked_count,
        "discounted_non_worked_count": entry.discounted_non_worked_count,
        "base_amount": _serialize_decimal(entry.base_amount),
        "deduction_amount": _serialize_decimal(entry.deduction_amount),
        "suggested_amount": _serialize_decimal(entry.suggested_amount),
        "final_amount": _serialize_decimal(entry.final_amount),
        "override_amount": (
            _serialize_decimal(entry.override_amount) if entry.override_amount is not None else None
        ),
        "override_note": _serialize_text(entry.override_note),
        "rest_details": [_serialize_rest_detail(detail) for detail in entry.rest_details],
        "shift_details": [_serialize_shift_detail(detail) for detail in entry.shift_details],
        "non_worked_details": [_serialize_idle_detail(detail) for detail in entry.non_worked_details],
        "extra_rest_tokens": list(entry.extra_rest_tokens),
    }


def _serialize_total(total: JobTypePayrollSummary) -> dict[str, Any]:
    return {
        "job_type": total.job_type,
        "job_type_label": _serialize_text(total.job_type_label),
        "collaborator_count": total.collaborator_count,
        "total_amount": _serialize_decimal(total.total_amount),
    }


def _serialize_farm_total(total: FarmPayrollSummary) -> dict[str, Any]:
    return {
        "farm_id": total.farm_id,
        "farm_label": _serialize_text(total.farm_label),
        "collaborator_count": total.collaborator_count,
        "total_amount": _serialize_decimal(total.total_amount),
    }


def _serialize_rest_detail(detail: PayrollRestDetail) -> dict[str, Any]:
    return {
        "token": detail.token,
        "date": detail.date.isoformat(),
        "status": _serialize_text(detail.status),
        "notes": _serialize_text(detail.notes),
        "is_extra": detail.is_extra,
        "is_bonified": detail.is_bonified,
    }


def _serialize_shift_detail(detail: PayrollShiftDetail) -> dict[str, Any]:
    return {
        "date": detail.date.isoformat(),
        "position_label": _serialize_text(detail.position_label),
        "is_overtime": detail.is_overtime,
    }


def _serialize_idle_detail(detail: PayrollIdleDetail) -> dict[str, Any]:
    return {
        "token": detail.token,
        "date": detail.date.isoformat(),
        "is_bonified": detail.is_bonified,
    }


def _serialize_decimal(value: Decimal | None) -> str:
    return str(value if value is not None else Decimal("0"))


def _serialize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _deserialize_entry(entry: Mapping[str, Any], operator_map: dict[int, UserProfile]) -> PayrollEntry:
    operator_payload = entry["operator"]
    operator = operator_map.get(operator_payload["id"])
    if not operator:
        operator = UserProfile(
            pk=operator_payload["id"],
            cedula=operator_payload.get("cedula") or "",
            nombres=operator_payload.get("nombres") or "",
            apellidos=operator_payload.get("apellidos") or "",
            telefono=operator_payload.get("telefono") or "",
        )
    rest_details = [
        PayrollRestDetail(
            token=detail["token"],
            date=date.fromisoformat(detail["date"]),
            status=detail["status"],
            notes=detail.get("notes") or "",
            is_extra=detail.get("is_extra", False),
            is_bonified=detail.get("is_bonified", False),
        )
        for detail in entry.get("rest_details", [])
    ]
    shift_details = [
        PayrollShiftDetail(
            date=date.fromisoformat(detail["date"]),
            position_label=detail["position_label"],
            is_overtime=detail.get("is_overtime", False),
        )
        for detail in entry.get("shift_details", [])
    ]
    idle_details = [
        PayrollIdleDetail(
            token=detail["token"],
            date=date.fromisoformat(detail["date"]),
            is_bonified=detail.get("is_bonified", False),
        )
        for detail in entry.get("non_worked_details", [])
    ]
    override_amount_raw = entry.get("override_amount")
    override_amount = Decimal(override_amount_raw) if override_amount_raw not in (None, "") else None
    return PayrollEntry(
        operator=operator,
        job_type=entry.get("job_type"),
        job_type_label=entry.get("job_type_label") or "",
        farm_id=entry.get("farm_id"),
        farm_label=entry.get("farm_label") or "Otros",
        payment_type=entry.get("payment_type"),
        payment_type_label=entry.get("payment_type_label") or "",
        salary_amount=Decimal(entry.get("salary_amount", "0")),
        worked_days=int(entry.get("worked_days", 0)),
        rest_days=int(entry.get("rest_days", 0)),
        rest_details=rest_details,
        shift_details=shift_details,
        extra_rest_count=int(entry.get("extra_rest_count", 0)),
        bonified_extra_count=int(entry.get("bonified_extra_count", 0)),
        discounted_extra_count=int(entry.get("discounted_extra_count", 0)),
        non_worked_details=idle_details,
        non_worked_count=int(entry.get("non_worked_count", 0)),
        bonified_non_worked_count=int(entry.get("bonified_non_worked_count", 0)),
        discounted_non_worked_count=int(entry.get("discounted_non_worked_count", 0)),
        base_amount=Decimal(entry.get("base_amount", "0")),
        deduction_amount=Decimal(entry.get("deduction_amount", "0")),
        suggested_amount=Decimal(entry.get("suggested_amount", "0")),
        final_amount=Decimal(entry.get("final_amount", "0")),
        override_amount=override_amount,
        override_note=entry.get("override_note") or "",
        extra_rest_tokens=entry.get("extra_rest_tokens", []),
    )


def _load_operator_map(operator_ids: Iterable[int]) -> dict[int, UserProfile]:
    unique_ids = {operator_id for operator_id in operator_ids if operator_id}
    if not unique_ids:
        return {}
    operators = UserProfile.objects.filter(pk__in=unique_ids)
    return {operator.pk: operator for operator in operators}


__all__ = [
    "serialize_payroll_summary",
    "deserialize_payroll_summary",
]
