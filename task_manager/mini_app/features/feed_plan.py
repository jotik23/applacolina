from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import Dict, Iterable, List, Optional, Sequence

from django.db.models import Prefetch, Sum
from django.utils.formats import date_format

from personal.models import UserProfile
from production.models import BirdBatch, BirdBatchRoomAllocation, ProductionRoomRecord
from production.services.reference_tables import get_reference_targets

from .production_registry import resolve_assignment_for_date

BAG_WEIGHT_KG = Decimal("40")
MORNING_RATIO = Decimal("0.65")
KG_QUANTIZER = Decimal("0.01")
BAG_QUANTIZER = Decimal("0.01")
GRAM_QUANTIZER = Decimal("0.01")
RATIO_QUANTIZER = Decimal("0.01")


@dataclass(frozen=True)
class FeedRoomPlan:
    room_id: int
    room_name: str
    lot_label: str
    birds: int
    feed_kg: Decimal
    feed_bags: Decimal
    grams_per_bird: Decimal


@dataclass(frozen=True)
class FeedHousePlan:
    house_id: int
    house_name: str
    farm_name: Optional[str]
    birds: int
    feed_kg: Decimal
    feed_bags: Decimal
    rooms: tuple[FeedRoomPlan, ...]


@dataclass(frozen=True)
class FeedLotSummary:
    label: str
    breed: Optional[str]
    age_weeks: int
    birds: int
    grams_per_bird: Decimal
    feed_kg: Decimal


@dataclass(frozen=True)
class FeedSummary:
    birds: int
    feed_kg: Decimal
    feed_bags: Decimal
    grams_per_bird: Decimal


@dataclass(frozen=True)
class FeedDistributionSnapshot:
    total_kg: Decimal
    total_bags: Decimal
    morning_kg: Decimal
    morning_bags: Decimal
    morning_ratio: Decimal
    afternoon_kg: Decimal
    afternoon_bags: Decimal
    afternoon_ratio: Decimal


@dataclass(frozen=True)
class FeedDistributionOption:
    direction: str  # "up" or "down"
    total_bags: int
    total_kg: Decimal
    delta_bags: Decimal
    delta_kg: Decimal
    morning_bags: int
    morning_kg: Decimal
    morning_ratio: Decimal
    afternoon_bags: int
    afternoon_kg: Decimal
    afternoon_ratio: Decimal


@dataclass(frozen=True)
class FeedPlan:
    date: date
    position_label: Optional[str]
    farm_name: Optional[str]
    chicken_house_name: Optional[str]
    houses: tuple[FeedHousePlan, ...]
    lots: tuple[FeedLotSummary, ...]
    summary: FeedSummary
    bag_weight_kg: Decimal
    exact_distribution: FeedDistributionSnapshot
    options: tuple[FeedDistributionOption, ...]
    recommended_option: Optional[FeedDistributionOption]
    recommended_grams_per_bird: Optional[Decimal]


