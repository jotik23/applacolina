from datetime import date, timedelta
from typing import List, TypedDict

from django.views.generic import TemplateView
from django.utils import timezone


class ProductionStats(TypedDict):
    name: str
    location: str
    total_birds: int
    current_birds: int
    average_posture: float
    average_mortality_rate: float
    notes: str


class SalonMetrics(TypedDict):
    label: str
    initial_birds: int
    current_birds: int
    breed: str
    age_weeks: int
    posture_percent: float
    accumulated_mortality: int
    daily_mortality_avg: float
    last_update: date


class GalponMetrics(TypedDict):
    name: str
    salons: List[SalonMetrics]


class FarmOverview(TypedDict):
    name: str
    code: str
    summary: ProductionStats
    barns: List[GalponMetrics]


class ProductionHomeView(TemplateView):
    """Render the landing page for the poultry production module."""

    template_name = "production/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        base_date = date.today()
        farms: List[FarmOverview] = [
            {
                "name": "Granja San Lucas",
                "code": "GS-01",
                "summary": ProductionStats(
                    name="Granja San Lucas",
                    location="Madrid, Cundinamarca",
                    total_birds=184_000,
                    current_birds=178_450,
                    average_posture=92.4,
                    average_mortality_rate=0.8,
                    notes="Lotes en fase pico de postura.",
                ),
                "barns": [
                    {
                        "name": "Galpón 1",
                        "salons": [
                            SalonMetrics(
                                label="Salón 1A",
                                initial_birds=9_200,
                                current_birds=9_020,
                                breed="Hy-Line Brown",
                                age_weeks=38,
                                posture_percent=94.2,
                                accumulated_mortality=180,
                                daily_mortality_avg=2.1,
                                last_update=base_date - timedelta(days=1),
                            ),
                            SalonMetrics(
                                label="Salón 1B",
                                initial_birds=9_200,
                                current_birds=9_010,
                                breed="Hy-Line Brown",
                                age_weeks=38,
                                posture_percent=93.7,
                                accumulated_mortality=190,
                                daily_mortality_avg=2.4,
                                last_update=base_date - timedelta(days=1),
                            ),
                        ],
                    },
                    {
                        "name": "Galpón 2",
                        "salons": [
                            SalonMetrics(
                                label="Salón 2A",
                                initial_birds=8_700,
                                current_birds=8_560,
                                breed="Lohmann LSL",
                                age_weeks=22,
                                posture_percent=86.9,
                                accumulated_mortality=140,
                                daily_mortality_avg=1.8,
                                last_update=base_date - timedelta(days=2),
                            ),
                            SalonMetrics(
                                label="Salón 2B",
                                initial_birds=8_700,
                                current_birds=8_540,
                                breed="Lohmann LSL",
                                age_weeks=22,
                                posture_percent=87.4,
                                accumulated_mortality=160,
                                daily_mortality_avg=1.9,
                                last_update=base_date - timedelta(days=2),
                            ),
                        ],
                    },
                ],
            },
            {
                "name": "Granja El Porvenir",
                "code": "GP-08",
                "summary": ProductionStats(
                    name="Granja El Porvenir",
                    location="Ubaté, Cundinamarca",
                    total_birds=96_500,
                    current_birds=94_380,
                    average_posture=88.1,
                    average_mortality_rate=1.1,
                    notes="Programado cambio de lotes en seis semanas.",
                ),
                "barns": [
                    {
                        "name": "Galpón 3",
                        "salons": [
                            SalonMetrics(
                                label="Salón 3A",
                                initial_birds=7_800,
                                current_birds=7_640,
                                breed="ISA Brown",
                                age_weeks=58,
                                posture_percent=79.5,
                                accumulated_mortality=260,
                                daily_mortality_avg=3.2,
                                last_update=base_date,
                            ),
                        ],
                    }
                ],
            },
        ]

        context.update(
            {
                "farms": farms,
                "dashboard_generated_at": timezone.now(),
            }
        )
        return context


production_home_view = ProductionHomeView.as_view()
