from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, TypedDict
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Avg, Count, Max, Prefetch, Q, Sum
from django.db.models.functions import Coalesce, TruncWeek
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import DeleteView, TemplateView, UpdateView

from applacolina.mixins import StaffRequiredMixin
from production.forms import BatchDistributionForm, BirdBatchForm, ChickenHouseForm, FarmForm, RoomForm
from production.models import (
    BirdBatch,
    BirdBatchRoomAllocation,
    ChickenHouse,
    Farm,
    ProductionRecord,
    Room,
    WeightSampleSession,
)
from production.services.reference_tables import get_reference_targets


class ScorecardMetric(TypedDict):
    label: str
    value: str
    delta: float
    is_positive: bool
    description: str


class MortalityRecord(TypedDict):
    label: str
    quantity: int
    percentage: float


class WeightTrendPoint(TypedDict):
    week: int
    actual_weight: float
    projected_weight: float


class ConsumptionRecord(TypedDict):
    week: int
    feed_kg: float
    grams_per_bird: float


class EggSizeRecord(TypedDict):
    size: str
    percentage: float
    avg_weight: float


class BarnAllocation(TypedDict):
    name: str
    segment: str
    initial_birds: int
    current_birds: int
    occupancy_rate: float
    feed_today_grams: Optional[float]
    weekly_feed_kg: float
    mortality_week: int
    mortality_percentage: float
    mortality_cumulative: int
    mortality_cumulative_percentage: float
    last_update: Optional[date]


class LotOverview(TypedDict):
    label: str
    breed: str
    birth_date: date
    age_weeks: int
    initial_birds: int
    current_birds: int
    bird_balance: int
    barn_count: int
    barn_names_display: str
    uniformity: Optional[float]
    avg_weight: Optional[float]
    target_weight: Optional[float]
    feed_today_grams: Optional[float]
    weekly_feed_kg: float
    total_feed_to_date_kg: float
    barns: List[BarnAllocation]
    mortality: List[MortalityRecord]
    weight_trend: List[WeightTrendPoint]
    consumption_history: List[ConsumptionRecord]
    egg_mix: List[EggSizeRecord]
    alerts: List[str]
    notes: str
    daily_snapshot: List["DailySnapshotMetric"]


class DailySnapshotMetric(TypedDict):
    label: str
    slug: str
    unit: str
    decimals: int
    actual: Optional[float]
    target: Optional[float]
    delta: Optional[float]
    status: Optional[str]


class FarmSummary(TypedDict):
    total_initial_birds: int
    current_birds: int
    mortality_percent: float
    weekly_consumption: float
    average_uniformity: Optional[float]
    average_weight: Optional[float]
    lot_count: int


class FarmOverview(TypedDict):
    name: str
    code: str
    summary: FarmSummary
    lots: List[LotOverview]


class BatchAllocationSummary(TypedDict):
    id: int
    room_name: str
    chicken_house_name: str
    quantity: int


class BatchCard(TypedDict):
    id: int
    label: str
    farm_name: str
    status: str
    status_label: str
    birth_date: date
    age_weeks: int
    age_days: int
    initial_quantity: int
    allocated_quantity: int
    remaining_quantity: int
    allocations: List[BatchAllocationSummary]


class BatchMetrics(TypedDict):
    total_batches: int
    active_batches: int
    inactive_batches: int
    total_initial_birds: int
    total_assigned_birds: int
    total_rooms_used: int


class FilterConfig(TypedDict):
    farms: List[str]
    barns: List[str]
    ranges: List[str]
    breeds: List[str]
    egg_sizes: List[str]


class UpcomingMilestone(TypedDict):
    title: str
    detail: str
    due_on: date