def build_feed_plan_card(
    *,
    user: Optional[UserProfile],
    reference_date: Optional[date] = None,
) -> Optional[FeedPlan]:
    """Return the feed planning snapshot for the operator assignment."""

    if not user or not getattr(user, "is_authenticated", False):
        return None
    if not user.has_perm("task_manager.view_mini_app_feed_card"):
        return None

    target_date = reference_date or UserProfile.colombia_today()
    assignment = resolve_assignment_for_date(user=user, target_date=target_date)
    if not assignment or not assignment.position:
        return None

    position = assignment.position
    chicken_house = position.chicken_house
    if not chicken_house:
        return None

    room_ids = list(position.rooms.values_list("pk", flat=True))

    allocation_queryset = (
        BirdBatchRoomAllocation.objects.select_related("room", "room__chicken_house", "room__chicken_house__farm")
        .filter(room__chicken_house=chicken_house)
        .order_by("room__name")
    )
    if room_ids:
        allocation_queryset = allocation_queryset.filter(room_id__in=room_ids)

    batches_queryset = (
        BirdBatch.objects.filter(
            status=BirdBatch.Status.ACTIVE,
            allocations__room__chicken_house=chicken_house,
        )
        .select_related("farm", "breed")
        .prefetch_related(
            Prefetch(
                "allocations",
                queryset=allocation_queryset,
                to_attr="filtered_allocations",
            )
        )
        .order_by("pk")
        .distinct()
    )
    if room_ids:
        batches_queryset = batches_queryset.filter(allocations__room_id__in=room_ids)

    batches: list[BirdBatch] = list(batches_queryset)
    if not batches:
        return None

    filtered_room_ids = {
        allocation.room_id
        for batch in batches
        for allocation in getattr(batch, "filtered_allocations", None) or []
    }
    if not filtered_room_ids:
        return None

    batch_ids = [batch.pk for batch in batches]
    mortality_entries = (
        ProductionRoomRecord.objects.filter(
            room_id__in=filtered_room_ids,
            production_record__bird_batch_id__in=batch_ids,
            production_record__date__lte=target_date,
        )
        .values("room_id")
        .annotate(total=Sum("mortality"))
    )
    mortality_map = {entry["room_id"]: int(entry["total"] or 0) for entry in mortality_entries}

    house_payloads: Dict[int, Dict[str, object]] = {}
    lot_summaries: List[FeedLotSummary] = []
    total_weighted_grams = Decimal("0")

    for batch in batches:
        allocations = getattr(batch, "filtered_allocations", None) or []
        if not allocations:
            continue

        room_snapshots: List[tuple[BirdBatchRoomAllocation, int]] = []
        for allocation in allocations:
            allocated = allocation.quantity or 0
            mortality = mortality_map.get(allocation.room_id, 0)
            live_birds = max(allocated - mortality, 0)
            if live_birds <= 0:
                continue
            room_snapshots.append((allocation, live_birds))

        if not room_snapshots:
            continue

        birth_date = batch.birth_date
        age_days = (target_date - birth_date).days if birth_date else 0
        age_weeks = max(age_days // 7, 0)
        total_batch_birds = sum(live for _, live in room_snapshots)
        reference_targets = get_reference_targets(batch.breed, age_weeks, total_batch_birds)
        lot_feed_kg = Decimal(str(reference_targets.get("consumption_kg") or 0))
        grams_per_bird = Decimal("0")
        if total_batch_birds:
            grams_per_bird = (lot_feed_kg * Decimal("1000") / Decimal(total_batch_birds)).quantize(
                GRAM_QUANTIZER, rounding=ROUND_HALF_UP
            )

        lot_feed_accumulator = Decimal("0")
        room_plans: List[FeedRoomPlan] = []
        for allocation, live_birds in room_snapshots:
            room_feed_kg = (grams_per_bird * Decimal(live_birds) / Decimal("1000")).quantize(
                KG_QUANTIZER, rounding=ROUND_HALF_UP
            )
            lot_feed_accumulator += room_feed_kg
            feed_bags = (room_feed_kg / BAG_WEIGHT_KG).quantize(BAG_QUANTIZER, rounding=ROUND_HALF_UP)
            room_plans.append(
                FeedRoomPlan(
                    room_id=allocation.room_id,
                    room_name=allocation.room.name,
                    lot_label=str(batch),
                    birds=live_birds,
                    feed_kg=room_feed_kg,
                    feed_bags=feed_bags,
                    grams_per_bird=grams_per_bird,
                )
            )

            house = allocation.room.chicken_house
            payload = house_payloads.setdefault(
                house.pk,
                {
                    "house": house,
                    "birds": 0,
                    "feed_kg": Decimal("0"),
                    "rooms": [],
                },
            )
            payload["birds"] += live_birds
            payload["feed_kg"] += room_feed_kg
            payload["rooms"].append(room_plans[-1])

        if not room_plans:
            continue

        lot_summaries.append(
            FeedLotSummary(
                label=str(batch),
                breed=batch.breed.name if batch.breed_id else None,
                age_weeks=age_weeks,
                birds=total_batch_birds,
                grams_per_bird=grams_per_bird,
                feed_kg=lot_feed_accumulator.quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP),
            )
        )
        total_weighted_grams += grams_per_bird * Decimal(total_batch_birds)

    if not house_payloads:
        return None

    house_plans: List[FeedHousePlan] = []
    for house_id, payload in house_payloads.items():
        house = payload["house"]
        birds = int(payload["birds"])
        feed_kg = Decimal(payload["feed_kg"]).quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP)
        rooms: Sequence[FeedRoomPlan] = tuple(
            sorted(payload["rooms"], key=lambda room_plan: (room_plan.lot_label, room_plan.room_name))
        )
        house_plans.append(
            FeedHousePlan(
                house_id=house_id,
                house_name=house.name,
                farm_name=house.farm.name if house.farm_id else None,
                birds=birds,
                feed_kg=feed_kg,
                feed_bags=(feed_kg / BAG_WEIGHT_KG).quantize(BAG_QUANTIZER, rounding=ROUND_HALF_UP),
                rooms=rooms,
            )
        )

    house_plans.sort(key=lambda plan: plan.house_name)
    total_birds = sum(plan.birds for plan in house_plans)
    total_feed_kg = sum(plan.feed_kg for plan in house_plans)
    total_feed_kg = total_feed_kg.quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP)
    grams_per_bird = Decimal("0")
    if total_birds:
        grams_per_bird = (total_weighted_grams / Decimal(total_birds)).quantize(
            GRAM_QUANTIZER, rounding=ROUND_HALF_UP
        )

    summary = FeedSummary(
        birds=total_birds,
        feed_kg=total_feed_kg,
        feed_bags=(total_feed_kg / BAG_WEIGHT_KG).quantize(BAG_QUANTIZER, rounding=ROUND_HALF_UP),
        grams_per_bird=grams_per_bird,
    )

    exact_distribution = _build_exact_distribution(total_feed_kg)
    options = tuple(_build_distribution_options(total_feed_kg))
    recommended_option = None
    if options:
        recommended_option = min(options, key=lambda option: abs(option.delta_kg))

    recommended_grams = None
    if recommended_option and summary.birds:
        recommended_grams = (recommended_option.total_kg * Decimal("1000") / Decimal(summary.birds)).quantize(
            GRAM_QUANTIZER, rounding=ROUND_HALF_UP
        )

    return FeedPlan(
        date=target_date,
        position_label=position.name,
        farm_name=position.farm.name if position.farm_id else None,
        chicken_house_name=chicken_house.name,
        houses=tuple(house_plans),
        lots=tuple(sorted(lot_summaries, key=lambda lot: lot.label)),
        summary=summary,
        bag_weight_kg=BAG_WEIGHT_KG,
        exact_distribution=exact_distribution,
        options=options,
        recommended_option=recommended_option,
        recommended_grams_per_bird=recommended_grams,
    )


