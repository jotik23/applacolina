from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta
from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, TypedDict
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.db import transaction
from django.db.models import Avg, Count, Max, Prefetch, Q, Sum
from django.db.models.functions import Coalesce, TruncWeek
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.generic import DeleteView, TemplateView, UpdateView

from applacolina.mixins import StaffRequiredMixin
from production.forms import (
    BatchDailyProductionForm,
    BatchDistributionForm,
    BirdBatchForm,
    BreedReferenceForm,
    BreedWeeklyMetricsForm,
    ChickenHouseForm,
    EggBatchClassificationForm,
    EggBatchReceiptForm,
    FarmForm,
    RoomForm,
    RoomProductionSnapshot,
)
from production.models import (
    BirdBatch,
    BirdBatchRoomAllocation,
    BreedReference,
    BreedWeeklyGuide,
    ChickenHouse,
    EggClassificationBatch,
    EggType,
    Farm,
    ProductionRecord,
    ProductionRoomRecord,
    Room,
    WeightSampleSession,
)
from production.services.daily_board import save_daily_room_entries
from production.services.egg_classification import (
    InventoryFlow,
    InventoryRow,
    PendingBatch,
    build_classification_session_flow_range,
    build_inventory_flow,
    build_inventory_flow_range,
    build_pending_batches,
    compute_unclassified_total,
    delete_classification_session,
    reset_batch_progress,
    summarize_classified_inventory,
)
from production.services.reference_tables import get_reference_targets, reset_reference_targets_cache


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


class DailyIndicatorAggregate(TypedDict):
    consumption_bags_today: Optional[float]
    consumption_bags_previous: Optional[float]
    production_cartons_today: Optional[float]
    production_cartons_previous: Optional[float]


class LotOverview(TypedDict):
    id: int
    label: str
    breed: str
    birth_date: date
    age_weeks: int
    initial_birds: int
    current_birds: int
    bird_balance: int
    production_avg_three_days: Optional[float]
    egg_weight_avg_three_days: Optional[float]
    feed_per_bird_avg_three_days: Optional[float]
    barn_count: int
    barn_names_display: str
    barn_houses_display: str
    uniformity: Optional[float]
    avg_weight: Optional[float]
    target_weight: Optional[float]
    feed_today_grams: Optional[float]
    weekly_feed_kg: float
    total_feed_to_date_kg: float
    weekly_mortality_percentage: Optional[float]
    barns: List[BarnAllocation]
    mortality: List[MortalityRecord]
    weight_trend: List[WeightTrendPoint]
    consumption_history: List[ConsumptionRecord]
    egg_mix: List[EggSizeRecord]
    alerts: List[str]
    notes: str
    daily_snapshot: List["DailySnapshotMetric"]
    daily_snapshot_map: Dict[str, "DailySnapshotMetric"]
    daily_rollup: DailyIndicatorAggregate
    latest_record_date: Optional[date]


class DailySnapshotMetric(TypedDict):
    label: str
    slug: str
    unit: str
    decimals: int
    actual: Optional[float]
    target: Optional[float]
    delta: Optional[float]
    previous: Optional[float]
    previous_delta: Optional[float]
    status: Optional[str]


class FarmSummary(TypedDict):
    total_initial_birds: int
    current_birds: int
    lot_count: int
    total_daily_production_cartons: float
    total_daily_consumption_kg: float
    total_daily_consumption_bags: float
    total_daily_mortality: float
    total_daily_discard: float


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


def build_active_batch_label_map(batches: Iterable[BirdBatch]) -> Dict[int, str]:
    """Return stable labels for active batches based on their identifiers."""
    label_map: Dict[int, str] = {}
    for batch in batches:
        if batch.status != BirdBatch.Status.ACTIVE:
            continue
        label_map[batch.pk] = f"Lote #{batch.pk}"
    return label_map


def resolve_batch_label(batch: BirdBatch, label_map: Mapping[int, str]) -> str:
    """Resolve the display label for a batch with a fallback to its primary key."""
    return label_map.get(batch.pk, f"Lote #{batch.pk}")


