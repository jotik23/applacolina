from dataclasses import dataclass
from typing import Dict


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


def get_reference_targets(breed: str, age_weeks: int, current_birds: int) -> Dict[str, float]:
    """Return daily target values for a lot based on breed and age.

    The values are in lot totals where aplica:
        - consumption_kg (float)
        - mortality_birds (float)
        - discard_birds (float)
        - egg_weight_g (float)
        - production_percent (float)
    """

    profile = REFERENCE_PROFILES.get(breed) or REFERENCE_PROFILES["__default__"]
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