def serialize_feed_plan_card(plan: FeedPlan) -> dict[str, object]:
    """Return a JSON-safe payload for templates."""

    rounded_grams = plan.recommended_grams_per_bird
    grams_difference: Optional[float] = None
    if rounded_grams is not None:
        grams_difference = float(rounded_grams - plan.summary.grams_per_bird)

    payload = {
        "date": plan.date.isoformat(),
        "date_label": date_format(plan.date, "DATE_FORMAT"),
        "weekday_label": date_format(plan.date, "l").capitalize(),
        "position_label": plan.position_label,
        "farm": plan.farm_name,
        "chicken_house": plan.chicken_house_name,
        "totals": {
            "birds": plan.summary.birds,
            "feed_kg": float(plan.summary.feed_kg),
            "feed_bags": float(plan.summary.feed_bags),
            "grams_per_bird": float(plan.summary.grams_per_bird),
        },
        "reference": {
            "grams_per_bird": float(plan.summary.grams_per_bird),
            "rounded_grams_per_bird": float(rounded_grams) if rounded_grams is not None else None,
            "grams_difference": grams_difference,
            "bag_weight_kg": float(plan.bag_weight_kg),
            "lots": [
                {
                    "label": lot.label,
                    "breed": lot.breed,
                    "age_weeks": lot.age_weeks,
                    "birds": lot.birds,
                    "grams_per_bird": float(lot.grams_per_bird),
                    "feed_kg": float(lot.feed_kg),
                }
                for lot in plan.lots
            ],
        },
        "houses": [
            {
                "id": house.house_id,
                "label": house.house_name,
                "farm": house.farm_name,
                "summary": {
                    "birds": house.birds,
                    "feed_kg": float(house.feed_kg),
                    "feed_bags": float(house.feed_bags),
                },
                "rooms": [
                    {
                        "id": room.room_id,
                        "label": room.room_name,
                        "lot_label": room.lot_label,
                        "birds": room.birds,
                        "feed_kg": float(room.feed_kg),
                        "feed_bags": float(room.feed_bags),
                        "grams_per_bird": float(room.grams_per_bird),
                        "dosing": _build_room_dosing(room, bag_weight=plan.bag_weight_kg),
                    }
                    for room in house.rooms
                ],
            }
            for house in plan.houses
        ],
        "distribution": {
            "exact": _serialize_distribution(plan.exact_distribution),
            "options": [_serialize_option(option) for option in plan.options],
            "recommended": _serialize_option(plan.recommended_option) if plan.recommended_option else None,
        },
    }
    return payload