class ProductionHomeView(StaffRequiredMixin, TemplateView):
    """Render the landing page for the poultry production module."""

    template_name = "production/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        today = timezone.localdate()
        week_start = today - timedelta(days=6)
        four_week_start = today - timedelta(days=27)
        year_start = date(today.year, 1, 1)
        history_start = today - timedelta(days=28)

        batches = list(
            BirdBatch.objects.filter(status=BirdBatch.Status.ACTIVE)
            .select_related("farm")
            .prefetch_related(
                Prefetch(
                    "allocations",
                    queryset=BirdBatchRoomAllocation.objects.select_related("room__chicken_house"),
                )
            )
        )

        if not batches:
            context.update(
                {
                    "farms": [],
                    "global_metrics": [
                        ScorecardMetric(
                            label="Aves activas",
                            value="0",
                            delta=0.0,
                            is_positive=True,
                            description="Sin lotes activos registrados.",
                        ),
                        ScorecardMetric(
                            label="Consumo semanal total (kg)",
                            value="0",
                            delta=0.0,
                            is_positive=True,
                            description="No hay consumo registrado.",
                        ),
                        ScorecardMetric(
                            label="Uniformidad promedio",
                            value="--",
                            delta=0.0,
                            is_positive=True,
                            description="Sin datos de sesiones de pesaje.",
                        ),
                        ScorecardMetric(
                            label="Peso vivo promedio (kg)",
                            value="--",
                            delta=0.0,
                            is_positive=True,
                            description="Sin datos de peso registrados.",
                        ),
                    ],
                    "filters": FilterConfig(
                        farms=[],
                        barns=[],
                        ranges=[
                            "Últimas 4 semanas",
                            "Últimos 3 meses",
                            "Ciclo completo",
                            "Personalizado…",
                        ],
                        breeds=[],
                        egg_sizes=["Huevos pequeños", "M", "L", "XL", "Jumbo", "Doble yema"],
                    ),
                    "upcoming_milestones": [],
                    "total_lots": 0,
                    "total_barn_allocations": 0,
                    "total_initial_birds": 0,
                    "total_current_birds": 0,
                    "total_feed_to_date": 0,
                    "dashboard_generated_at": timezone.now(),
                }
            )
            return context

        batch_ids = [batch.id for batch in batches]
        batch_id_set = set(batch_ids)

        room_batch_map: Dict[int, set[int]] = defaultdict(set)
        for batch in batches:
            for allocation in batch.allocations.all():
                room_batch_map[allocation.room_id].add(batch.id)
        room_ids = set(room_batch_map.keys())

        production_aggregates = (
            ProductionRecord.objects.filter(bird_batch_id__in=batch_ids)
            .values("bird_batch_id")
            .annotate(
                total_consumption=Coalesce(Sum("consumption"), Decimal("0")),
                weekly_consumption=Coalesce(
                    Sum("consumption", filter=Q(date__range=(week_start, today))), Decimal("0")
                ),
                total_mortality=Coalesce(Sum("mortality"), 0),
                total_discard=Coalesce(Sum("discard"), 0),
                weekly_mortality=Coalesce(
                    Sum("mortality", filter=Q(date__range=(week_start, today))), 0
                ),
                four_week_mortality=Coalesce(
                    Sum("mortality", filter=Q(date__range=(four_week_start, today))), 0
                ),
                yearly_mortality=Coalesce(Sum("mortality", filter=Q(date__gte=year_start)), 0),
                latest_record_date=Max("date"),
            )
        )
        production_map = {entry["bird_batch_id"]: entry for entry in production_aggregates}

        latest_record_map: Dict[int, ProductionRecord] = {}
        for record in (
            ProductionRecord.objects.filter(bird_batch_id__in=batch_ids)
            .order_by("-date", "-id")
            .iterator()
        ):
            if record.bird_batch_id not in latest_record_map:
                latest_record_map[record.bird_batch_id] = record
            if len(latest_record_map) == len(batch_ids):
                break

        daily_records_map: Dict[int, ProductionRecord] = {
            record.bird_batch_id: record
            for record in ProductionRecord.objects.filter(
                bird_batch_id__in=batch_ids, date=today
            ).select_related("bird_batch")
        }

        weekly_history_map: Dict[int, List[Dict[str, object]]] = defaultdict(list)
        weekly_history_qs = (
            ProductionRecord.objects.filter(bird_batch_id__in=batch_ids, date__gte=history_start)
            .annotate(week_start=TruncWeek("date"))
            .values("bird_batch_id", "week_start")
            .annotate(feed_kg=Coalesce(Sum("consumption"), Decimal("0")))
            .order_by("bird_batch_id", "-week_start")
        )
        for entry in weekly_history_qs:
            week_start_date = entry["week_start"]
            if not week_start_date:
                continue
            batch_id = entry["bird_batch_id"]
            if len(weekly_history_map[batch_id]) >= 4:
                continue
            weekly_history_map[batch_id].append(
                {
                    "week_start": week_start_date,
                    "week": week_start_date.isocalendar().week,
                    "feed_kg": float(entry["feed_kg"] or 0),
                }
            )

        weight_sessions_qs = (
            WeightSampleSession.objects.filter(
                Q(production_record__bird_batch_id__in=batch_ids) | Q(room_id__in=room_ids)
            )
            .select_related("production_record__bird_batch", "room__chicken_house")
            .prefetch_related("room__allocations")
            .order_by("-date", "-id")
        )

        weight_data: Dict[int, Dict[str, object]] = defaultdict(
            lambda: {
                "uniformity_weight_sum": Decimal("0"),
                "uniformity_sample_sum": 0,
                "weight_weight_sum": Decimal("0"),
                "weight_sample_sum": 0,
                "sessions": [],
            }
        )
        room_last_update_map: Dict[tuple[int, int], date] = {}

        for session in weight_sessions_qs:
            batch_id: Optional[int] = None
            if session.production_record_id and session.production_record.bird_batch_id in batch_id_set:
                batch_id = session.production_record.bird_batch_id
            else:
                room_batches = room_batch_map.get(session.room_id)
                if room_batches and len(room_batches) == 1:
                    batch_id = next(iter(room_batches))

            if batch_id is None or batch_id not in batch_id_set:
                continue

            session_data = weight_data[batch_id]
            sample_size = session.sample_size or 0

            if sample_size > 0:
                if session.uniformity_percent is not None:
                    session_data["uniformity_weight_sum"] += Decimal(session.uniformity_percent) * sample_size
                    session_data["uniformity_sample_sum"] += sample_size
                if session.average_grams is not None:
                    session_data["weight_weight_sum"] += Decimal(session.average_grams) * sample_size
                    session_data["weight_sample_sum"] += sample_size

            if session.average_grams is not None and len(session_data["sessions"]) < 3:
                session_data["sessions"].append(session)

            key = (batch_id, session.room_id)
            if key not in room_last_update_map or session.date > room_last_update_map[key]:
                room_last_update_map[key] = session.date

        def evaluate_delta(
            actual_value: Optional[float],
            target_value: Optional[float],
            tolerance_value: float,
        ) -> Tuple[Optional[float], Optional[str]]:
            if actual_value is None or target_value is None:
                return None, None
            delta_value = round(actual_value - target_value, 2)
            if abs(delta_value) <= tolerance_value:
                status = "ok"
            elif delta_value > 0:
                status = "high"
            else:
                status = "low"
            return delta_value, status

        farms_map: Dict[int, Dict[str, object]] = {}
        all_lots: List[LotOverview] = []
        total_barn_allocations = 0

        for batch in batches:
            stats = production_map.get(batch.id, {})
            total_mortality = int(stats.get("total_mortality", 0) or 0)
            total_discard = int(stats.get("total_discard", 0) or 0)
            bird_balance = total_mortality + total_discard
            current_birds = max(batch.initial_quantity - bird_balance, 0)

            weekly_consumption = float(stats.get("weekly_consumption") or Decimal("0"))
            total_consumption = float(stats.get("total_consumption") or Decimal("0"))
            weekly_mortality = int(stats.get("weekly_mortality", 0) or 0)
            four_week_mortality = int(stats.get("four_week_mortality", 0) or 0)
            yearly_mortality = int(stats.get("yearly_mortality", 0) or 0)
            latest_record_date = stats.get("latest_record_date")

            uniformity_info = weight_data.get(batch.id)
            uniformity = None
            avg_weight = None
            trend_sessions = []
            if uniformity_info:
                if uniformity_info["uniformity_sample_sum"]:
                    uniformity = float(
                        uniformity_info["uniformity_weight_sum"]
                        / Decimal(uniformity_info["uniformity_sample_sum"])
                    )
                if uniformity_info["weight_sample_sum"]:
                    avg_weight = float(
                        (
                            uniformity_info["weight_weight_sum"]
                            / Decimal(uniformity_info["weight_sample_sum"])
                        )
                        / Decimal("1000")
                    )
                trend_sessions = list(uniformity_info["sessions"])

            weight_trend: List[WeightTrendPoint] = []
            for session in reversed(trend_sessions):
                if session.average_grams is None:
                    continue
                weight_kg = round(float(session.average_grams) / 1000, 2)
                weight_trend.append(
                    WeightTrendPoint(
                        week=session.date.isocalendar().week,
                        actual_weight=weight_kg,
                        projected_weight=weight_kg,
                    )
                )

            history_entries = weekly_history_map.get(batch.id, [])
            consumption_history: List[ConsumptionRecord] = []
            for entry in history_entries[:3]:
                feed_kg = entry["feed_kg"]
                grams_per_bird = (
                    round((feed_kg * 1000) / (current_birds * 7), 2)
                    if current_birds and feed_kg
                    else 0.0
                )
                consumption_history.append(
                    ConsumptionRecord(
                        week=entry["week"],
                        feed_kg=round(feed_kg, 2),
                        grams_per_bird=grams_per_bird,
                    )
                )
            consumption_history = list(reversed(consumption_history))

            latest_record = latest_record_map.get(batch.id)
            daily_record = daily_records_map.get(batch.id)
            actual_record = daily_record or latest_record

            consumption_actual = (
                float(actual_record.consumption)
                if actual_record and actual_record.consumption is not None
                else None
            )
            mortality_actual = (
                float(actual_record.mortality)
                if actual_record and actual_record.mortality is not None
                else None
            )
            discard_actual = (
                float(actual_record.discard)
                if actual_record and actual_record.discard is not None
                else None
            )
            egg_weight_actual = (
                float(actual_record.average_egg_weight)
                if actual_record and actual_record.average_egg_weight is not None
                else None
            )
            production_actual = (
                float(actual_record.production)
                if actual_record and actual_record.production is not None
                else None
            )

            reference_targets = get_reference_targets(
                batch.breed,
                (today - batch.birth_date).days // 7 if batch.birth_date else 0,
                current_birds or batch.initial_quantity,
            )

            feed_today_grams: Optional[float] = None
            if consumption_actual is not None and current_birds:
                feed_today_grams = round(consumption_actual * 1000 / current_birds, 2)

            egg_mix: List[EggSizeRecord] = []
            if egg_weight_actual is not None:
                egg_mix.append(
                    EggSizeRecord(
                        size="Promedio",
                        percentage=100.0,
                        avg_weight=egg_weight_actual,
                    )
                )

            mortality_records: List[MortalityRecord] = [
                MortalityRecord(
                    label="Semana actual",
                    quantity=weekly_mortality,
                    percentage=round(
                        (weekly_mortality / batch.initial_quantity) * 100, 2
                    )
                    if batch.initial_quantity
                    else 0.0,
                ),
                MortalityRecord(
                    label="Últimas 4 semanas",
                    quantity=four_week_mortality,
                    percentage=round(
                        (four_week_mortality / batch.initial_quantity) * 100, 2
                    )
                    if batch.initial_quantity
                    else 0.0,
                ),
                MortalityRecord(
                    label="Año en curso",
                    quantity=yearly_mortality,
                    percentage=round(
                        (yearly_mortality / batch.initial_quantity) * 100, 2
                    )
                    if batch.initial_quantity
                    else 0.0,
                ),
            ]

            daily_snapshot: List[DailySnapshotMetric] = []

            consumption_target = reference_targets["consumption_kg"]
            consumption_delta, consumption_status = evaluate_delta(
                consumption_actual,
                consumption_target,
                tolerance_value=max(consumption_target * 0.05, 5.0),
            )
            daily_snapshot.append(
                DailySnapshotMetric(
                    label="Consumo diario",
                    slug="consumption",
                    unit="kg",
                    decimals=1,
                    actual=consumption_actual,
                    target=consumption_target,
                    delta=consumption_delta,
                    status=consumption_status,
                )
            )

            mortality_target = reference_targets["mortality_birds"]
            mortality_delta, mortality_status = evaluate_delta(
                mortality_actual,
                mortality_target,
                tolerance_value=max(mortality_target * 0.25, 1.0),
            )
            daily_snapshot.append(
                DailySnapshotMetric(
                    label="Mortalidad",
                    slug="mortality",
                    unit="aves",
                    decimals=1,
                    actual=mortality_actual,
                    target=mortality_target,
                    delta=mortality_delta,
                    status=mortality_status,
                )
            )

            discard_target = reference_targets["discard_birds"]
            discard_delta, discard_status = evaluate_delta(
                discard_actual,
                discard_target,
                tolerance_value=max(discard_target * 0.25, 1.0),
            )
            daily_snapshot.append(
                DailySnapshotMetric(
                    label="Descarte",
                    slug="discard",
                    unit="aves",
                    decimals=1,
                    actual=discard_actual,
                    target=discard_target,
                    delta=discard_delta,
                    status=discard_status,
                )
            )

            egg_target = reference_targets["egg_weight_g"]
            egg_delta, egg_status = evaluate_delta(
                egg_weight_actual,
                egg_target,
                tolerance_value=1.2,
            )
            daily_snapshot.append(
                DailySnapshotMetric(
                    label="Peso huevo",
                    slug="egg_weight",
                    unit="g",
                    decimals=1,
                    actual=egg_weight_actual,
                    target=egg_target,
                    delta=egg_delta,
                    status=egg_status,
                )
            )

            production_target = reference_targets["production_percent"]
            production_delta, production_status = evaluate_delta(
                production_actual,
                production_target,
                tolerance_value=2.0,
            )
            daily_snapshot.append(
                DailySnapshotMetric(
                    label="Producción",
                    slug="production",
                    unit="%",
                    decimals=1,
                    actual=production_actual,
                    target=production_target,
                    delta=production_delta,
                    status=production_status,
                )
            )

            alerts: List[str] = []
            if consumption_status == "high" and consumption_delta is not None:
                alerts.append(
                    f"Consumo diario +{consumption_delta:.1f} kg sobre la tabla."
                )
            if consumption_status == "low" and consumption_delta is not None:
                alerts.append(
                    f"Consumo diario {consumption_delta:.1f} kg por debajo del objetivo."
                )
            if uniformity is not None and uniformity < 85:
                alerts.append(f"Uniformidad promedio {uniformity:.1f}% por debajo del objetivo.")
            if mortality_status == "high" and mortality_delta is not None:
                alerts.append(
                    f"Mortalidad diaria +{mortality_delta:.0f} aves sobre la tabla."
                )
            if discard_status == "high" and discard_delta is not None:
                alerts.append(
                    f"Descarte diario +{discard_delta:.0f} aves respecto a la tabla."
                )
            if production_status == "low" and production_delta is not None:
                alerts.append(
                    f"Producción diaria {production_delta:.1f}% por debajo del objetivo."
                )
            if batch.initial_quantity and current_birds / batch.initial_quantity < 0.9:
                alerts.append("Saldo de aves por debajo del 90% del lote inicial.")

            notes = (
                f"Último registro de producción: {actual_record.date:%d %b %Y}."
                if actual_record
                else "Sin registros de producción disponibles."
            )

            barns_list: List[BarnAllocation] = []
            barn_names: List[str] = []

            for allocation in batch.allocations.all():
                share = allocation.quantity / batch.initial_quantity if batch.initial_quantity else 0
                allocation_current = int(round(current_birds * share))
                occupancy_rate = (
                    round((allocation_current / allocation.quantity) * 100, 1)
                    if allocation.quantity
                    else 0.0
                )
                weekly_feed_alloc = round(weekly_consumption * share, 2)
                mortality_week_alloc = int(round(weekly_mortality * share))
                mortality_percentage_alloc = (
                    round((mortality_week_alloc / allocation.quantity) * 100, 2)
                    if allocation.quantity
                    else 0.0
                )
                mortality_cumulative_alloc = int(round(total_mortality * share))
                mortality_cumulative_percentage_alloc = (
                    round((mortality_cumulative_alloc / allocation.quantity) * 100, 2)
                    if allocation.quantity
                    else 0.0
                )
                last_update = room_last_update_map.get(
                    (batch.id, allocation.room_id), latest_record_date
                )
                barns_list.append(
                    BarnAllocation(
                        name=allocation.room.chicken_house.name,
                        segment=allocation.room.name,
                        initial_birds=allocation.quantity,
                        current_birds=allocation_current,
                        occupancy_rate=occupancy_rate,
                        feed_today_grams=feed_today_grams,
                        weekly_feed_kg=weekly_feed_alloc,
                        mortality_week=mortality_week_alloc,
                        mortality_percentage=mortality_percentage_alloc,
                        mortality_cumulative=mortality_cumulative_alloc,
                        mortality_cumulative_percentage=mortality_cumulative_percentage_alloc,
                        last_update=last_update,
                    )
                )
                barn_names.append(f"{allocation.room.chicken_house.name} · {allocation.room.name}")

            total_barn_allocations += len(barns_list)
            barn_names_display = ", ".join(barn_names) if barn_names else "Sin asignación"

            lot_data: LotOverview = {
                "label": f"Lote #{batch.id}",
                "breed": batch.breed,
                "birth_date": batch.birth_date,
                "age_weeks": (today - batch.birth_date).days // 7 if batch.birth_date else 0,
                "initial_birds": batch.initial_quantity,
                "current_birds": current_birds,
                "bird_balance": batch.initial_quantity - current_birds,
                "barn_count": len(barns_list),
                "barn_names_display": barn_names_display,
                "uniformity": round(uniformity, 2) if uniformity is not None else None,
                "avg_weight": round(avg_weight, 2) if avg_weight is not None else None,
                "target_weight": None,
                "feed_today_grams": feed_today_grams,
                "weekly_feed_kg": round(weekly_consumption, 2),
                "total_feed_to_date_kg": round(total_consumption, 2),
                "barns": barns_list,
                "mortality": mortality_records,
                "weight_trend": weight_trend,
                "consumption_history": consumption_history,
                "egg_mix": egg_mix,
                "alerts": alerts,
                "notes": notes,
                "daily_snapshot": daily_snapshot,
            }

            all_lots.append(lot_data)
            farm_bucket = farms_map.setdefault(
                batch.farm_id,
                {
                    "farm": batch.farm,
                    "lots": [],
                },
            )
            farm_bucket["lots"].append(lot_data)

        farms_context: List[FarmOverview] = []
        for farm_id, info in farms_map.items():
            farm = info["farm"]
            lots = info["lots"]
            total_initial = sum(lot["initial_birds"] for lot in lots)
            total_current = sum(lot["current_birds"] for lot in lots)
            weekly_consumption_sum = round(sum(lot["weekly_feed_kg"] for lot in lots), 2)
            mortality_percent = (
                round(((total_initial - total_current) / total_initial) * 100, 2)
                if total_initial
                else 0.0
            )
            uniformity_values = [lot["uniformity"] for lot in lots if lot["uniformity"] is not None]
            average_uniformity = (
                round(sum(uniformity_values) / len(uniformity_values), 2)
                if uniformity_values
                else None
            )
            weight_values = [lot["avg_weight"] for lot in lots if lot["avg_weight"] is not None]
            average_weight = (
                round(sum(weight_values) / len(weight_values), 2)
                if weight_values
                else None
            )

            farm_summary: FarmSummary = {
                "total_initial_birds": total_initial,
                "current_birds": total_current,
                "mortality_percent": mortality_percent,
                "weekly_consumption": weekly_consumption_sum,
                "average_uniformity": average_uniformity,
                "average_weight": average_weight,
                "lot_count": len(lots),
            }
            farms_context.append(
                {
                    "name": farm.name,
                    "code": f"F-{farm.id}",
                    "summary": farm_summary,
                    "lots": lots,
                }
            )
        farms_context.sort(key=lambda farm: farm["name"])

        total_lots = len(all_lots)
        total_initial_birds = sum(lot["initial_birds"] for lot in all_lots)
        total_current_birds = sum(lot["current_birds"] for lot in all_lots)
        total_weekly_feed = round(sum(lot["weekly_feed_kg"] for lot in all_lots), 2)
        total_feed_to_date = round(sum(lot["total_feed_to_date_kg"] for lot in all_lots), 2)

        global_metrics: List[ScorecardMetric] = [
            ScorecardMetric(
                label="Aves activas",
                value=f"{total_current_birds:,}",
                delta=0.0,
                is_positive=True,
                description="Inventario vivo total en lotes activos.",
            ),
            ScorecardMetric(
                label="Consumo semanal total (kg)",
                value=f"{total_weekly_feed:,.1f}",
                delta=0.0,
                is_positive=True,
                description="Suma del consumo registrado en los últimos 7 días.",
            ),
        ]

        farm_names = sorted({farm["name"] for farm in farms_context})
        barn_filter_names = sorted(
            {
                f'{allocation["name"]} · {farm["name"]}'
                for farm in farms_context
                for lot in farm["lots"]
                for allocation in lot["barns"]
            }
        )
        breed_names = sorted({lot["breed"] for lot in all_lots})

        filters = FilterConfig(
            farms=farm_names,
            barns=barn_filter_names,
            ranges=[
                "Últimas 4 semanas",
                "Últimos 3 meses",
                "Ciclo completo",
                "Personalizado…",
            ],
            breeds=breed_names,
            egg_sizes=["Huevos pequeños", "M", "L", "XL", "Jumbo", "Doble yema"],
        )

        upcoming_milestones: List[UpcomingMilestone] = []
        for farm in farms_context:
            for lot in farm["lots"]:
                latest_updates = [
                    allocation["last_update"] for allocation in lot["barns"] if allocation["last_update"]
                ]
                if not latest_updates:
                    continue
                next_due = max(latest_updates) + timedelta(days=7)
                upcoming_milestones.append(
                    UpcomingMilestone(
                        title=f"Seguimiento de pesaje {lot['label']}",
                        detail=f"{farm['name']} · {lot['barn_names_display']}",
                        due_on=next_due,
                    )
                )
        upcoming_milestones.sort(key=lambda milestone: milestone["due_on"])
        upcoming_milestones = upcoming_milestones[:3]

        context.update(
            {
                "farms": farms_context,
                "global_metrics": global_metrics,
                "filters": filters,
                "upcoming_milestones": upcoming_milestones,
                "total_lots": total_lots,
                "total_barn_allocations": total_barn_allocations,
                "total_initial_birds": total_initial_birds,
                "total_current_birds": total_current_birds,
                "total_feed_to_date": total_feed_to_date,
                "dashboard_generated_at": timezone.now(),
                "active_submenu": "overview",
            }
        )
        return context


