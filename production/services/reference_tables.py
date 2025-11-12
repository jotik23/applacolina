from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Optional, Union

from production.models import BreedReference, BreedWeeklyGuide


@dataclass
class ReferenceProfile:
    consumption_base: float  # grams per bird
    consumption_slope: float
    consumption_min: float
    consumption_max: float
    mortality_base: float  # percent
    mortality_slope: float
    mortality_min: float
    mortality_max: float
    discard_base: float  # percent
    discard_slope: float
    discard_min: float
    discard_max: float
    egg_weight_base: float  # grams
    egg_weight_slope: float
    egg_weight_min: float
    egg_weight_max: float
    production_base: float  # percent
    production_slope: float
    production_min: float
    production_max: float


REFERENCE_PROFILES: Dict[str, ReferenceProfile] = {
    "Hy-Line Brown": ReferenceProfile(
        consumption_base=82.0,
        consumption_slope=0.7,
        consumption_min=70.0,
        consumption_max=120.0,
        mortality_base=0.08,
        mortality_slope=0.015,
        mortality_min=0.05,
        mortality_max=0.8,
        discard_base=0.03,
        discard_slope=0.01,
        discard_min=0.0,
        discard_max=0.4,
        egg_weight_base=46.0,
        egg_weight_slope=0.45,
        egg_weight_min=40.0,
        egg_weight_max=68.0,
        production_base=15.0,
        production_slope=2.05,
        production_min=0.0,
        production_max=96.0,
    ),
    "ISA Brown": ReferenceProfile(
        consumption_base=80.0,
        consumption_slope=0.75,
        consumption_min=70.0,
        consumption_max=118.0,
        mortality_base=0.07,
        mortality_slope=0.013,
        mortality_min=0.05,
        mortality_max=0.7,
        discard_base=0.02,
        discard_slope=0.009,
        discard_min=0.0,
        discard_max=0.35,
        egg_weight_base=45.0,
        egg_weight_slope=0.47,
        egg_weight_min=40.0,
        egg_weight_max=67.0,
        production_base=17.0,
        production_slope=2.0,
        production_min=0.0,
        production_max=95.0,
    ),
    "Lohmann LSL": ReferenceProfile(
        consumption_base=78.0,
        consumption_slope=0.8,
        consumption_min=68.0,
        consumption_max=116.0,
        mortality_base=0.08,
        mortality_slope=0.012,
        mortality_min=0.05,
        mortality_max=0.7,
        discard_base=0.02,
        discard_slope=0.008,
        discard_min=0.0,
        discard_max=0.3,
        egg_weight_base=44.0,
        egg_weight_slope=0.5,
        egg_weight_min=38.0,
        egg_weight_max=66.0,
        production_base=18.0,
        production_slope=2.05,
        production_min=0.0,
        production_max=96.0,
    ),
    "__default__": ReferenceProfile(
        consumption_base=80.0,
        consumption_slope=0.7,
        consumption_min=70.0,
        consumption_max=120.0,
        mortality_base=0.08,
        mortality_slope=0.015,
        mortality_min=0.05,
        mortality_max=0.8,
        discard_base=0.02,
        discard_slope=0.01,
        discard_min=0.0,
        discard_max=0.4,
        egg_weight_base=45.0,
        egg_weight_slope=0.45,
        egg_weight_min=38.0,
        egg_weight_max=68.0,
        production_base=16.0,
        production_slope=2.0,
        production_min=0.0,
        production_max=95.0,
    ),
}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _resolve_profile(breed_name: str) -> ReferenceProfile:
    return REFERENCE_PROFILES.get(breed_name) or REFERENCE_PROFILES["__default__"]