def _serialize_distribution(snapshot: FeedDistributionSnapshot) -> dict[str, object]:
    return {
        "total_kg": float(snapshot.total_kg),
        "total_bags": float(snapshot.total_bags),
        "morning_kg": float(snapshot.morning_kg),
        "morning_bags": float(snapshot.morning_bags),
        "morning_ratio": float(snapshot.morning_ratio),
        "morning_percentage": float(snapshot.morning_ratio * Decimal("100")),
        "afternoon_kg": float(snapshot.afternoon_kg),
        "afternoon_bags": float(snapshot.afternoon_bags),
        "afternoon_ratio": float(snapshot.afternoon_ratio),
        "afternoon_percentage": float(snapshot.afternoon_ratio * Decimal("100")),
    }


def _serialize_option(option: Optional[FeedDistributionOption]) -> Optional[dict[str, object]]:
    if option is None:
        return None
    return {
        "direction": option.direction,
        "total_bags": option.total_bags,
        "total_kg": float(option.total_kg),
        "delta_bags": float(option.delta_bags),
        "delta_kg": float(option.delta_kg),
        "morning_bags": option.morning_bags,
        "morning_kg": float(option.morning_kg),
        "morning_ratio": float(option.morning_ratio),
        "morning_percentage": float(option.morning_ratio * Decimal("100")),
        "afternoon_bags": option.afternoon_bags,
        "afternoon_kg": float(option.afternoon_kg),
        "afternoon_ratio": float(option.afternoon_ratio),
        "afternoon_percentage": float(option.afternoon_ratio * Decimal("100")),
    }


def _build_room_dosing(room: FeedRoomPlan, bag_weight: Decimal) -> dict[str, dict[str, dict[str, float]]]:
    return {
        "ideal": _serialize_decimal_dosing(room.feed_kg, bag_weight),
        "min": _serialize_integer_dosing(room.feed_bags, bag_weight, rounding=ROUND_DOWN),
        "max": _serialize_integer_dosing(room.feed_bags, bag_weight, rounding=ROUND_UP),
    }


def _serialize_decimal_dosing(feed_kg: Decimal, bag_weight: Decimal) -> dict[str, dict[str, float]]:
    if feed_kg <= 0:
        return {
            "am": {"bags": 0.0, "kg": 0.0},
            "pm": {"bags": 0.0, "kg": 0.0},
        }

    morning_kg = (feed_kg * MORNING_RATIO).quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP)
    afternoon_kg = (feed_kg - morning_kg).quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP)
    morning_bags = (morning_kg / bag_weight).quantize(BAG_QUANTIZER, rounding=ROUND_HALF_UP)
    afternoon_bags = (afternoon_kg / bag_weight).quantize(BAG_QUANTIZER, rounding=ROUND_HALF_UP)
    return {
        "am": {"bags": float(morning_bags), "kg": float(morning_kg)},
        "pm": {"bags": float(afternoon_bags), "kg": float(afternoon_kg)},
    }


def _serialize_integer_dosing(feed_bags: Decimal, bag_weight: Decimal, *, rounding: str) -> dict[str, dict[str, float]]:
    bag_count = int(feed_bags.to_integral_value(rounding=rounding))
    return _serialize_split_integer_bags(bag_count, bag_weight)


def _serialize_split_integer_bags(bag_total: int, bag_weight: Decimal) -> dict[str, dict[str, float]]:
    if bag_total <= 0:
        return {
            "am": {"bags": 0.0, "kg": 0.0},
            "pm": {"bags": 0.0, "kg": 0.0},
        }

    morning_bags = _split_bags(bag_total)
    afternoon_bags = bag_total - morning_bags
    morning_kg = (bag_weight * morning_bags).quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP)
    afternoon_kg = (bag_weight * afternoon_bags).quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP)
    return {
        "am": {"bags": float(morning_bags), "kg": float(morning_kg)},
        "pm": {"bags": float(afternoon_bags), "kg": float(afternoon_kg)},
    }