class BatchManagementView(StaffRequiredMixin, TemplateView):
    """Manage bird batches and their room allocations."""

    template_name = "production/batches.html"
    form_registry = {
        "batch": ("_batch_form", BirdBatchForm),
    }

    def post(self, request, *args, **kwargs):
        form_type = request.POST.get("form_type", "")

        if form_type == "distribution":
            return self._handle_distribution_post(request)

        registry_entry = self.form_registry.get(form_type)
        if not registry_entry:
            messages.error(request, "No se pudo determinar el formulario enviado.")
            return redirect("production:batches")

        form_attr, form_class = registry_entry
        form = form_class(request.POST)
        setattr(self, form_attr, form)

        if form.is_valid():
            instance = form.save()
            messages.success(
                request,
                f'Se registró el lote #{instance.pk} para {instance.farm.name}. Desde este panel puedes distribuirlo en salones.',
            )
            return redirect(f"{reverse_lazy('production:batches')}?batch={instance.pk}")

        return self.get(request, *args, **kwargs)

    def _handle_distribution_post(self, request):
        batch_id = self._safe_pk_lookup(BirdBatch, request.POST.get("batch_id"))
        if not batch_id:
            messages.error(request, "No fue posible identificar el lote seleccionado.")
            return redirect("production:batches")

        allocations_prefetch = Prefetch(
            "allocations",
            queryset=BirdBatchRoomAllocation.objects.select_related("room__chicken_house"),
        )
        try:
            batch = (
                BirdBatch.objects.select_related("farm")
                .prefetch_related(allocations_prefetch)
                .get(pk=batch_id)
            )
        except BirdBatch.DoesNotExist:
            messages.error(request, "El lote seleccionado ya no existe.")
            return redirect("production:batches")

        form = BatchDistributionForm(request.POST, batch=batch)
        self._distribution_form = form
        self._focused_batch_id = batch_id

        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Distribución guardada correctamente. Las asignaciones fueron actualizadas.",
            )
            return redirect(f"{reverse_lazy('production:batches')}?batch={batch_id}")

        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        batches = self._fetch_batches()
        focused_batch_id = getattr(self, "_focused_batch_id", None)
        if focused_batch_id is None:
            focused_batch_id = self._safe_pk_lookup(BirdBatch, self.request.GET.get("batch"))

        selected_batch = self._select_batch(batches, focused_batch_id)

        batch_form = self._get_form_instance("batch")
        distribution_form = self._get_distribution_form(selected_batch)
        distribution_groups, distribution_assigned = self._build_distribution_view_data(
            distribution_form
        )
        batch_cards = self._build_batch_cards(batches, focused_batch_id=selected_batch.pk if selected_batch else None)

        context.update(
            {
                "active_submenu": "batches",
                "batch_form": batch_form,
                "distribution_form": distribution_form,
                "distribution_groups": distribution_groups,
                "distribution_totals": (
                    self._build_distribution_totals(
                        selected_batch, distribution_assigned
                    )
                    if selected_batch
                    else None
                ),
                "batch_cards": batch_cards,
                "selected_batch": selected_batch,
                "selected_batch_id": selected_batch.pk if selected_batch else None,
                "batch_metrics": self._compute_batch_metrics(batches=batches),
                "dashboard_generated_at": timezone.now(),
            }
        )
        return context

    def _fetch_batches(self) -> List[BirdBatch]:
        allocations_prefetch = Prefetch(
            "allocations",
            queryset=BirdBatchRoomAllocation.objects.select_related("room__chicken_house").order_by(
                "room__chicken_house__name", "room__name"
            ),
        )
        return list(
            BirdBatch.objects.select_related("farm")
            .prefetch_related(allocations_prefetch)
            .order_by("-status", "-birth_date")
        )

    def _select_batch(
        self, batches: List[BirdBatch], focused_batch_id: Optional[int]
    ) -> Optional[BirdBatch]:
        if not batches:
            return None

        if focused_batch_id:
            for batch in batches:
                if batch.pk == focused_batch_id:
                    return batch

        return batches[0]

    def _get_form_instance(self, form_key: str):
        form_attr, form_class = self.form_registry[form_key]
        existing_form = getattr(self, form_attr, None)
        if existing_form is not None:
            return existing_form

        form = form_class()
        setattr(self, form_attr, form)
        return form

    def _get_distribution_form(self, batch: Optional[BirdBatch]):
        if not batch:
            return None

        existing_form = getattr(self, "_distribution_form", None)
        if existing_form is not None and existing_form.batch.pk == batch.pk:
            return existing_form

        return BatchDistributionForm(batch=batch)

    def _build_distribution_view_data(
        self, form: Optional[BatchDistributionForm]
    ) -> Tuple[List[Dict[str, Any]], int]:
        if not form:
            return [], 0

        groups, overall_total = form.build_groups()
        return groups, overall_total

    def _build_distribution_totals(
        self, batch: BirdBatch, assigned_quantity: int
    ) -> Dict[str, int]:
        initial = batch.initial_quantity
        remaining = max(initial - assigned_quantity, 0)
        return {
            "initial": initial,
            "assigned": assigned_quantity,
            "remaining": remaining,
        }

    def _build_batch_cards(
        self, batches: List[BirdBatch], focused_batch_id: Optional[int]
    ) -> List[BatchCard]:
        today = timezone.localdate()
        cards: List[BatchCard] = []
        for batch in batches:
            allocations: List[BatchAllocationSummary] = []
            allocated_total = 0
            for allocation in batch.allocations.all():
                allocated_total += allocation.quantity
                allocations.append(
                    BatchAllocationSummary(
                        id=allocation.pk,
                        room_name=allocation.room.name,
                        chicken_house_name=allocation.room.chicken_house.name,
                        quantity=allocation.quantity,
                    )
                )

            age_days = max((today - batch.birth_date).days, 0)
            age_weeks = age_days // 7

            cards.append(
                BatchCard(
                    id=batch.pk,
                    label=f"Lote #{batch.pk}",
                    farm_name=batch.farm.name,
                    status=batch.status,
                    status_label=batch.get_status_display(),
                    birth_date=batch.birth_date,
                    age_weeks=age_weeks,
                    age_days=age_days,
                    initial_quantity=batch.initial_quantity,
                    allocated_quantity=allocated_total,
                    remaining_quantity=max(batch.initial_quantity - allocated_total, 0),
                    allocations=allocations,
                )
            )

        if focused_batch_id:
            cards.sort(
                key=lambda card: (
                    card["id"] != focused_batch_id,
                    card["status"] != BirdBatch.Status.ACTIVE,
                    -card["birth_date"].toordinal(),
                )
            )
        else:
            cards.sort(
                key=lambda card: (
                    card["status"] != BirdBatch.Status.ACTIVE,
                    -card["birth_date"].toordinal(),
                )
            )
        return cards

    def _compute_batch_metrics(self, batches: Optional[List[BirdBatch]] = None) -> BatchMetrics:
        if batches is None:
            total_batches = BirdBatch.objects.count()
            active_batches = BirdBatch.objects.filter(status=BirdBatch.Status.ACTIVE).count()
            inactive_batches = BirdBatch.objects.filter(status=BirdBatch.Status.INACTIVE).count()
            total_initial_birds = (
                BirdBatch.objects.aggregate(total=Coalesce(Sum("initial_quantity"), 0))["total"] or 0
            )
            total_assigned_birds = (
                BirdBatchRoomAllocation.objects.aggregate(total=Coalesce(Sum("quantity"), 0))["total"] or 0
            )
            total_rooms_used = (
                BirdBatchRoomAllocation.objects.values("room").distinct().count()
            )
            return BatchMetrics(
                total_batches=total_batches,
                active_batches=active_batches,
                inactive_batches=inactive_batches,
                total_initial_birds=total_initial_birds,
                total_assigned_birds=total_assigned_birds,
                total_rooms_used=total_rooms_used,
            )

        total_batches = len(batches)
        active_batches = sum(1 for batch in batches if batch.status == BirdBatch.Status.ACTIVE)
        inactive_batches = total_batches - active_batches
        total_initial_birds = sum(batch.initial_quantity for batch in batches)
        total_assigned_birds = sum(
            allocation.quantity for batch in batches for allocation in batch.allocations.all()
        )
        rooms_used = {
            allocation.room_id for batch in batches for allocation in batch.allocations.all()
        }

        return BatchMetrics(
            total_batches=total_batches,
            active_batches=active_batches,
            inactive_batches=inactive_batches,
            total_initial_birds=total_initial_birds,
            total_assigned_birds=total_assigned_birds,
            total_rooms_used=len(rooms_used),
        )

    def _safe_pk_lookup(self, model, raw_value: Optional[str]) -> Optional[int]:
        try:
            pk_value = int(raw_value) if raw_value is not None else None
        except (TypeError, ValueError):
            return None
        if pk_value is None:
            return None
        if model.objects.filter(pk=pk_value).exists():
            return pk_value
        return None