def _compute_profile_targets(
    profile: ReferenceProfile, age_weeks: int, current_birds: int
) -> Dict[str, float]:
    age_factor = max(age_weeks, 0)

    consumption_g = _clamp(
        profile.consumption_base + profile.consumption_slope * age_factor,
        profile.consumption_min,
        profile.consumption_max,
    )
    mortality_pct = _clamp(
        profile.mortality_base + profile.mortality_slope * age_factor,
        profile.mortality_min,
        profile.mortality_max,
    )
    discard_pct = _clamp(
        profile.discard_base + profile.discard_slope * age_factor,
        profile.discard_min,
        profile.discard_max,
    )
    egg_weight_g = _clamp(
        profile.egg_weight_base + profile.egg_weight_slope * age_factor,
        profile.egg_weight_min,
        profile.egg_weight_max,
    )
    production_percent = _clamp(
        profile.production_base + profile.production_slope * age_factor,
        profile.production_min,
        profile.production_max,
    )

    consumption_kg = round((consumption_g * current_birds) / 1000, 2) if current_birds else 0.0
    mortality_birds = round(current_birds * mortality_pct / 100, 1) if current_birds else 0.0
    discard_birds = round(current_birds * discard_pct / 100, 1) if current_birds else 0.0

    return {
        "consumption_kg": consumption_kg,
        "mortality_birds": mortality_birds,
        "discard_birds": discard_birds,
        "egg_weight_g": egg_weight_g,
        "production_percent": production_percent,
    }


def _sanitize_week(age_weeks: int) -> int:
    if age_weeks <= 0:
        return 1
    if age_weeks > BreedWeeklyGuide.WEEK_MAX:
        return BreedWeeklyGuide.WEEK_MAX
    return age_weeks


def _to_float(value: Optional[object]) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=512)
def _fetch_weekly_metrics(breed_id: int, week: int) -> Optional[Dict[str, Optional[float]]]:
    return (
        BreedWeeklyGuide.objects.filter(breed_id=breed_id, week=week)
        .values(
            "posture_percentage",
            "egg_weight_g",
            "grams_per_bird",
            "weekly_mortality_percentage",
        )
        .first()
    )


def reset_reference_targets_cache() -> None:
    """Clear cached weekly lookups after updating the reference tables."""

    _fetch_weekly_metrics.cache_clear()


BreedInput = Union[str, BreedReference]


def get_reference_targets(breed: BreedInput, age_weeks: int, current_birds: int) -> Dict[str, float]:
    """Return daily target values for a lot based on breed and age.

    The values are in lot totals where aplica:
        - consumption_kg (float)
        - mortality_birds (float)
        - discard_birds (float)
        - egg_weight_g (float)
        - production_percent (float)
    """

    breed_obj = breed if isinstance(breed, BreedReference) else None
    breed_name = breed_obj.name if breed_obj else str(breed)
    profile = _resolve_profile(breed_name)
    profile_targets = _compute_profile_targets(profile, age_weeks, current_birds)

    weekly_entry: Optional[Dict[str, Optional[float]]] = None
    if breed_obj:
        target_week = _sanitize_week(age_weeks)
        weekly_entry = _fetch_weekly_metrics(breed_obj.pk, target_week)

    consumption_kg = profile_targets["consumption_kg"]
    egg_weight_g = profile_targets["egg_weight_g"]
    mortality_birds = profile_targets["mortality_birds"]
    production_percent = profile_targets["production_percent"]

    if weekly_entry:
        grams_per_bird = _to_float(weekly_entry.get("grams_per_bird"))
        if grams_per_bird is not None and current_birds:
            consumption_kg = round((grams_per_bird * current_birds) / 1000, 2)

        egg_weight_value = _to_float(weekly_entry.get("egg_weight_g"))
        if egg_weight_value is not None:
            egg_weight_g = egg_weight_value

        mortality_pct_week = _to_float(weekly_entry.get("weekly_mortality_percentage"))
        if mortality_pct_week is not None and current_birds:
            daily_pct = mortality_pct_week / 7
            mortality_birds = round((current_birds * daily_pct) / 100, 1)

        production_value = _to_float(weekly_entry.get("posture_percentage"))
        if production_value is not None:
            production_percent = production_value

    return {
        "consumption_kg": consumption_kg,
        "mortality_birds": mortality_birds,
        "discard_birds": profile_targets["discard_birds"],
        "egg_weight_g": egg_weight_g,
        "production_percent": production_percent,
    }