def _build_exact_distribution(total_feed_kg: Decimal) -> FeedDistributionSnapshot:
    total_bags = (total_feed_kg / BAG_WEIGHT_KG).quantize(BAG_QUANTIZER, rounding=ROUND_HALF_UP)
    morning_kg = (total_feed_kg * MORNING_RATIO).quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP)
    afternoon_kg = (total_feed_kg - morning_kg).quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP)
    morning_bags = (morning_kg / BAG_WEIGHT_KG).quantize(BAG_QUANTIZER, rounding=ROUND_HALF_UP)
    afternoon_bags = (afternoon_kg / BAG_WEIGHT_KG).quantize(BAG_QUANTIZER, rounding=ROUND_HALF_UP)
    total_bags_value = total_bags if total_bags else Decimal("0.00")
    morning_ratio = (
        (morning_kg / total_feed_kg).quantize(RATIO_QUANTIZER, rounding=ROUND_HALF_UP) if total_feed_kg else Decimal("0")
    )
    afternoon_ratio = Decimal("1") - morning_ratio if total_feed_kg else Decimal("0")
    return FeedDistributionSnapshot(
        total_kg=total_feed_kg,
        total_bags=total_bags_value,
        morning_kg=morning_kg,
        morning_bags=morning_bags,
        morning_ratio=morning_ratio,
        afternoon_kg=afternoon_kg,
        afternoon_bags=afternoon_bags,
        afternoon_ratio=afternoon_ratio,
    )


def _build_distribution_options(total_feed_kg: Decimal) -> Iterable[FeedDistributionOption]:
    if total_feed_kg <= 0:
        return []

    total_bags_exact = total_feed_kg / BAG_WEIGHT_KG
    floor_bags = int(total_bags_exact.to_integral_value(rounding=ROUND_DOWN))
    ceil_bags = floor_bags if total_bags_exact == total_bags_exact.to_integral_value(rounding=ROUND_DOWN) else floor_bags + 1

    options: List[FeedDistributionOption] = []
    if floor_bags > 0 and total_bags_exact != floor_bags:
        options.append(_build_option(total_feed_kg, floor_bags, direction="down"))
    if ceil_bags > floor_bags:
        options.append(_build_option(total_feed_kg, ceil_bags, direction="up"))
    return options


def _build_option(total_feed_kg: Decimal, bag_count: int, *, direction: str) -> FeedDistributionOption:
    total_kg = (BAG_WEIGHT_KG * bag_count).quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP)
    delta_kg = (total_kg - total_feed_kg).quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP)
    total_bags_exact = total_feed_kg / BAG_WEIGHT_KG
    delta_bags = (Decimal(bag_count) - total_bags_exact).quantize(BAG_QUANTIZER, rounding=ROUND_HALF_UP)
    morning_bags = _split_bags(bag_count)
    afternoon_bags = bag_count - morning_bags
    morning_kg = (BAG_WEIGHT_KG * morning_bags).quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP)
    afternoon_kg = (BAG_WEIGHT_KG * afternoon_bags).quantize(KG_QUANTIZER, rounding=ROUND_HALF_UP)
    morning_ratio = (
        (Decimal(morning_bags) / Decimal(bag_count)).quantize(RATIO_QUANTIZER, rounding=ROUND_HALF_UP)
        if bag_count
        else Decimal("0")
    )
    afternoon_ratio = Decimal("1") - morning_ratio if bag_count else Decimal("0")
    return FeedDistributionOption(
        direction=direction,
        total_bags=bag_count,
        total_kg=total_kg,
        delta_bags=delta_bags,
        delta_kg=delta_kg,
        morning_bags=morning_bags,
        morning_kg=morning_kg,
        morning_ratio=morning_ratio,
        afternoon_bags=afternoon_bags,
        afternoon_kg=afternoon_kg,
        afternoon_ratio=afternoon_ratio,
    )


def _split_bags(total_bags: int) -> int:
    if total_bags <= 0:
        return 0
    ideal_morning = Decimal(total_bags) * MORNING_RATIO
    morning_bags = int(ideal_morning.to_integral_value(rounding=ROUND_HALF_UP))
    return min(morning_bags, total_bags)