class InfrastructureHomeView(StaffRequiredMixin, TemplateView):
    """Catalogue and management hub for farms, chicken houses and rooms."""

    template_name = "production/infrastructure.html"
    form_registry = {
        "farm": ("_farm_form", FarmForm),
        "chicken_house": ("_chicken_house_form", ChickenHouseForm),
        "room": ("_room_form", RoomForm),
    }

    def post(self, request, *args, **kwargs):
        form_key = request.POST.get("form_type", "")
        registry_entry = self.form_registry.get(form_key)
        if not registry_entry:
            messages.error(request, "No se pudo determinar el formulario enviado.")
            return redirect("production:infrastructure")

        form_attr, form_class = registry_entry
        form = form_class(request.POST)
        setattr(self, "_selected_panel", form_key)

        if form.is_valid():
            instance = form.save()
            messages.success(request, self._success_message(form_key, instance))
            return redirect(self._build_success_redirect(form_key, instance))

        setattr(self, form_attr, form)
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_submenu"] = "infrastructure"
        context["selected_panel"] = self._resolve_selected_panel()
        context["farms"] = self._fetch_farms()
        context["infrastructure_stats"] = self._compute_stats()
        context["farm_form"] = self._get_form_instance("farm")
        context["chicken_house_form"] = self._get_form_instance("chicken_house")
        context["room_form"] = self._get_form_instance("room")
        context["dashboard_generated_at"] = timezone.now()
        return context

    def _resolve_selected_panel(self) -> str:
        if hasattr(self, "_selected_panel"):
            return getattr(self, "_selected_panel")
        candidate = self.request.GET.get("panel", "summary")
        if candidate in self.form_registry:
            return candidate
        return "summary"

    def _fetch_farms(self):
        chicken_house_prefetch = Prefetch(
            "chicken_houses",
            queryset=ChickenHouse.objects.prefetch_related("rooms").order_by("name"),
        )
        return (
            Farm.objects.annotate(
                chicken_house_count=Count("chicken_houses", distinct=True),
                room_count=Count("chicken_houses__rooms", distinct=True),
            )
            .prefetch_related(chicken_house_prefetch)
            .order_by("name")
        )

    def _compute_stats(self) -> Dict[str, Optional[Decimal]]:
        total_house_area = ChickenHouse.objects.aggregate(
            total=Coalesce(Sum("area_m2"), Decimal("0"))
        )["total"]
        avg_room_area = Room.objects.aggregate(avg=Avg("area_m2"))["avg"]
        farms_without_barns = (
            Farm.objects.annotate(barn_count=Count("chicken_houses"))
            .filter(barn_count=0)
            .count()
        )
        largest_barn = (
            ChickenHouse.objects.select_related("farm")
            .order_by("-area_m2")
            .first()
        )

        return {
            "total_farms": Farm.objects.count(),
            "total_chicken_houses": ChickenHouse.objects.count(),
            "total_rooms": Room.objects.count(),
            "total_house_area": total_house_area,
            "avg_room_area": avg_room_area,
            "farms_without_barns": farms_without_barns,
            "largest_barn": largest_barn,
        }

    def _get_form_instance(self, form_key: str):
        form_attr, form_class = self.form_registry[form_key]
        existing_form = getattr(self, form_attr, None)
        if existing_form is not None:
            return existing_form

        initial = self._build_initial_data(form_key)
        form = form_class(initial=initial)
        setattr(self, form_attr, form)
        return form

    def _build_initial_data(self, form_key: str) -> Dict[str, int]:
        if form_key == "chicken_house":
            farm_id = self._safe_pk_lookup(Farm, self.request.GET.get("farm"))
            return {"farm": farm_id} if farm_id else {}
        if form_key == "room":
            chicken_house_id = self._safe_pk_lookup(ChickenHouse, self.request.GET.get("chicken_house"))
            if chicken_house_id:
                return {"chicken_house": chicken_house_id}
            farm_id = self._safe_pk_lookup(Farm, self.request.GET.get("farm"))
            if farm_id:
                first_chicken_house = (
                    ChickenHouse.objects.filter(farm_id=farm_id).order_by("name").first()
                )
                if first_chicken_house:
                    return {"chicken_house": first_chicken_house.pk}
        return {}

    def _safe_pk_lookup(self, model, raw_value: Optional[str]) -> Optional[int]:
        try:
            pk_value = int(raw_value) if raw_value is not None else None
        except (TypeError, ValueError):
            return None
        if pk_value is None:
            return None
        if model.objects.filter(pk=pk_value).exists():
            return pk_value
        return None

    def _success_message(self, form_key: str, instance) -> str:
        if form_key == "farm":
            return f'Se agregó la granja "{instance.name}".'
        if form_key == "chicken_house":
            return f'Se registró el galpón "{instance.name}" en {instance.farm.name}.'
        if form_key == "room":
            return (
                f'Se creó el salón "{instance.name}" en {instance.chicken_house.name} '
                f'({instance.chicken_house.farm.name}).'
            )
        return "Cambios guardados correctamente."

    def _build_success_redirect(self, form_key: str, instance) -> str:
        base_url = str(reverse_lazy("production:infrastructure"))
        params: Dict[str, str] = {"panel": form_key}
        if form_key == "chicken_house":
            params["farm"] = str(instance.farm_id)
        elif form_key == "room":
            params["chicken_house"] = str(instance.chicken_house_id)
            params["farm"] = str(instance.chicken_house.farm_id)
        return f"{base_url}?{urlencode(params)}"