class ProductionDashboardContextMixin(StaffRequiredMixin):
    """Build the shared poultry production dashboard context."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        focused_lot_id: Optional[int] = None
        lot_param = self.request.GET.get("lot") if self.request else None
        if lot_param and lot_param.isdigit():
            focused_lot_id = int(lot_param)

        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        week_start = today - timedelta(days=6)
        four_week_start = today - timedelta(days=27)
        year_start = date(today.year, 1, 1)
        history_start = today - timedelta(days=28)
        three_day_start = today - timedelta(days=2)

        batches = list(
            BirdBatch.objects.filter(status=BirdBatch.Status.ACTIVE)
            .select_related("farm", "breed")
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
                    "dashboard_generated_at": timezone.now(),
                    "today": today,
                    "yesterday": yesterday,
                    "focused_lot_id": focused_lot_id,
                }
            )
            return context

        batches.sort(
            key=lambda batch: (
                -max((today - batch.birth_date).days, 0),
                -batch.initial_quantity,
                batch.pk,
            )
        )

        label_map = build_active_batch_label_map(batches)

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
                three_day_production_avg=Avg(
                    "production", filter=Q(date__range=(three_day_start, today))
                ),
                three_day_consumption_avg=Avg(
                    "consumption", filter=Q(date__range=(three_day_start, today))
                ),
                three_day_egg_weight_avg=Avg(
                    "average_egg_weight", filter=Q(date__range=(three_day_start, today))
                ),
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

        yesterday_records_map: Dict[int, ProductionRecord] = {
            record.bird_batch_id: record
            for record in ProductionRecord.objects.filter(
                bird_batch_id__in=batch_ids, date=yesterday
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

        def compute_previous_delta(
            actual_value: Optional[float],
            previous_value: Optional[float],
            decimals: int,
        ) -> Optional[float]:
            if actual_value is None or previous_value is None:
                return None
            return round(actual_value - previous_value, decimals)

        def normalize_value(
            value: Optional[float],
            divisor: float,
            precision: int,
        ) -> Optional[float]:
            if value is None:
                return None
            try:
                return round(float(value) / divisor, precision)
            except (ArithmeticError, ValueError, TypeError, ZeroDivisionError):
                return None

        farms_map: Dict[int, Dict[str, object]] = {}
        all_lots: List[LotOverview] = []

        for batch in batches:
            stats = production_map.get(batch.id, {})
            total_mortality = int(stats.get("total_mortality", 0) or 0)
            total_discard = int(stats.get("total_discard", 0) or 0)
            current_birds = max(batch.initial_quantity - total_mortality, 0)
            bird_balance = batch.initial_quantity - current_birds

            weekly_consumption = float(stats.get("weekly_consumption") or Decimal("0"))
            total_consumption = float(stats.get("total_consumption") or Decimal("0"))
            weekly_mortality = int(stats.get("weekly_mortality", 0) or 0)
            four_week_mortality = int(stats.get("four_week_mortality", 0) or 0)
            yearly_mortality = int(stats.get("yearly_mortality", 0) or 0)
            latest_record_date = stats.get("latest_record_date")
            three_day_production_avg_raw = stats.get("three_day_production_avg")
            production_avg_three_days = (
                float(three_day_production_avg_raw) if three_day_production_avg_raw is not None else None
            )
            three_day_consumption_avg_raw = stats.get("three_day_consumption_avg")
            consumption_avg_three_days = (
                float(three_day_consumption_avg_raw) if three_day_consumption_avg_raw is not None else None
            )
            three_day_egg_weight_avg_raw = stats.get("three_day_egg_weight_avg")
            egg_weight_avg_three_days = (
                float(three_day_egg_weight_avg_raw) if three_day_egg_weight_avg_raw is not None else None
            )
            feed_per_bird_avg_three_days = (
                round(consumption_avg_three_days * 1000 / current_birds, 2)
                if consumption_avg_three_days is not None and current_birds
                else None
            )
            weekly_mortality_percentage = (
                round((weekly_mortality / batch.initial_quantity) * 100, 2)
                if batch.initial_quantity
                else None
            )

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
            actual_record = daily_record
            previous_record = yesterday_records_map.get(batch.id)
            record_for_display = daily_record or latest_record

            consumption_actual = (
                float(actual_record.consumption)
                if actual_record and actual_record.consumption is not None
                else None
            )
            consumption_previous = (
                float(previous_record.consumption)
                if previous_record and previous_record.consumption is not None
                else None
            )
            mortality_actual = (
                float(actual_record.mortality)
                if actual_record and actual_record.mortality is not None
                else None
            )
            mortality_previous = (
                float(previous_record.mortality)
                if previous_record and previous_record.mortality is not None
                else None
            )
            discard_actual = (
                float(actual_record.discard)
                if actual_record and actual_record.discard is not None
                else None
            )
            discard_previous = (
                float(previous_record.discard)
                if previous_record and previous_record.discard is not None
                else None
            )
            egg_weight_actual = (
                float(actual_record.average_egg_weight)
                if actual_record and actual_record.average_egg_weight is not None
                else None
            )
            egg_weight_previous = (
                float(previous_record.average_egg_weight)
                if previous_record and previous_record.average_egg_weight is not None
                else None
            )
            production_actual = (
                float(actual_record.production)
                if actual_record and actual_record.production is not None
                else None
            )
            production_previous = (
                float(previous_record.production)
                if previous_record and previous_record.production is not None
                else None
            )

            birds_previous_day = current_birds + int(mortality_actual or 0)
            posture_percent_actual = (
                round((production_actual / current_birds) * 100, 2)
                if production_actual is not None and current_birds
                else None
            )
            posture_percent_previous = (
                round((production_previous / birds_previous_day) * 100, 2)
                if production_previous is not None and birds_previous_day
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
            consumption_target_value = float(consumption_target) if consumption_target is not None else None
            consumption_delta_raw, consumption_status = evaluate_delta(
                consumption_actual,
                consumption_target,
                tolerance_value=max(consumption_target * 0.05, 5.0),
            )
            consumption_actual_bags = normalize_value(consumption_actual, 40, 2)
            consumption_previous_bags = normalize_value(consumption_previous, 40, 2)
            consumption_target_bags = normalize_value(consumption_target_value, 40, 2)
            consumption_delta_display = normalize_value(consumption_delta_raw, 40, 2)
            daily_snapshot.append(
                DailySnapshotMetric(
                    label="Consumo",
                    slug="consumption",
                    unit="bultos 40 kg",
                    decimals=2,
                    actual=consumption_actual_bags,
                    target=consumption_target_bags,
                    delta=consumption_delta_display,
                    previous=consumption_previous_bags,
                    previous_delta=compute_previous_delta(
                        consumption_actual_bags,
                        consumption_previous_bags,
                        2,
                    ),
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
                    decimals=0,
                    actual=mortality_actual,
                    target=mortality_target,
                    delta=mortality_delta,
                    previous=mortality_previous,
                    previous_delta=compute_previous_delta(
                        mortality_actual,
                        mortality_previous,
                        0,
                    ),
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
                    decimals=0,
                    actual=discard_actual,
                    target=discard_target,
                    delta=discard_delta,
                    previous=discard_previous,
                    previous_delta=compute_previous_delta(
                        discard_actual,
                        discard_previous,
                        0,
                    ),
                    status=discard_status,
                )
            )

            egg_target = reference_targets["egg_weight_g"]
            egg_target_value = float(egg_target) if egg_target is not None else None
            egg_delta_raw, egg_status = evaluate_delta(
                egg_weight_actual,
                egg_target,
                tolerance_value=1.2,
            )
            egg_weight_display = normalize_value(egg_weight_actual, 300, 2)
            egg_weight_previous_display = normalize_value(egg_weight_previous, 300, 2)
            egg_target_display = normalize_value(egg_target_value, 300, 2)
            egg_delta_display = normalize_value(egg_delta_raw, 300, 2)
            daily_snapshot.append(
                DailySnapshotMetric(
                    label="Peso del huevo",
                    slug="egg_weight",
                    unit="kg",
                    decimals=2,
                    actual=egg_weight_display,
                    target=egg_target_display,
                    delta=egg_delta_display,
                    previous=egg_weight_previous_display,
                    previous_delta=compute_previous_delta(
                        egg_weight_display,
                        egg_weight_previous_display,
                        2,
                    ),
                    status=egg_status,
                )
            )

            production_target = reference_targets["production_percent"]
            production_delta, production_status = evaluate_delta(
                posture_percent_actual,
                production_target,
                tolerance_value=2.0,
            )
            daily_snapshot.append(
                DailySnapshotMetric(
                    label="% de postura",
                    slug="production",
                    unit="%",
                    decimals=2,
                    actual=posture_percent_actual,
                    target=production_target,
                    delta=production_delta,
                    previous=posture_percent_previous,
                    previous_delta=compute_previous_delta(
                        posture_percent_actual,
                        posture_percent_previous,
                        2,
                    ),
                    status=production_status,
                )
            )

            consumption_bags_today = consumption_actual_bags
            consumption_bags_previous = consumption_previous_bags
            production_cartons_today = normalize_value(production_actual, 30, 1)
            production_cartons_previous = normalize_value(production_previous, 30, 1)

            daily_rollup: DailyIndicatorAggregate = {
                "consumption_bags_today": consumption_bags_today,
                "consumption_bags_previous": consumption_bags_previous,
                "production_cartons_today": production_cartons_today,
                "production_cartons_previous": production_cartons_previous,
            }

            daily_snapshot_map = {metric["slug"]: metric for metric in daily_snapshot}

            alerts: List[str] = []
            consumption_delta_text_value = (
                consumption_delta_display
                if consumption_delta_display is not None
                else consumption_delta_raw
            )
            consumption_unit_label = (
                "bultos (40 kg)" if consumption_delta_display is not None else "kg"
            )
            if consumption_status == "high" and consumption_delta_text_value is not None:
                alerts.append(
                    f"Consumo diario +{consumption_delta_text_value:.2f} {consumption_unit_label} sobre la tabla."
                )
            if consumption_status == "low" and consumption_delta_text_value is not None:
                alerts.append(
                    f"Consumo diario {consumption_delta_text_value:.2f} {consumption_unit_label} por debajo del objetivo."
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
                f"Último registro de producción: {record_for_display.date:%d %b %Y}."
                if record_for_display
                else "Sin registros de producción disponibles."
            )

            barns_list: List[BarnAllocation] = []
            barn_names: List[str] = []
            barn_house_names: List[str] = []

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
                house_name = allocation.room.chicken_house.name
                barn_names.append(f"{house_name} · {allocation.room.name}")
                if house_name not in barn_house_names:
                    barn_house_names.append(house_name)

            barn_names_display = ", ".join(barn_names) if barn_names else "Sin asignación"
            barn_houses_display = ", ".join(barn_house_names) if barn_house_names else "Sin galpón"

            lot_data: LotOverview = {
                "id": batch.pk,
                "label": resolve_batch_label(batch, label_map),
                "breed": batch.breed.name,
                "birth_date": batch.birth_date,
                "age_weeks": (today - batch.birth_date).days // 7 if batch.birth_date else 0,
                "initial_birds": batch.initial_quantity,
                "current_birds": current_birds,
                "bird_balance": batch.initial_quantity - current_birds,
                "production_avg_three_days": production_avg_three_days,
                "egg_weight_avg_three_days": egg_weight_avg_three_days,
                "feed_per_bird_avg_three_days": feed_per_bird_avg_three_days,
                "barn_count": len(barns_list),
                "barn_names_display": barn_names_display,
                "barn_houses_display": barn_houses_display,
                "uniformity": round(uniformity, 2) if uniformity is not None else None,
                "avg_weight": round(avg_weight, 2) if avg_weight is not None else None,
                "target_weight": None,
                "feed_today_grams": feed_today_grams,
                "weekly_feed_kg": round(weekly_consumption, 2),
                "total_feed_to_date_kg": round(total_consumption, 2),
                "weekly_mortality_percentage": weekly_mortality_percentage,
                "barns": barns_list,
                "mortality": mortality_records,
                "weight_trend": weight_trend,
                "consumption_history": consumption_history,
                "egg_mix": egg_mix,
                "alerts": alerts,
                "notes": notes,
                "daily_snapshot": daily_snapshot,
                "daily_snapshot_map": daily_snapshot_map,
                "daily_rollup": daily_rollup,
                "latest_record_date": record_for_display.date if record_for_display else None,
            }

            all_lots.append(lot_data)
            farm_bucket = farms_map.setdefault(
                batch.farm_id,
                {
                    "farm": batch.farm,
                    "lots": [],
                    "aggregates": {
                        "production_cartons": 0.0,
                        "consumption_kg": 0.0,
                        "consumption_bags": 0.0,
                        "mortality": 0.0,
                        "discard": 0.0,
                    },
                },
            )
            aggregates = farm_bucket["aggregates"]
            if production_cartons_today is not None:
                aggregates["production_cartons"] += production_cartons_today
            if consumption_actual is not None:
                aggregates["consumption_kg"] += consumption_actual
            if consumption_bags_today is not None:
                aggregates["consumption_bags"] += consumption_bags_today
            if mortality_actual is not None:
                aggregates["mortality"] += mortality_actual
            if discard_actual is not None:
                aggregates["discard"] += discard_actual
            farm_bucket["lots"].append(lot_data)

        farms_context: List[FarmOverview] = []
        for farm_id, info in farms_map.items():
            farm = info["farm"]
            lots = info["lots"]
            aggregates = info.get("aggregates", {})
            total_initial = sum(lot["initial_birds"] for lot in lots)
            total_current = sum(lot["current_birds"] for lot in lots)
            total_daily_production_cartons = round(float(aggregates.get("production_cartons", 0.0)), 1)
            total_daily_consumption_kg = round(float(aggregates.get("consumption_kg", 0.0)), 2)
            total_daily_consumption_bags = round(float(aggregates.get("consumption_bags", 0.0)), 2)
            total_daily_mortality = float(aggregates.get("mortality", 0.0))
            total_daily_discard = float(aggregates.get("discard", 0.0))

            farm_summary: FarmSummary = {
                "total_initial_birds": total_initial,
                "current_birds": total_current,
                "lot_count": len(lots),
                "total_daily_production_cartons": total_daily_production_cartons,
                "total_daily_consumption_kg": total_daily_consumption_kg,
                "total_daily_consumption_bags": total_daily_consumption_bags,
                "total_daily_mortality": float(round(total_daily_mortality, 1)),
                "total_daily_discard": float(round(total_daily_discard, 1)),
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

        context.update(
            {
                "farms": farms_context,
                "dashboard_generated_at": timezone.now(),
                "today": today,
                "yesterday": yesterday,
                "focused_lot_id": focused_lot_id,
            }
        )
        return context


class EggInventoryDashboardView(StaffRequiredMixin, TemplateView):
    template_name = "production/egg_inventory.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pending_batches = build_pending_batches(limit=120)
        pending_groups = self._group_pending_batches(pending_batches)
        inventory_rows: list[InventoryRow] = summarize_classified_inventory()
        inventory_total = sum((row.cartons for row in inventory_rows), Decimal("0"))
        sellable_types = {
            EggType.JUMBO,
            EggType.TRIPLE_A,
            EggType.DOUBLE_A,
            EggType.SINGLE_A,
            EggType.B,
            EggType.C,
        }
        inventory_sellable_rows = [row for row in inventory_rows if row.egg_type in sellable_types]
        inventory_discard_rows = [row for row in inventory_rows if row.egg_type not in sellable_types]
        sellable_total = sum((row.cartons for row in inventory_sellable_rows), Decimal("0"))
        discard_total = sum((row.cartons for row in inventory_discard_rows), Decimal("0"))
        unclassified_total = compute_unclassified_total()

        context.update(
            {
                "active_submenu": "egg_inventory",
                "pending_batches": pending_batches,
                "pending_groups": pending_groups,
                "inventory_rows": inventory_rows,
                "inventory_sellable_rows": inventory_sellable_rows,
                "inventory_discard_rows": inventory_discard_rows,
                "inventory_total": inventory_total,
                "inventory_sellable_total": sellable_total,
                "inventory_discard_total": discard_total,
                "unclassified_total": unclassified_total,
            }
        )
        return context

    def _group_pending_batches(self, batches: list[PendingBatch]) -> list[dict[str, Any]]:
        grouped: list[dict[str, Any]] = []
        day_map: dict[date, dict[str, Any]] = {}
        for batch in batches:
            day_group = day_map.get(batch.production_date)
            if not day_group:
                day_group = {
                    "date": batch.production_date,
                    "farms": [],
                    "_farm_map": {},
                    "total_pending": Decimal("0"),
                }
                day_map[batch.production_date] = day_group
                grouped.append(day_group)

            farm_map = day_group["_farm_map"]
            farm_group = farm_map.get(batch.farm_name)
            if not farm_group:
                farm_group = {"farm_name": batch.farm_name, "batches": []}
                farm_map[batch.farm_name] = farm_group
                day_group["farms"].append(farm_group)

            farm_group["batches"].append(batch)
            day_group["total_pending"] += Decimal(batch.pending_cartons)

        grouped.sort(key=lambda item: item["date"])
        for day_group in grouped:
            day_group.pop("_farm_map", None)
            day_group["farms"].sort(key=lambda farm: farm["farm_name"])
            for farm_group in day_group["farms"]:
                farm_group["batches"].sort(
                    key=lambda batch: (batch.production_date, batch.farm_name, batch.lot_label)
                )
        return grouped


class EggInventoryCardexView(StaffRequiredMixin, TemplateView):
    template_name = "production/egg_inventory_cardex.html"
    egg_type_labels = dict(EggType.choices)
    type_order = [
        {"key": EggType.JUMBO, "label": egg_type_labels[EggType.JUMBO]},
        {"key": EggType.TRIPLE_A, "label": egg_type_labels[EggType.TRIPLE_A]},
        {"key": EggType.DOUBLE_A, "label": egg_type_labels[EggType.DOUBLE_A]},
        {"key": EggType.SINGLE_A, "label": egg_type_labels[EggType.SINGLE_A]},
        {"key": EggType.B, "label": egg_type_labels[EggType.B]},
        {"key": EggType.C, "label": egg_type_labels[EggType.C]},
        {"key": EggType.D, "label": egg_type_labels[EggType.D]},
    ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        month_start = self._resolve_month()
        month_end = self._resolve_month_end(month_start)
        farm_options = list(Farm.objects.order_by("name").values("id", "name"))
        valid_ids = {option["id"] for option in farm_options}
        selected_farm_id = self._resolve_farm_id(valid_ids)
        cardex_view = self._resolve_view()

        flows = []
        flow_cumulative_totals: dict[date, dict[str, Decimal]] = {}
        session_flows = []
        if cardex_view == "sessions":
            session_flows = build_classification_session_flow_range(
                start_date=month_start,
                end_date=month_end,
                farm_id=selected_farm_id,
            )
        else:
            flows = build_inventory_flow_range(
                start_date=month_start,
                end_date=month_end,
                farm_id=selected_farm_id,
            )
            flow_cumulative_totals = self._build_flow_cumulative_totals(flows)
            flows.sort(key=lambda flow: flow.day, reverse=True)

        prev_month = self._add_months(month_start, -1)
        next_month = self._add_months(month_start, 1)
        current_month_start = timezone.localdate().replace(day=1)
        can_go_next = next_month <= current_month_start

        context.update(
            {
                "active_submenu": "egg_inventory",
                "flows": flows,
                "month_start": month_start,
                "month_end": month_end,
                "month_slug": self._month_slug(month_start),
                "prev_month_slug": self._month_slug(prev_month),
                "next_month_slug": self._month_slug(next_month),
                "can_go_next": can_go_next,
                "selected_farm_id": selected_farm_id,
                "farm_options": farm_options,
                "type_order": self.type_order,
                "cardex_view": cardex_view,
                "session_flows": session_flows,
                "flow_cumulative_totals": flow_cumulative_totals,
            }
        )
        return context

    def _resolve_month(self) -> date:
        today = timezone.localdate()
        default_month = today.replace(day=1)
        raw_month = self.request.GET.get("month")
        if not raw_month:
            return default_month
        try:
            year_str, month_str = raw_month.split("-", 1)
            year = int(year_str)
            month = int(month_str)
            if 1 <= month <= 12:
                return date(year, month, 1)
        except (ValueError, TypeError):
            pass
        return default_month

    def _resolve_month_end(self, month_start: date) -> date:
        last_day = monthrange(month_start.year, month_start.month)[1]
        month_end = date(month_start.year, month_start.month, last_day)
        today = timezone.localdate()
        if month_start.year == today.year and month_start.month == today.month:
            return today
        return month_end

    def _add_months(self, month_start: date, delta: int) -> date:
        month_index = (month_start.year * 12 + month_start.month - 1) + delta
        target_year = month_index // 12
        target_month = month_index % 12 + 1
        return date(target_year, target_month, 1)

    def _month_slug(self, month_start: date) -> str:
        return f"{month_start:%Y-%m}"

    def _resolve_farm_id(self, valid_ids: set[int]) -> Optional[int]:
        raw_value = self.request.GET.get("farm")
        if raw_value in (None, "", "all"):
            return None
        try:
            farm_id = int(raw_value)
        except (TypeError, ValueError):
            return None
        return farm_id if farm_id in valid_ids else None

    def _resolve_view(self) -> str:
        raw = self.request.GET.get("view")
        if raw == "sessions":
            return "sessions"
        return "production"

    def _build_flow_cumulative_totals(
        self, flows: list[InventoryFlow]
    ) -> dict[date, dict[str, Decimal]]:
        if not flows:
            return {}
        running = {
            "produced_cartons": Decimal("0"),
            "delta_receipt": Decimal("0"),
            "confirmed_cartons": Decimal("0"),
            "classified_cartons": Decimal("0"),
            "delta_inventory": Decimal("0"),
        }
        totals_by_day: dict[date, dict[str, Decimal]] = {}
        for flow in sorted(flows, key=lambda current: current.day):
            running["produced_cartons"] += flow.produced_cartons
            running["delta_receipt"] += flow.delta_receipt
            running["confirmed_cartons"] += flow.confirmed_cartons
            running["classified_cartons"] += flow.classified_cartons
            running["delta_inventory"] += flow.delta_inventory
            totals_by_day[flow.day] = dict(running)
        return totals_by_day


class EggInventoryBatchDetailView(StaffRequiredMixin, TemplateView):
    template_name = "production/egg_inventory_batch_detail.html"
    batch: EggClassificationBatch

    def dispatch(self, request, *args, **kwargs):
        self.batch = self._get_batch()
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        form_key = request.POST.get("form")
        if form_key == "receipt":
            form = EggBatchReceiptForm(request.POST, batch=self.batch)
            if form.is_valid():
                form.save(actor=request.user)
                messages.success(request, "Cantidad recibida confirmada correctamente.")
                return redirect(self.request.path)
            return self.render_to_response(self.get_context_data(receipt_form=form))

        if form_key == "classification":
            form = EggBatchClassificationForm(request.POST, batch=self.batch)
            if form.is_valid():
                form.save(actor=request.user)
                messages.success(
                    request,
                    "Clasificación parcial registrada. El inventario ya refleja el movimiento y puedes seguir con este lote.",
                )
                return redirect(self.request.path)
            return self.render_to_response(self.get_context_data(classification_form=form))

        if form_key == "delete_session":
            session_id = request.POST.get("session_id")
            session = (
                self.batch.classification_sessions.filter(pk=session_id).first()
                if session_id
                else None
            )
            if not session:
                messages.error(request, "No se encontró la iteración seleccionada.")
                return redirect(self.request.path)
            delete_classification_session(session=session)
            messages.success(request, "La iteración de clasificación fue revertida.")
            return redirect(self.request.path)

        if form_key == "reset_batch":
            reset_batch_progress(batch=self.batch)
            messages.success(
                request,
                "Se restableció el día: puedes volver a confirmar y clasificar desde cero.",
            )
            return redirect(self.request.path)

        messages.error(request, "No se pudo determinar el formulario enviado.")
        return redirect(self.request.path)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        receipt_form = kwargs.get("receipt_form") or EggBatchReceiptForm(batch=self.batch)
        classification_form = kwargs.get("classification_form") or EggBatchClassificationForm(batch=self.batch)
        entries = list(self.batch.classification_entries.all())
        entry_rows = self._build_entry_rows(entries)
        session_rows = self._build_session_rows()
        pending_value = max(Decimal(self.batch.pending_cartons), Decimal("0"))
        can_submit = self.batch.received_cartons is not None and pending_value > 0
        batch_snapshot = {
            "reported_cartons": self.batch.reported_cartons,
            "received_cartons": self.batch.received_cartons,
            "pending_cartons": self.batch.pending_cartons,
        }
        context.update(
            {
                "active_submenu": "egg_inventory",
                "batch": self.batch,
                "receipt_form": receipt_form,
                "classification_form": classification_form,
                "entries": entries,
                "entry_rows": entry_rows,
                "session_rows": session_rows,
                "batch_snapshot": batch_snapshot,
                "pending_for_form": pending_value,
                "can_submit_classification": can_submit,
                "has_resettable_progress": bool(self.batch.received_cartons or session_rows),
                "return_url": reverse("production:egg-inventory"),
            }
        )
        return context

    def _get_batch(self) -> EggClassificationBatch:
        return get_object_or_404(
            EggClassificationBatch.objects.select_related(
                "bird_batch",
                "bird_batch__farm",
                "production_record",
            ).prefetch_related(
                "classification_entries",
                "classification_sessions__entries",
            ),
            pk=self.kwargs["pk"],
        )

    def _build_entry_rows(self, entries: List[Any]) -> List[Dict[str, Any]]:
        totals: Dict[str, Decimal] = defaultdict(Decimal)
        for entry in entries:
            totals[entry.egg_type] += Decimal(entry.cartons or 0)

        rows: List[Dict[str, Any]] = []
        for egg_type, label in EggType.choices:
            qty = totals.get(egg_type)
            if not qty:
                continue
            rows.append(
                {
                    "type": egg_type,
                    "label": label,
                    "cartons": qty,
                }
            )
        return rows

    def _build_session_rows(self) -> List[Dict[str, Any]]:
        sessions = sorted(
            self.batch.classification_sessions.all(),
            key=lambda session: (session.classified_at, session.pk),
        )
        session_rows: List[Dict[str, Any]] = []
        for session in sessions:
            entries = list(session.entries.all())
            entry_rows = [
                {"label": entry.get_egg_type_display(), "cartons": entry.cartons}
                for entry in entries
            ]
            total_cartons = sum((Decimal(entry.cartons or 0) for entry in entries), Decimal("0"))
            session_rows.append(
                {
                    "id": session.pk,
                    "classified_at": session.classified_at,
                    "classified_by": session.classified_by,
                    "entries": entry_rows,
                    "total": total_cartons,
                }
            )
        return session_rows


class DailyIndicatorsView(ProductionDashboardContextMixin, TemplateView):
    """Present a consolidated snapshot of daily lot indicators."""

    template_name = "production/daily_indicators.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_submenu"] = "daily_indicators"
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
            return self._handle_distribution_post(request, *args, **kwargs)

        registry_entry = self.form_registry.get(form_type)
        if not registry_entry:
            messages.error(request, "No se pudo determinar el formulario enviado.")
            return redirect("production:batches")

        form_attr, form_class = registry_entry
        form = form_class(request.POST)
        setattr(self, form_attr, form)

        if form.is_valid():
            instance = form.save()
            label_map = build_active_batch_label_map(
                BirdBatch.objects.filter(status=BirdBatch.Status.ACTIVE).only(
                    "id", "birth_date", "initial_quantity", "status"
                )
            )
            batch_label = resolve_batch_label(instance, label_map)
            messages.success(
                request,
                f"Se registró {batch_label} para {instance.farm.name}. Desde este panel puedes distribuirlo en salones.",
            )
            return redirect(f"{reverse_lazy('production:batches')}?batch={instance.pk}")

        return self.get(request, *args, **kwargs)

    def _handle_distribution_post(self, request, *args, **kwargs):
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
                BirdBatch.objects.select_related("farm", "breed")
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
        today = timezone.localdate()
        batches.sort(
            key=lambda batch: (
                batch.status != BirdBatch.Status.ACTIVE,
                -max((today - batch.birth_date).days, 0),
                -batch.initial_quantity,
                batch.pk,
            )
        )
        label_map = build_active_batch_label_map(batches)
        focused_batch_id = getattr(self, "_focused_batch_id", None)
        if focused_batch_id is None:
            focused_batch_id = self._safe_pk_lookup(BirdBatch, self.request.GET.get("batch"))

        selected_batch = self._select_batch(batches, focused_batch_id)

        batch_form = self._get_form_instance("batch")
        distribution_form = self._get_distribution_form(selected_batch)
        distribution_groups, distribution_assigned = self._build_distribution_view_data(
            distribution_form
        )
        batch_cards = self._build_batch_cards(batches, label_map)
        selected_batch_label = (
            resolve_batch_label(selected_batch, label_map) if selected_batch else None
        )

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
                "selected_batch_label": selected_batch_label,
                "batch_label_map": label_map,
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
            BirdBatch.objects.select_related("farm", "breed")
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
        self,
        batches: List[BirdBatch],
        label_map: Mapping[int, str],
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
                    label=resolve_batch_label(batch, label_map),
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

        def card_sort_key(card: BatchCard) -> Tuple[bool, int, int, int, int]:
            return (
                card["status"] != BirdBatch.Status.ACTIVE,
                -card["age_days"],
                -card["initial_quantity"],
                card["birth_date"].toordinal(),
                card["id"],
            )

        cards.sort(key=card_sort_key)
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
        total_house_area = Room.objects.aggregate(
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
            .annotate(total_area=Coalesce(Sum("rooms__area_m2"), Decimal("0")))
            .order_by("-total_area", "name")
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


class ReferenceTablesView(StaffRequiredMixin, TemplateView):
    template_name = "production/reference_tables.html"

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        if action == "create-breed":
            return self._handle_create_breed(request)
        if action == "update-metrics":
            return self._handle_update_metrics(request)
        messages.error(request, "Acción no reconocida para las tablas de referencia.")
        return redirect("production:reference-tables")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        breeds = kwargs.get("breeds")
        if breeds is None:
            breeds = self._fetch_breeds()

        selected_breed: Optional[BreedReference] = kwargs.get("selected_breed")
        if selected_breed is None:
            selected_breed = self._resolve_selected_breed(breeds)

        metrics_form: Optional[BreedWeeklyMetricsForm] = kwargs.get("metrics_form")
        if selected_breed and metrics_form is None:
            metrics_form = self._build_metrics_form(selected_breed)

        breed_form = kwargs.get("breed_form") or BreedReferenceForm()
        breed_list = [
            {
                "id": breed.pk,
                "name": breed.name,
                "week_count": getattr(breed, "week_count", 0),
                "is_selected": selected_breed.pk == breed.pk if selected_breed else False,
                "url": f"{reverse('production:reference-tables')}?breed={breed.pk}",
            }
            for breed in breeds
        ]

        selected_breed_week_count = (
            len(selected_breed.weekly_guides.all()) if selected_breed else 0
        )

        context.update(
            {
                "active_submenu": "reference_tables",
                "page_title": "Tablas de referencia",
                "breadcrumbs": [
                    {"label": "Tablas de referencia", "url": ""},
                ],
                "breeds": breed_list,
                "selected_breed": selected_breed,
                "selected_breed_week_count": selected_breed_week_count,
                "breed_form": breed_form,
                "metrics_form": metrics_form,
                "metric_columns": metrics_form.columns if metrics_form else BreedWeeklyMetricsForm.columns,
                "metrics_rows": metrics_form.iter_rows() if metrics_form else [],
                "weeks_limit": BreedWeeklyGuide.WEEK_MAX,
            }
        )
        return context

    def _handle_create_breed(self, request):
        form = BreedReferenceForm(request.POST)
        if form.is_valid():
            breed = form.save()
            messages.success(request, f'Se registró la raza "{breed.name}".')
            return redirect(self._build_redirect(breed.pk))

        return self.render_to_response(self.get_context_data(breed_form=form))

    def _handle_update_metrics(self, request):
        breed_id = self._safe_int(request.POST.get("breed_id"))
        if breed_id is None:
            messages.error(request, "Selecciona una raza antes de guardar métricas.")
            return redirect("production:reference-tables")

        breed = self._fetch_breed_with_guides(breed_id)
        if not breed:
            messages.error(request, "La raza seleccionada no existe.")
            return redirect("production:reference-tables")

        form = BreedWeeklyMetricsForm(request.POST)
        if form.is_valid():
            self._persist_weekly_metrics(breed, form.cleaned_week_data())
            messages.success(request, f"Se actualizaron las semanas de {breed.name}.")
            return redirect(self._build_redirect(breed.pk))

        return self.render_to_response(
            self.get_context_data(
                selected_breed=breed,
                metrics_form=form,
            )
        )

    def _fetch_breeds(self) -> List[BreedReference]:
        return list(
            BreedReference.objects.annotate(week_count=Count("weekly_guides")).order_by("name")
        )

    def _resolve_selected_breed(
        self, breeds: List[BreedReference]
    ) -> Optional[BreedReference]:
        candidate_id = self._safe_int(self.request.GET.get("breed"))
        if candidate_id:
            breed = self._fetch_breed_with_guides(candidate_id)
            if breed:
                return breed
        if breeds:
            return self._fetch_breed_with_guides(breeds[0].pk)
        return None

    def _build_metrics_form(self, breed: BreedReference) -> BreedWeeklyMetricsForm:
        initial_values = self._build_initial_values(breed)
        return BreedWeeklyMetricsForm(initial_values=initial_values)

    def _build_initial_values(self, breed: BreedReference) -> Dict[str, Decimal]:
        initial: Dict[str, Decimal] = {}
        for entry in breed.weekly_guides.all():
            for column in BreedWeeklyMetricsForm.columns:
                value = getattr(entry, column.key)
                if value is not None:
                    field_name = BreedWeeklyMetricsForm.build_field_name(column.key, entry.week)
                    initial[field_name] = value
        return initial

    def _persist_weekly_metrics(
        self,
        breed: BreedReference,
        week_rows: List[Tuple[int, Dict[str, Optional[Decimal]]]],
    ) -> None:
        existing_entries = {entry.week: entry for entry in breed.weekly_guides.all()}
        with transaction.atomic():
            for week, metrics in week_rows:
                has_values = any(value is not None for value in metrics.values())
                entry = existing_entries.get(week)
                if not has_values:
                    if entry:
                        entry.delete()
                    continue
                if entry:
                    updated = False
                    for field, value in metrics.items():
                        if getattr(entry, field) != value:
                            setattr(entry, field, value)
                            updated = True
                    if updated:
                        entry.save()
                else:
                    BreedWeeklyGuide.objects.create(breed=breed, week=week, **metrics)
        reset_reference_targets_cache()

    def _fetch_breed_with_guides(self, breed_id: int) -> Optional[BreedReference]:
        return (
            BreedReference.objects.prefetch_related(
                Prefetch("weekly_guides", queryset=BreedWeeklyGuide.objects.order_by("week"))
            )
            .filter(pk=breed_id)
            .first()
        )

    def _build_redirect(self, breed_id: int) -> str:
        base_url = reverse("production:reference-tables")
        return f"{base_url}?breed={breed_id}"

    @staticmethod
    def _safe_int(raw_value: Optional[str]) -> Optional[int]:
        if not raw_value:
            return None
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return None


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


class BatchProductionBoardView(StaffRequiredMixin, TemplateView):
    """Interactive board to review and edit daily production entries per lot."""

    template_name = "production/batch_production_board.html"

    def dispatch(self, request, *args, **kwargs):
        self.batch = get_object_or_404(
            BirdBatch.objects.select_related("farm", "breed"),
            pk=kwargs.get("pk"),
        )
        self.allocations = list(
            BirdBatchRoomAllocation.objects.filter(bird_batch=self.batch)
            .select_related("room__chicken_house")
            .order_by("room__chicken_house__name", "room__name")
        )
        self.total_allocated_birds = sum(allocation.quantity or 0 for allocation in self.allocations)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        self._hydrate_state()
        form: Optional[BatchDailyProductionForm] = kwargs.get("form")
        if form is None and self.allocations:
            form = BatchDailyProductionForm(
                rooms=self.room_snapshots,
                initial=self.form_initial,
            )
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "batch": self.batch,
                "batch_heading": self._build_batch_heading(),
                "active_submenu": "daily_indicators",
                "batch_navigation": self._build_batch_navigation(),
                "week_rows": self.week_rows,
                "week_navigation": self.week_navigation,
                "selected_day": self.selected_day,
                "selected_summary": self.selected_summary,
                "reference_metrics": self.reference_metrics,
                "reference_comparisons": self.reference_comparisons,
                "weekly_mortality_pct": self.weekly_mortality_pct,
                "weight_comparison": self.weight_comparison,
                "mortality_comparison": self.mortality_comparison,
                "selected_room_breakdown": self.room_breakdown_rows,
                "room_form": form if self.allocations else None,
                "room_rows": form.room_rows if form else [],
                "barn_rows": form.barn_rows if form else [],
                "selected_egg_batch": self.selected_egg_batch,
                "selected_batch_detail_url": self.selected_batch_detail_url,
                "selected_receipt_snapshot": self.selected_receipt_snapshot,
                "selected_classification_snapshot": self.selected_classification_snapshot,
                "has_rooms": bool(self.allocations),
                "week_start": self.week_start,
                "week_end": self.week_end,
                "allocated_birds": self.total_allocated_birds,
                "live_birds": self.selected_summary.get("birds"),
                "selected_avg_weight": self.selected_avg_weight,
                "selected_uniformity": self.selected_uniformity,
                "batch_age_weeks": self.batch_age_weeks,
                "batch_age_days": self.batch_age_days,
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        self._hydrate_state()
        if not self.allocations:
            messages.error(
                request,
                "Configura la distribución del lote en salones antes de registrar producción.",
            )
            return redirect(self._build_week_url(self.selected_day, self.selected_week_index))

        input_mode = request.POST.get("input_mode") or "rooms"
        form = BatchDailyProductionForm(
            rooms=self.room_snapshots,
            input_mode=input_mode,
            data=request.POST,
            initial=self.form_initial,
        )
        if form.is_valid():
            record = save_daily_room_entries(
                batch=self.batch,
                date=form.cleaned_data["date"],
                entries=form.cleaned_entries,
                average_egg_weight=form.cleaned_data.get("average_egg_weight"),
                actor=request.user if request.user.is_authenticated else None,
            )
            messages.success(
                request,
                f"Se actualizaron los datos de producción del {record.date:%d/%m/%Y}.",
            )
            target_index = max((record.date - self.batch.birth_date).days // 7, 0)
            return redirect(self._build_week_url(record.date, target_index))

        return self.render_to_response(self.get_context_data(form=form))

    def _hydrate_state(self) -> None:
        self._init_timeline()
        self.week_dates = [self.week_start + timedelta(days=index) for index in range(7)]
        self.history_records: List[ProductionRecord] = []
        records_qs = ProductionRecord.objects.filter(
            bird_batch=self.batch,
            date__range=(self.week_start, self.week_end),
        )
        self.records_map = {record.date: record for record in records_qs}
        self.selected_record = self.records_map.get(self.selected_day) or ProductionRecord.objects.filter(
            bird_batch=self.batch,
            date=self.selected_day,
        ).first()

        history_records = list(
            ProductionRecord.objects.filter(
                bird_batch=self.batch,
                date__lte=self.week_end,
            ).order_by("date")
        )
        self.history_records = history_records
        (
            self.current_birds_map,
            self.posture_birds_map,
        ) = self._build_bird_population_maps(history_records, self.week_dates)
        self.week_rows = self._build_week_rows()
        self.room_snapshots = self._build_room_snapshots()
        self.reference_metrics = self._resolve_reference_metrics()
        self.room_breakdown_rows = self._build_room_breakdown()
        self.form_initial = self._build_form_initial(self.room_snapshots, self.selected_record)
        self.selected_summary = self._build_selected_summary()
        self.selected_egg_batch = self._resolve_selected_egg_batch()
        self.selected_batch_detail_url = (
            reverse("production:egg-inventory-batch", args=[self.selected_egg_batch.pk])
            if self.selected_egg_batch
            else None
        )
        self.selected_receipt_snapshot = self._build_receipt_snapshot(self.selected_egg_batch)
        self.selected_classification_snapshot = self._build_classification_snapshot(self.selected_egg_batch)
        self.reference_comparisons = self._build_reference_comparisons()
        self.weekly_mortality_pct = self._compute_weekly_mortality_pct()
        self.week_navigation = self._build_week_navigation()
        self.batch_age_days = max((self.selected_day - self.batch.birth_date).days, 0)
        # Display the current week of life (1-based) instead of completed weeks only.
        self.batch_age_weeks = max((self.batch_age_days // 7) + 1, 1)
        avg_weight, uniformity = self._resolve_weight_snapshot(self.selected_day)
        self.selected_avg_weight = avg_weight
        self.selected_uniformity = uniformity
        self.weight_comparison = self._build_weight_comparison()
        self.mortality_comparison = self._build_mortality_comparison()

    def _build_batch_navigation(self) -> Dict[str, Optional[Dict[str, str]]]:
        """Compute previous/next active batches to allow quick navigation."""

        active_batches = list(
            BirdBatch.objects.filter(status=BirdBatch.Status.ACTIVE).only(
                "id", "birth_date", "initial_quantity"
            )
        )
        if not active_batches:
            return {"previous": None, "next": None}

        today = timezone.localdate()
        active_batches.sort(
            key=lambda batch: (
                -max((today - batch.birth_date).days, 0),
                -batch.initial_quantity,
                batch.pk,
            )
        )
        label_map = build_active_batch_label_map(active_batches)
        batch_ids = [batch.pk for batch in active_batches]
        try:
            current_index = batch_ids.index(self.batch.pk)
        except ValueError:
            return {"previous": None, "next": None}

        def serialize(target: Optional[BirdBatch]) -> Optional[Dict[str, str]]:
            if not target:
                return None
            return {
                "id": str(target.pk),
                "label": resolve_batch_label(target, label_map),
                "url": reverse("production:batch-production-board", args=[target.pk]),
            }

        previous_batch = active_batches[current_index - 1] if current_index > 0 else None
        next_batch = (
            active_batches[current_index + 1] if current_index < len(active_batches) - 1 else None
        )

        return {"previous": serialize(previous_batch), "next": serialize(next_batch)}

    def _build_batch_heading(self) -> str:
        """Return a readable heading like 'Lote Galpón 1, Galpón 2 - Granja'."""

        farm_name = self.batch.farm.name
        if not self.allocations:
            return str(self.batch)

        barns: "OrderedDict[str, None]" = OrderedDict()
        for allocation in self.allocations:
            house_name = allocation.room.chicken_house.name
            if house_name not in barns:
                barns[house_name] = None

        if not barns:
            return str(self.batch)

        barns_display = ", ".join(barns.keys())
        return f"Lote {barns_display} - {farm_name}"

    def _init_timeline(self) -> None:
        raw_day = self.request.POST.get("date") or self.request.GET.get("day")
        has_explicit_day = bool(raw_day)
        selected_day = self._parse_param_date(raw_day)
        if selected_day is None:
            selected_day = timezone.localdate()
        if selected_day < self.batch.birth_date:
            selected_day = self.batch.birth_date

        requested_week_index = self._coerce_week_index(
            self.request.POST.get("week_index") or self.request.GET.get("week_index")
        )
        week_param = self._parse_param_date(self.request.POST.get("week") or self.request.GET.get("week"))
        has_explicit_week = requested_week_index is not None or week_param is not None

        if requested_week_index is None and week_param is not None:
            requested_week_index = max((week_param - self.batch.birth_date).days // 7, 0)

        if requested_week_index is not None:
            week_start = self.batch.birth_date + timedelta(days=requested_week_index * 7)
        else:
            reference_day = selected_day if has_explicit_day else timezone.localdate()
            age_days = max((reference_day - self.batch.birth_date).days, 0)
            requested_week_index = age_days // 7
            week_start = self.batch.birth_date + timedelta(days=requested_week_index * 7)

        if week_start < self.batch.birth_date:
            week_start = self.batch.birth_date
            requested_week_index = 0

        week_end = week_start + timedelta(days=6)
        if not has_explicit_week and not has_explicit_day:
            # snap selected day to today within current week by default
            selected_day = min(max(timezone.localdate(), week_start), week_end)
        else:
            if selected_day > week_end:
                selected_day = week_end
            if selected_day < week_start:
                selected_day = week_start

        self.selected_day = selected_day
        self.week_start = week_start
        self.week_end = week_end
        self.selected_week_index = requested_week_index
        self.selected_week_number = requested_week_index + 1

    def _parse_param_date(self, raw_value: Optional[str]) -> Optional[date]:
        if not raw_value:
            return None
        parsed = parse_date(raw_value)
        return parsed

    def _coerce_week_index(self, raw_value: Optional[str]) -> Optional[int]:
        if raw_value in (None, ""):
            return None
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return None
        return max(value, 0)

    def _build_room_snapshots(self) -> List[RoomProductionSnapshot]:
        if not self.allocations:
            return []

        room_records = {
            record.room_id: record
            for record in ProductionRoomRecord.objects.select_related("room")
            .filter(
                production_record__bird_batch=self.batch,
                production_record__date=self.selected_day,
            )
        }
        room_ids = [allocation.room_id for allocation in self.allocations]
        mortality_totals = (
            ProductionRoomRecord.objects.filter(
                production_record__bird_batch=self.batch,
                room_id__in=room_ids,
                production_record__date__lte=self.selected_day,
            )
            .values("room_id")
            .annotate(total=Sum("mortality"))
        )
        room_mortality_map = {
            entry["room_id"]: int(entry["total"] or 0) for entry in mortality_totals
        }

        snapshots: List[RoomProductionSnapshot] = []
        for allocation in self.allocations:
            room = allocation.room
            room_record = room_records.get(room.pk)
            allocated_birds = allocation.quantity or 0
            cumulative_mortality = room_mortality_map.get(room.pk, 0)
            current_birds = max(allocated_birds - cumulative_mortality, 0)
            snapshots.append(
                RoomProductionSnapshot(
                    room_id=room.pk,
                    room_name=room.name,
                    chicken_house_id=room.chicken_house_id,
                    chicken_house_name=room.chicken_house.name,
                    allocated_birds=allocated_birds,
                    current_birds=current_birds,
                    production=room_record.production if room_record else None,
                    consumption=room_record.consumption if room_record else None,
                    mortality=room_record.mortality if room_record else None,
                    discard=room_record.discard if room_record else None,
                )
            )
        return snapshots

    def _build_form_initial(
        self,
        snapshots: List[RoomProductionSnapshot],
        record: Optional[ProductionRecord],
    ) -> Dict[str, Any]:
        initial: Dict[str, Any] = {"date": self.selected_day}
        if record and record.average_egg_weight is not None:
            initial["average_egg_weight"] = record.average_egg_weight

        barn_totals: Dict[int, Dict[str, Decimal | int]] = {}

        for snapshot in snapshots:
            prefix = f"room_{snapshot.room_id}"
            if snapshot.production is not None:
                initial[f"{prefix}_production"] = self._round_to_int(snapshot.production)
            if snapshot.consumption is not None:
                initial[f"{prefix}_consumption"] = self._round_to_int(snapshot.consumption)
            if snapshot.mortality is not None:
                initial[f"{prefix}_mortality"] = snapshot.mortality
            if snapshot.discard is not None:
                initial[f"{prefix}_discard"] = snapshot.discard

            barn = barn_totals.setdefault(
                snapshot.chicken_house_id,
                {
                    "production": Decimal("0"),
                    "consumption": Decimal("0"),
                    "mortality": 0,
                    "discard": 0,
                },
            )
            if snapshot.production is not None:
                barn["production"] += Decimal(snapshot.production)
            if snapshot.consumption is not None:
                barn["consumption"] += Decimal(snapshot.consumption)
            if snapshot.mortality is not None:
                barn["mortality"] += snapshot.mortality
            if snapshot.discard is not None:
                barn["discard"] += snapshot.discard

        for barn_id, totals in barn_totals.items():
            prefix = f"barn_{barn_id}"
            if totals["production"]:
                initial[f"{prefix}_production"] = self._round_to_int(totals["production"])
            if totals["consumption"]:
                initial[f"{prefix}_consumption"] = self._round_to_int(totals["consumption"])
            if totals["mortality"]:
                initial[f"{prefix}_mortality"] = totals["mortality"]
            if totals["discard"]:
                initial[f"{prefix}_discard"] = totals["discard"]

        return initial

    def _build_week_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for day in self.week_dates:
            record = self.records_map.get(day)
            birds = self.current_birds_map.get(day, self._allocated_population())
            posture_birds = self.posture_birds_map.get(day, birds)
            production_cartons = self._normalize_metric(
                record.production if record else None,
                Decimal("30"),
                1,
            )
            consumption_bags = self._normalize_metric(
                record.consumption if record else None,
                Decimal("40"),
                2,
            )
            egg_weight_per_egg = self._normalize_metric(
                record.average_egg_weight if record else None,
                Decimal("300"),
                2,
            )
            rows.append(
                {
                    "date": day,
                    "url": self._build_week_url(day, self.selected_week_index),
                    "is_selected": day == self.selected_day,
                    "production": record.production if record else None,
                    "consumption": record.consumption if record else None,
                    "production_cartons": production_cartons,
                    "consumption_bags": consumption_bags,
                    "hen_day": self._hen_day_pct(record, posture_birds),
                    "feed_per_bird": self._feed_per_bird(record, birds),
                    "average_egg_weight": record.average_egg_weight if record else None,
                    "egg_weight_per_egg": egg_weight_per_egg,
                    "mortality": record.mortality if record else None,
                    "discard": record.discard if record else None,
                }
            )
        return rows

    def _build_room_breakdown(self) -> List[Dict[str, Any]]:
        if not self.room_snapshots:
            return []

        metrics_map = {}
        reference_metrics = getattr(self, "reference_metrics", {})
        if isinstance(reference_metrics, dict):
            metrics_map = reference_metrics.get("metrics", {}) or {}
        posture_pct_ref = self._to_decimal(metrics_map.get("posture_percentage"))
        decimal_eggs_per_carton = Decimal("30")
        decimal_percentage = Decimal("100")
        cartons_quantizer = Decimal("0.1")
        posture_quantizer = Decimal("0.01")

        breakdown: List[Dict[str, Any]] = []
        for snapshot in self.room_snapshots:
            birds = snapshot.current_birds or 0
            production_eggs = snapshot.production
            consumption_value = snapshot.consumption

            production_cartons = self._normalize_metric(
                production_eggs,
                decimal_eggs_per_carton,
                1,
            )
            consumption_bags = self._normalize_metric(
                consumption_value,
                Decimal("40"),
                2,
            )
            hen_day_value = self._hen_day_from_value(production_eggs, birds)
            feed_per_bird_value = self._feed_per_bird_from_value(consumption_value, birds)

            actual_eggs_decimal = self._to_decimal(production_eggs)
            actual_cartons_decimal = (
                actual_eggs_decimal / decimal_eggs_per_carton if actual_eggs_decimal is not None else None
            )
            reference_cartons = None
            if posture_pct_ref is not None and birds > 0:
                reference_eggs = (Decimal(birds) * posture_pct_ref) / decimal_percentage
                reference_cartons = reference_eggs / decimal_eggs_per_carton
            cartons_diff = None
            if actual_cartons_decimal is not None and reference_cartons is not None:
                cartons_diff = (actual_cartons_decimal - reference_cartons).quantize(
                    cartons_quantizer,
                    rounding=ROUND_HALF_UP,
                )

            hen_day_decimal = self._to_decimal(hen_day_value)
            hen_day_diff = None
            if hen_day_decimal is not None and posture_pct_ref is not None:
                hen_day_diff = (hen_day_decimal - posture_pct_ref).quantize(
                    posture_quantizer,
                    rounding=ROUND_HALF_UP,
                )

            breakdown.append(
                {
                    "room_name": snapshot.room_name,
                    "chicken_house_name": snapshot.chicken_house_name,
                    "birds": birds,
                    "production_eggs": production_eggs,
                    "production_cartons": production_cartons,
                    "consumption_bags": consumption_bags,
                    "hen_day": hen_day_value,
                    "feed_per_bird": feed_per_bird_value,
                    "mortality": snapshot.mortality,
                    "discard": snapshot.discard,
                    "cartons_diff": cartons_diff,
                    "hen_day_diff": hen_day_diff,
                }
            )
        return breakdown

    def _build_bird_population_maps(
        self,
        history_records: List[ProductionRecord],
        days: List[date],
    ) -> Tuple[Dict[date, int], Dict[date, int]]:
        starting_population = self._allocated_population()
        result: Dict[date, int] = {}
        posture_result: Dict[date, int] = {}
        running_losses = 0
        running_mortality = 0
        pointer = 0
        total_records = len(history_records)
        for day in days:
            while pointer < total_records and history_records[pointer].date <= day:
                record = history_records[pointer]
                daily_mortality = int(record.mortality or 0)
                daily_discard = int(record.discard or 0)
                running_losses += daily_mortality + daily_discard
                running_mortality += daily_mortality
                pointer += 1
            result[day] = max(starting_population - running_losses, 0)
            posture_result[day] = max(starting_population - running_mortality, 0)
        return result, posture_result

    def _build_week_navigation(self) -> Dict[str, Any]:
        prev_url = (
            self._build_week_url(
                self.week_start - timedelta(days=7),
                self.selected_week_index - 1,
            )
            if self.selected_week_index > 0
            else None
        )
        next_url = self._build_week_url(
            self.week_start + timedelta(days=7),
            self.selected_week_index + 1,
        )
        return {
            "life_label": f"Semana de vida #{self.selected_week_number}",
            "range_label": f"{self.week_start:%d %b} – {self.week_end:%d %b}",
            "previous_url": prev_url,
            "next_url": next_url,
        }

    def _build_week_url(self, day: date, week_index: Optional[int] = None) -> str:
        if day < self.batch.birth_date:
            day = self.batch.birth_date
        target_index = week_index if week_index is not None else self.selected_week_index
        base_url = reverse("production:batch-production-board", args=[self.batch.pk])
        params = {
            "week_index": str(max(target_index, 0)),
            "day": day.isoformat(),
        }
        return f"{base_url}?{urlencode(params)}"

    def _resolve_reference_metrics(self) -> Dict[str, Any]:
        target_week = min(max(self.selected_week_number, 1), BreedWeeklyGuide.WEEK_MAX)
        guide = (
            BreedWeeklyGuide.objects.filter(breed=self.batch.breed, week=target_week)
            .only(
                "posture_percentage",
                "egg_weight_g",
                "grams_per_bird",
                "weekly_mortality_percentage",
                "body_weight_g",
            )
            .first()
        )
        metrics = {
            "posture_percentage": guide.posture_percentage if guide else None,
            "egg_weight_g": guide.egg_weight_g if guide else None,
            "grams_per_bird": guide.grams_per_bird if guide else None,
            "weekly_mortality_percentage": guide.weekly_mortality_percentage if guide else None,
            "body_weight_g": guide.body_weight_g if guide else None,
        }
        return {
            "week": target_week,
            "has_data": guide is not None,
            "metrics": metrics,
        }

    def _build_reference_comparisons(self) -> List[Dict[str, str]]:
        summary = self.selected_summary
        metrics = self.reference_metrics.get("metrics", {})
        record: Optional[ProductionRecord] = summary.get("record")
        posture_population = Decimal(
            self.posture_birds_map.get(self.selected_day, self._allocated_population()) or 0
        )
        birds_population = Decimal(
            self.current_birds_map.get(self.selected_day, self._allocated_population()) or 0
        )

        posture_pct_ref = self._to_decimal(metrics.get("posture_percentage"))
        grams_per_bird_ref = self._to_decimal(metrics.get("grams_per_bird"))
        egg_weight_ref = self._to_decimal(metrics.get("egg_weight_g"))

        posture_pct_actual = self._to_decimal(summary.get("hen_day"))
        actual_cartons = self._to_decimal(summary.get("production_cartons"))
        actual_eggs = self._to_decimal(summary.get("production_eggs"))
        actual_bags = self._to_decimal(summary.get("consumption_bags"))
        actual_feed_per_bird = self._to_decimal(summary.get("feed_per_bird"))
        actual_egg_weight = self._to_decimal(summary.get("egg_weight_per_egg"))

        if actual_eggs is None and record and record.production is not None:
            actual_eggs = self._to_decimal(record.production)
        if actual_cartons is None and actual_eggs is not None:
            actual_cartons = actual_eggs / Decimal("30")
        if actual_bags is None and record and record.consumption is not None:
            actual_consumption = self._to_decimal(record.consumption)
            if actual_consumption is not None:
                actual_bags = actual_consumption / Decimal("40")
        if actual_feed_per_bird is None and record and record.consumption is not None:
            feed_value = self._feed_per_bird(record, int(birds_population))
            actual_feed_per_bird = self._to_decimal(feed_value)
        if actual_egg_weight is None and record and record.average_egg_weight is not None:
            actual_egg_weight = self._to_decimal(record.average_egg_weight)

        reference_eggs = (
            (posture_population * posture_pct_ref) / Decimal("100")
            if posture_pct_ref is not None
            else None
        )
        reference_cartons = (
            reference_eggs / Decimal("30") if reference_eggs is not None else None
        )
        reference_consumption_bags = (
            (grams_per_bird_ref * birds_population) / Decimal("40000")
            if grams_per_bird_ref is not None
            else None
        )

        comparisons = [
            self._make_comparison("% postura", posture_pct_actual, posture_pct_ref, 1, "%"),
            self._make_comparison(
                "Producción total (cartones)",
                actual_cartons,
                reference_cartons,
                1,
                " crt",
                use_thousands=True,
            ),
            self._make_comparison(
                "Producción total (huevos)",
                actual_eggs,
                reference_eggs,
                0,
                " h",
                use_thousands=True,
            ),
            self._make_comparison("Gr/Huevo", actual_egg_weight, egg_weight_ref, 1, " g"),
            self._make_comparison(
                "Consumo total (bultos)",
                actual_bags,
                reference_consumption_bags,
                2,
                " b",
                use_thousands=True,
            ),
            self._make_comparison(
                "Consumo gr/ave", actual_feed_per_bird, grams_per_bird_ref, 0, " g"
            ),
        ]
        return comparisons

    def _build_weight_comparison(self) -> Dict[str, str]:
        metrics = self.reference_metrics.get("metrics", {})
        actual = self._to_decimal(self.selected_avg_weight)
        reference = self._to_decimal(metrics.get("body_weight_g"))
        return self._make_comparison("Peso promedio", actual, reference, 1, " g")

    def _build_mortality_comparison(self) -> Dict[str, str]:
        metrics = self.reference_metrics.get("metrics", {})
        actual = self._to_decimal(self.weekly_mortality_pct)
        reference = self._to_decimal(metrics.get("weekly_mortality_percentage"))
        return self._make_comparison("% Mortalidad semanal", actual, reference, 2, "%")

    def _compute_weekly_mortality_pct(self) -> Optional[float]:
        weekly_records = [
            record
            for record in (self.history_records or [])
            if self.week_start <= record.date <= self.week_end
        ]
        weekly_losses = sum(int(record.mortality or 0) for record in weekly_records)
        base_population = self._week_start_population()
        if base_population <= 0:
            return None
        try:
            return float((Decimal(weekly_losses) / Decimal(base_population)) * Decimal("100"))
        except (ArithmeticError, InvalidOperation):
            return None

    def _week_start_population(self) -> int:
        starting_population = self._allocated_population()
        losses_before_week = 0
        for record in self.history_records or []:
            if record.date >= self.week_start:
                break
            losses_before_week += int(record.mortality or 0) + int(record.discard or 0)
        return max(starting_population - losses_before_week, 0)

    def _make_comparison(
        self,
        label: str,
        actual: Optional[Decimal],
        reference: Optional[Decimal],
        precision: int,
        suffix: str,
        use_thousands: bool = False,
    ) -> Dict[str, str]:
        diff: Optional[Decimal] = None
        if actual is not None and reference is not None:
            diff = actual - reference
        return {
            "label": label,
            "actual_display": self._format_value(actual, precision, suffix, use_thousands),
            "reference_display": self._format_value(reference, precision, suffix, use_thousands),
            "diff_display": (
                self._format_value(diff, precision, suffix, use_thousands, show_sign=True)
                if diff is not None
                else "—"
            ),
        }

    @staticmethod
    def _format_value(
        value: Optional[Decimal],
        precision: int,
        suffix: str,
        use_thousands: bool = False,
        show_sign: bool = False,
    ) -> str:
        if value is None:
            return "—"
        quantizer = Decimal("1").scaleb(-precision) if precision > 0 else Decimal("1")
        quantized = value.quantize(quantizer, rounding=ROUND_HALF_UP)
        if precision == 0:
            fmt_spec = ",.0f" if use_thousands else ".0f"
        else:
            fmt_spec = f",.{precision}f" if use_thousands else f".{precision}f"
        formatted = format(quantized, fmt_spec)
        if show_sign:
            if quantized > 0:
                formatted = f"+{formatted}"
            elif quantized == 0:
                formatted = format(quantized, fmt_spec)
        return f"{formatted}{suffix}"

    @staticmethod
    def _to_decimal(value: Any) -> Optional[Decimal]:
        if value in (None, ""):
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    def _build_selected_summary(self) -> Dict[str, Any]:
        record = self.records_map.get(self.selected_day) or self.selected_record
        birds = self.current_birds_map.get(self.selected_day, self._allocated_population())
        posture_birds = self.posture_birds_map.get(self.selected_day, birds)
        return {
            "record": record,
            "birds": birds,
            "hen_day": self._hen_day_pct(record, posture_birds),
            "feed_per_bird": self._feed_per_bird(record, birds),
            "production_eggs": self._round_to_int(record.production) if record else None,
            "production_cartons": self._normalize_metric(
                record.production if record else None,
                Decimal("30"),
                1,
            ),
            "consumption_bags": self._normalize_metric(
                record.consumption if record else None,
                Decimal("40"),
                2,
            ),
            "egg_weight_per_egg": self._normalize_metric(
                record.average_egg_weight if record else None,
                Decimal("300"),
                2,
            ),
        }

    def _resolve_selected_egg_batch(self) -> Optional[EggClassificationBatch]:
        record = self.selected_record
        if not record:
            return None
        return (
            EggClassificationBatch.objects.select_related(
                "production_record",
                "confirmed_by",
                "classified_by",
            )
            .prefetch_related("classification_entries")
            .filter(production_record=record)
            .first()
        )

    def _build_receipt_snapshot(
        self,
        batch: Optional[EggClassificationBatch],
    ) -> Optional[Dict[str, Any]]:
        if not batch:
            return None
        return {
            "reported_cartons": batch.reported_cartons,
            "received_cartons": batch.received_cartons,
            "pending_cartons": batch.pending_cartons,
            "notes": (batch.notes or "").strip(),
            "status_label": batch.get_status_display(),
            "confirmed_at": self._local_datetime(batch.confirmed_at),
            "confirmed_by": self._display_user(batch.confirmed_by),
        }

    def _build_classification_snapshot(
        self,
        batch: Optional[EggClassificationBatch],
    ) -> Optional[Dict[str, Any]]:
        if not batch:
            return None
        entries = list(batch.classification_entries.all())
        entry_map: Dict[str, Decimal] = {entry.egg_type: Decimal(entry.cartons or 0) for entry in entries}
        entry_rows: List[Dict[str, Any]] = []
        classified_total = batch.classified_total
        for egg_type, label in EggType.choices:
            qty = entry_map.get(egg_type)
            if not qty:
                continue
            percentage = None
            if classified_total > 0:
                percentage = (qty / classified_total) * Decimal("100")
            entry_rows.append({"label": label, "cartons": qty, "percentage": percentage})
        classified_at = self._local_datetime(batch.classified_at)
        classification_date = classified_at.date() if classified_at else None
        delay_days: Optional[int] = None
        if classification_date:
            delay_days = (classification_date - batch.production_date).days
        return {
            "entries": entry_rows,
            "classified_cartons": batch.classified_total,
            "classified_at": classified_at,
            "classification_date": classification_date,
            "delay_days": delay_days,
            "classifier_name": self._display_user(batch.classified_by),
        }

    @staticmethod
    def _local_datetime(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if timezone.is_naive(value):
            return value
        return timezone.localtime(value)

    @staticmethod
    def _display_user(user: Any) -> Optional[str]:
        if not user:
            return None
        full_name = getattr(user, "get_full_name", None)
        if callable(full_name):
            value = full_name()
            if value:
                return value
        short_name = getattr(user, "get_short_name", None)
        if callable(short_name):
            short_value = short_name()
            if short_value:
                return short_value
        username = getattr(user, "username", None)
        if username:
            return username
        return str(user)

    def _allocated_population(self) -> int:
        return self.total_allocated_birds or self.batch.initial_quantity or 0

    @staticmethod
    def _round_to_int(value: Decimal | int | None) -> int:
        if value in (None, ""):
            return 0
        if isinstance(value, int):
            return value
        try:
            decimal_value = Decimal(value)
        except (ArithmeticError, ValueError, TypeError):
            return 0
        return int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _normalize_metric(
        value: Decimal | int | float | None,
        divisor: Decimal,
        precision: int,
    ) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            decimal_value = Decimal(value) / divisor
        except (ArithmeticError, InvalidOperation, ValueError, TypeError):
            return None
        quantizer = Decimal("1").scaleb(-precision) if precision > 0 else Decimal("1")
        try:
            normalized = decimal_value.quantize(quantizer, rounding=ROUND_HALF_UP)
        except (ArithmeticError, InvalidOperation):
            return None
        return float(normalized)

    def _resolve_weight_snapshot(self, target_day: date) -> tuple[Optional[float], Optional[float]]:
        room_ids = [allocation.room_id for allocation in self.allocations]
        filter_condition = Q(production_record__bird_batch=self.batch)
        if room_ids:
            filter_condition |= Q(room_id__in=room_ids)

        sessions = (
            WeightSampleSession.objects.filter(date=target_day)
            .filter(filter_condition)
            .only("uniformity_percent", "sample_size", "average_grams")
        )

        uniformity_weight = Decimal("0")
        uniformity_samples = 0
        weight_sum = Decimal("0")
        weight_samples = 0

        for session in sessions:
            sample_size = session.sample_size or 0
            if sample_size <= 0:
                continue
            if session.uniformity_percent is not None:
                uniformity_weight += Decimal(session.uniformity_percent) * sample_size
                uniformity_samples += sample_size
            if session.average_grams is not None:
                weight_sum += Decimal(session.average_grams) * sample_size
                weight_samples += sample_size

        avg_weight = float(weight_sum / weight_samples) if weight_samples else None
        avg_uniformity = float(uniformity_weight / uniformity_samples) if uniformity_samples else None
        return (avg_weight, avg_uniformity)

    def _hen_day_pct(self, record: Optional[ProductionRecord], birds: int) -> Optional[float]:
        production = record.production if record else None
        return self._hen_day_from_value(production, birds)

    def _feed_per_bird(self, record: Optional[ProductionRecord], birds: int) -> Optional[float]:
        consumption = record.consumption if record else None
        return self._feed_per_bird_from_value(consumption, birds)

    @staticmethod
    def _hen_day_from_value(production: Optional[Decimal | int | float], birds: int) -> Optional[float]:
        if not production or birds <= 0:
            return None
        try:
            return float((Decimal(production) / Decimal(birds)) * Decimal("100"))
        except (ArithmeticError, InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _feed_per_bird_from_value(
        consumption: Optional[Decimal | int | float],
        birds: int,
    ) -> Optional[float]:
        if consumption in (None, "") or birds <= 0:
            return None
        try:
            return float((Decimal(consumption) * Decimal("1000")) / Decimal(birds))
        except (ArithmeticError, InvalidOperation, ValueError, TypeError):
            return None


batch_production_board_view = BatchProductionBoardView.as_view()
egg_inventory_dashboard_view = EggInventoryDashboardView.as_view()
egg_inventory_cardex_view = EggInventoryCardexView.as_view()
egg_inventory_batch_detail_view = EggInventoryBatchDetailView.as_view()
daily_indicators_view = DailyIndicatorsView.as_view()
infrastructure_home_view = InfrastructureHomeView.as_view()
farm_update_view = FarmUpdateView.as_view()
chicken_house_update_view = ChickenHouseUpdateView.as_view()
room_update_view = RoomUpdateView.as_view()
farm_delete_view = FarmDeleteView.as_view()
chicken_house_delete_view = ChickenHouseDeleteView.as_view()
room_delete_view = RoomDeleteView.as_view()
reference_tables_view = ReferenceTablesView.as_view()
batch_management_view = BatchManagementView.as_view()
bird_batch_update_view = BirdBatchUpdateView.as_view()
bird_batch_delete_view = BirdBatchDeleteView.as_view()
batch_allocation_delete_view = BatchAllocationDeleteView.as_view()