class InfrastructureFormViewMixin(StaffRequiredMixin, SuccessMessageMixin):
    template_name = "production/infrastructure_form.html"
    success_url = reverse_lazy("production:infrastructure")
    page_title: str = ""
    submit_label: str = ""
    entity_label: str = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("active_submenu", "infrastructure")
        context["page_title"] = self.page_title
        context["submit_label"] = self.submit_label
        context["entity_label"] = self.entity_label
        context["breadcrumbs"] = [
            {"label": "Infraestructura", "url": reverse_lazy("production:infrastructure")},
            {"label": self.page_title, "url": ""},
        ]
        return context


class FarmUpdateView(InfrastructureFormViewMixin, UpdateView):
    model = Farm
    form_class = FarmForm
    page_title = "Editar granja"
    submit_label = "Guardar cambios"
    entity_label = "granja"
    success_message = 'Se actualizó la granja "%(name)s" correctamente.'


class ChickenHouseUpdateView(InfrastructureFormViewMixin, UpdateView):
    model = ChickenHouse
    form_class = ChickenHouseForm
    page_title = "Editar galpón"
    submit_label = "Guardar cambios"
    entity_label = "galpón"
    success_message = 'Se actualizó el galpón "%(name)s" correctamente.'


class RoomUpdateView(InfrastructureFormViewMixin, UpdateView):
    model = Room
    form_class = RoomForm
    page_title = "Editar salón"
    submit_label = "Guardar cambios"
    entity_label = "salón"
    success_message = 'Se actualizó el salón "%(name)s" correctamente.'


class InfrastructureDeleteView(StaffRequiredMixin, DeleteView):
    template_name = "production/infrastructure_confirm_delete.html"
    success_url = reverse_lazy("production:infrastructure")
    entity_label: str = ""
    success_message: str = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("active_submenu", "infrastructure")
        context["entity_label"] = self.entity_label
        context["object_display"] = str(self.object)
        context["breadcrumbs"] = [
            {"label": "Infraestructura", "url": reverse_lazy("production:infrastructure")},
            {"label": f"Eliminar {self.entity_label}", "url": ""},
        ]
        return context

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        object_display = str(self.object)
        response = super().delete(request, *args, **kwargs)
        if self.success_message:
            messages.success(request, self.success_message % {"object": object_display})
        return response


class FarmDeleteView(InfrastructureDeleteView):
    model = Farm
    entity_label = "granja"
    success_message = 'Se eliminó la granja "%(object)s" correctamente.'


class ChickenHouseDeleteView(InfrastructureDeleteView):
    model = ChickenHouse
    entity_label = "galpón"
    success_message = 'Se eliminó el galpón "%(object)s" correctamente.'


class RoomDeleteView(InfrastructureDeleteView):
    model = Room
    entity_label = "salón"
    success_message = 'Se eliminó el salón "%(object)s" correctamente.'


class BatchFormViewMixin(StaffRequiredMixin, SuccessMessageMixin):
    template_name = "production/batch_form.html"
    success_url = reverse_lazy("production:batches")
    page_title: str = ""
    submit_label: str = ""
    entity_label: str = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("active_submenu", "batches")
        context["page_title"] = self.page_title
        context["submit_label"] = self.submit_label
        context["entity_label"] = self.entity_label
        context["breadcrumbs"] = [
            {"label": "Lotes y asignaciones", "url": reverse_lazy("production:batches")},
            {"label": self.page_title, "url": ""},
        ]
        return context


class BirdBatchUpdateView(BatchFormViewMixin, UpdateView):
    model = BirdBatch
    form_class = BirdBatchForm
    page_title = "Editar lote de aves"
    submit_label = "Guardar cambios"
    entity_label = "lote de aves"
    success_message = 'Se actualizó el lote #%(pk)s correctamente.'


class BirdBatchDeleteView(StaffRequiredMixin, DeleteView):
    model = BirdBatch
    template_name = "production/batches_confirm_delete.html"
    success_url = reverse_lazy("production:batches")
    entity_label = "lote de aves"
    success_message = 'Se eliminó el lote "%(object)s" correctamente.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("active_submenu", "batches")
        context["entity_label"] = self.entity_label
        context["object_display"] = str(self.object)
        context["breadcrumbs"] = [
            {"label": "Lotes y asignaciones", "url": reverse_lazy("production:batches")},
            {"label": f"Eliminar {self.entity_label}", "url": ""},
        ]
        return context

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        object_display = str(self.object)
        response = super().delete(request, *args, **kwargs)
        messages.success(request, self.success_message % {"object": object_display})
        return response


class BatchAllocationDeleteView(StaffRequiredMixin, DeleteView):
    model = BirdBatchRoomAllocation
    template_name = "production/batches_confirm_delete.html"
    success_url = reverse_lazy("production:batches")
    entity_label = "asignación de lote"
    success_message = 'Se eliminó la asignación "%(object)s" correctamente.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("active_submenu", "batches")
        context["entity_label"] = self.entity_label
        context["object_display"] = str(self.object)
        context["breadcrumbs"] = [
            {"label": "Lotes y asignaciones", "url": reverse_lazy("production:batches")},
            {"label": f"Eliminar {self.entity_label}", "url": ""},
        ]
        return context

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        object_display = str(self.object)
        response = super().delete(request, *args, **kwargs)
        messages.success(request, self.success_message % {"object": object_display})
        return response


production_home_view = ProductionHomeView.as_view()
infrastructure_home_view = InfrastructureHomeView.as_view()
farm_update_view = FarmUpdateView.as_view()
chicken_house_update_view = ChickenHouseUpdateView.as_view()
room_update_view = RoomUpdateView.as_view()
farm_delete_view = FarmDeleteView.as_view()
chicken_house_delete_view = ChickenHouseDeleteView.as_view()
room_delete_view = RoomDeleteView.as_view()
batch_management_view = BatchManagementView.as_view()
bird_batch_update_view = BirdBatchUpdateView.as_view()
bird_batch_delete_view = BirdBatchDeleteView.as_view()
batch_allocation_delete_view = BatchAllocationDeleteView.as_view()
