from datetime import date, timedelta
from typing import List, TypedDict

from django.views.generic import TemplateView
from django.utils import timezone

from applacolina.mixins import StaffRequiredMixin


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
    feed_today_grams: int
    weekly_feed_kg: int
    mortality_week: int
    mortality_percentage: float
    last_update: date


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
    uniformity: float
    avg_weight: float
    target_weight: float
    feed_today_grams: int
    weekly_feed_kg: int
    total_feed_to_date_kg: int
    barns: List[BarnAllocation]
    mortality: List[MortalityRecord]
    weight_trend: List[WeightTrendPoint]
    consumption_history: List[ConsumptionRecord]
    egg_mix: List[EggSizeRecord]
    alerts: List[str]
    notes: str


class FarmSummary(TypedDict):
    location: str
    total_birds: int
    current_birds: int
    posture_percent: float
    mortality_percent: float
    average_uniformity: float
    eggs_per_day: int
    feed_weekly_kg: float


class FarmOverview(TypedDict):
    name: str
    code: str
    summary: FarmSummary
    lots: List[LotOverview]


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

        base_date = date.today()
        farms: List[FarmOverview] = [
            {
                "name": "Granja San Lucas",
                "code": "GS-01",
                "summary": FarmSummary(
                    location="Madrid, Cundinamarca",
                    total_birds=184_000,
                    current_birds=178_450,
                    posture_percent=92.4,
                    mortality_percent=0.8,
                    average_uniformity=91.2,
                    eggs_per_day=172_100,
                    feed_weekly_kg=131_500,
                ),
                "lots": [
                    LotOverview(
                        label="Lote 1A",
                        breed="Hy-Line Brown",
                        birth_date=base_date - timedelta(weeks=38),
                        age_weeks=38,
                        initial_birds=9_200,
                        current_birds=9_020,
                        bird_balance=180,
                        uniformity=91.8,
                        avg_weight=1.78,
                        target_weight=1.80,
                        feed_today_grams=112,
                        weekly_feed_kg=7_020,
                        total_feed_to_date_kg=312_480,
                        barns=[
                            BarnAllocation(
                                name="Galpón 1",
                                segment="Nave central",
                                initial_birds=4_600,
                                current_birds=4_520,
                                occupancy_rate=98.3,
                                feed_today_grams=112,
                                weekly_feed_kg=3_520,
                                mortality_week=6,
                                mortality_percentage=0.13,
                                last_update=base_date - timedelta(days=1),
                            ),
                            BarnAllocation(
                                name="Galpón 4",
                                segment="Extensión oriente",
                                initial_birds=4_600,
                                current_birds=4_500,
                                occupancy_rate=97.8,
                                feed_today_grams=112,
                                weekly_feed_kg=3_500,
                                mortality_week=6,
                                mortality_percentage=0.13,
                                last_update=base_date - timedelta(days=1),
                            ),
                        ],
                        mortality=[
                            MortalityRecord(label="Semana actual", quantity=12, percentage=0.13),
                            MortalityRecord(label="Últimas 4 semanas", quantity=46, percentage=0.50),
                            MortalityRecord(label="Año en curso", quantity=180, percentage=1.96),
                        ],
                        weight_trend=[
                            WeightTrendPoint(week=36, actual_weight=1.74, projected_weight=1.75),
                            WeightTrendPoint(week=37, actual_weight=1.76, projected_weight=1.78),
                            WeightTrendPoint(week=38, actual_weight=1.78, projected_weight=1.80),
                        ],
                        consumption_history=[
                            ConsumptionRecord(week=36, feed_kg=6_930, grams_per_bird=108),
                            ConsumptionRecord(week=37, feed_kg=6_980, grams_per_bird=110),
                            ConsumptionRecord(week=38, feed_kg=7_020, grams_per_bird=112),
                        ],
                        egg_mix=[
                            EggSizeRecord(size="M", percentage=34.0, avg_weight=61),
                            EggSizeRecord(size="L", percentage=39.0, avg_weight=64),
                            EggSizeRecord(size="XL", percentage=19.0, avg_weight=69),
                            EggSizeRecord(size="Jumbo", percentage=8.0, avg_weight=72),
                        ],
                        alerts=[
                            "Seguimiento de uniformidad: -0.7 pts vs meta semanal",
                        ],
                        notes="Curva de peso alineada al plan; se recomiendan ajustes menores en consumo vespertino.",
                    ),
                    LotOverview(
                        label="Lote 1B",
                        breed="Hy-Line Brown",
                        birth_date=base_date - timedelta(weeks=38, days=3),
                        age_weeks=38,
                        initial_birds=9_200,
                        current_birds=9_010,
                        bird_balance=190,
                        uniformity=92.6,
                        avg_weight=1.79,
                        target_weight=1.80,
                        feed_today_grams=111,
                        weekly_feed_kg=6_980,
                        total_feed_to_date_kg=311_940,
                        barns=[
                            BarnAllocation(
                                name="Galpón 2",
                                segment="Sector occidente",
                                initial_birds=4_600,
                                current_birds=4_510,
                                occupancy_rate=98.0,
                                feed_today_grams=111,
                                weekly_feed_kg=3_490,
                                mortality_week=7,
                                mortality_percentage=0.16,
                                last_update=base_date - timedelta(days=1),
                            ),
                            BarnAllocation(
                                name="Galpón 3",
                                segment="Sector norte",
                                initial_birds=4_600,
                                current_birds=4_500,
                                occupancy_rate=97.8,
                                feed_today_grams=111,
                                weekly_feed_kg=3_490,
                                mortality_week=7,
                                mortality_percentage=0.16,
                                last_update=base_date - timedelta(days=1),
                            ),
                        ],
                        mortality=[
                            MortalityRecord(label="Semana actual", quantity=14, percentage=0.15),
                            MortalityRecord(label="Últimas 4 semanas", quantity=49, percentage=0.53),
                            MortalityRecord(label="Año en curso", quantity=190, percentage=2.07),
                        ],
                        weight_trend=[
                            WeightTrendPoint(week=36, actual_weight=1.73, projected_weight=1.75),
                            WeightTrendPoint(week=37, actual_weight=1.77, projected_weight=1.78),
                            WeightTrendPoint(week=38, actual_weight=1.79, projected_weight=1.80),
                        ],
                        consumption_history=[
                            ConsumptionRecord(week=36, feed_kg=6_920, grams_per_bird=107),
                            ConsumptionRecord(week=37, feed_kg=6_950, grams_per_bird=109),
                            ConsumptionRecord(week=38, feed_kg=6_980, grams_per_bird=111),
                        ],
                        egg_mix=[
                            EggSizeRecord(size="M", percentage=32.0, avg_weight=60),
                            EggSizeRecord(size="L", percentage=41.0, avg_weight=64),
                            EggSizeRecord(size="XL", percentage=20.0, avg_weight=69),
                            EggSizeRecord(size="Jumbo", percentage=7.0, avg_weight=73),
                        ],
                        alerts=[
                            "Revisión de ventilación programada por ligera variación en consumo.",
                        ],
                        notes="Lote estable; preparar transición a programa de luz 14 h para semana 40.",
                    ),
                    LotOverview(
                        label="Lote 2A",
                        breed="Lohmann LSL",
                        birth_date=base_date - timedelta(weeks=22),
                        age_weeks=22,
                        initial_birds=8_700,
                        current_birds=8_560,
                        bird_balance=140,
                        uniformity=88.5,
                        avg_weight=1.42,
                        target_weight=1.45,
                        feed_today_grams=104,
                        weekly_feed_kg=5_460,
                        total_feed_to_date_kg=204_320,
                        barns=[
                            BarnAllocation(
                                name="Galpón 5",
                                segment="Juveniles ala norte",
                                initial_birds=4_350,
                                current_birds=4_280,
                                occupancy_rate=98.4,
                                feed_today_grams=104,
                                weekly_feed_kg=2_730,
                                mortality_week=9,
                                mortality_percentage=0.21,
                                last_update=base_date - timedelta(days=2),
                            ),
                            BarnAllocation(
                                name="Galpón 6",
                                segment="Juveniles ala sur",
                                initial_birds=4_350,
                                current_birds=4_280,
                                occupancy_rate=98.4,
                                feed_today_grams=104,
                                weekly_feed_kg=2_730,
                                mortality_week=9,
                                mortality_percentage=0.21,
                                last_update=base_date - timedelta(days=2),
                            ),
                        ],
                        mortality=[
                            MortalityRecord(label="Semana actual", quantity=18, percentage=0.21),
                            MortalityRecord(label="Últimas 4 semanas", quantity=65, percentage=0.74),
                            MortalityRecord(label="Año en curso", quantity=140, percentage=1.61),
                        ],
                        weight_trend=[
                            WeightTrendPoint(week=20, actual_weight=1.37, projected_weight=1.38),
                            WeightTrendPoint(week=21, actual_weight=1.40, projected_weight=1.42),
                            WeightTrendPoint(week=22, actual_weight=1.42, projected_weight=1.45),
                        ],
                        consumption_history=[
                            ConsumptionRecord(week=20, feed_kg=5_360, grams_per_bird=100),
                            ConsumptionRecord(week=21, feed_kg=5_410, grams_per_bird=102.5),
                            ConsumptionRecord(week=22, feed_kg=5_460, grams_per_bird=104),
                        ],
                        egg_mix=[
                            EggSizeRecord(size="M", percentage=46.0, avg_weight=58),
                            EggSizeRecord(size="L", percentage=31.0, avg_weight=61),
                            EggSizeRecord(size="XL", percentage=14.0, avg_weight=66),
                            EggSizeRecord(size="Huevos pequeños", percentage=9.0, avg_weight=52),
                        ],
                        alerts=[
                            "Uniformidad por debajo del objetivo de 90%. Revisar densidad en comederos.",
                        ],
                        notes="Ajustar curva de alimentación a partir de la semana 23 según plan técnico.",
                    ),
                    LotOverview(
                        label="Lote 2B",
                        breed="Lohmann LSL",
                        birth_date=base_date - timedelta(weeks=22, days=5),
                        age_weeks=22,
                        initial_birds=8_700,
                        current_birds=8_540,
                        bird_balance=160,
                        uniformity=89.3,
                        avg_weight=1.41,
                        target_weight=1.45,
                        feed_today_grams=103,
                        weekly_feed_kg=5_420,
                        total_feed_to_date_kg=203_660,
                        barns=[
                            BarnAllocation(
                                name="Galpón 7",
                                segment="Juveniles transición",
                                initial_birds=4_350,
                                current_birds=4_270,
                                occupancy_rate=98.2,
                                feed_today_grams=103,
                                weekly_feed_kg=2_710,
                                mortality_week=10,
                                mortality_percentage=0.24,
                                last_update=base_date - timedelta(days=2),
                            ),
                            BarnAllocation(
                                name="Galpón 8",
                                segment="Recrudecidos",
                                initial_birds=4_350,
                                current_birds=4_270,
                                occupancy_rate=98.2,
                                feed_today_grams=103,
                                weekly_feed_kg=2_710,
                                mortality_week=10,
                                mortality_percentage=0.24,
                                last_update=base_date - timedelta(days=2),
                            ),
                        ],
                        mortality=[
                            MortalityRecord(label="Semana actual", quantity=20, percentage=0.23),
                            MortalityRecord(label="Últimas 4 semanas", quantity=67, percentage=0.77),
                            MortalityRecord(label="Año en curso", quantity=160, percentage=1.84),
                        ],
                        weight_trend=[
                            WeightTrendPoint(week=20, actual_weight=1.36, projected_weight=1.38),
                            WeightTrendPoint(week=21, actual_weight=1.39, projected_weight=1.42),
                            WeightTrendPoint(week=22, actual_weight=1.41, projected_weight=1.45),
                        ],
                        consumption_history=[
                            ConsumptionRecord(week=20, feed_kg=5_320, grams_per_bird=99),
                            ConsumptionRecord(week=21, feed_kg=5_380, grams_per_bird=101.5),
                            ConsumptionRecord(week=22, feed_kg=5_420, grams_per_bird=103),
                        ],
                        egg_mix=[
                            EggSizeRecord(size="M", percentage=45.0, avg_weight=57),
                            EggSizeRecord(size="L", percentage=33.0, avg_weight=61),
                            EggSizeRecord(size="XL", percentage=15.0, avg_weight=65),
                            EggSizeRecord(size="Huevos pequeños", percentage=7.0, avg_weight=51),
                        ],
                        alerts=[
                            "Planificar traslado a área de postura intensiva en 2 semanas.",
                        ],
                        notes="Preparar seguimiento de consumo de agua junto al nuevo sistema IoT.",
                    ),
                ],
            },
            {
                "name": "Granja El Porvenir",
                "code": "GP-08",
                "summary": FarmSummary(
                    location="Ubaté, Cundinamarca",
                    total_birds=96_500,
                    current_birds=94_380,
                    posture_percent=88.1,
                    mortality_percent=1.1,
                    average_uniformity=89.4,
                    eggs_per_day=87_950,
                    feed_weekly_kg=72_800,
                ),
                "lots": [
                    LotOverview(
                        label="Lote 3A",
                        breed="ISA Brown",
                        birth_date=base_date - timedelta(weeks=58),
                        age_weeks=58,
                        initial_birds=7_800,
                        current_birds=7_640,
                        bird_balance=160,
                        uniformity=86.3,
                        avg_weight=1.91,
                        target_weight=1.94,
                        feed_today_grams=118,
                        weekly_feed_kg=7_140,
                        total_feed_to_date_kg=412_680,
                        barns=[
                            BarnAllocation(
                                name="Galpón 9",
                                segment="Producción alta",
                                initial_birds=3_900,
                                current_birds=3_820,
                                occupancy_rate=97.9,
                                feed_today_grams=118,
                                weekly_feed_kg=3_570,
                                mortality_week=13,
                                mortality_percentage=0.34,
                                last_update=base_date,
                            ),
                            BarnAllocation(
                                name="Galpón 10",
                                segment="Producción baja",
                                initial_birds=3_900,
                                current_birds=3_820,
                                occupancy_rate=97.9,
                                feed_today_grams=118,
                                weekly_feed_kg=3_570,
                                mortality_week=13,
                                mortality_percentage=0.34,
                                last_update=base_date,
                            ),
                        ],
                        mortality=[
                            MortalityRecord(label="Semana actual", quantity=26, percentage=0.33),
                            MortalityRecord(label="Últimas 4 semanas", quantity=92, percentage=1.18),
                            MortalityRecord(label="Año en curso", quantity=260, percentage=3.33),
                        ],
                        weight_trend=[
                            WeightTrendPoint(week=56, actual_weight=1.88, projected_weight=1.92),
                            WeightTrendPoint(week=57, actual_weight=1.90, projected_weight=1.93),
                            WeightTrendPoint(week=58, actual_weight=1.91, projected_weight=1.94),
                        ],
                        consumption_history=[
                            ConsumptionRecord(week=56, feed_kg=7_100, grams_per_bird=116),
                            ConsumptionRecord(week=57, feed_kg=7_120, grams_per_bird=117),
                            ConsumptionRecord(week=58, feed_kg=7_140, grams_per_bird=118),
                        ],
                        egg_mix=[
                            EggSizeRecord(size="L", percentage=38.0, avg_weight=65),
                            EggSizeRecord(size="XL", percentage=34.0, avg_weight=70),
                            EggSizeRecord(size="Jumbo", percentage=16.0, avg_weight=74),
                            EggSizeRecord(size="Doble yema", percentage=12.0, avg_weight=82),
                        ],
                        alerts=[
                            "Plan de renovación de lote en 6 semanas.",
                            "Monitorear mortalidad: +0.2 pts vs promedio histórico.",
                        ],
                        notes="Ante el próximo reemplazo preparar cronograma de desinfección profunda.",
                    ),
                ],
            },
        ]

        all_lots: List[LotOverview] = []
        total_barn_allocations = 0
        for farm in farms:
            for lot in farm["lots"]:
                lot["bird_balance"] = lot["initial_birds"] - lot["current_birds"]
                lot["barn_count"] = len(lot["barns"])
                barn_names = [allocation["name"] for allocation in lot["barns"]]
                lot["barn_names_display"] = ", ".join(barn_names) if barn_names else "Sin asignación"
                total_barn_allocations += lot["barn_count"]
                all_lots.append(lot)

        total_lots = len(all_lots)
        total_initial_birds = sum(lot["initial_birds"] for lot in all_lots)
        total_current_birds = sum(lot["current_birds"] for lot in all_lots)
        total_weekly_feed = sum(lot["weekly_feed_kg"] for lot in all_lots)
        total_feed_to_date = int(sum(lot["total_feed_to_date_kg"] for lot in all_lots))
        average_uniformity = (
            round(sum(lot["uniformity"] for lot in all_lots) / total_lots, 1)
            if total_lots
            else 0.0
        )
        average_weight = (
            round(sum(lot["avg_weight"] for lot in all_lots) / total_lots, 2)
            if total_lots
            else 0.0
        )

        global_metrics: List[ScorecardMetric] = [
            ScorecardMetric(
                label="Aves activas",
                value=f"{total_current_birds:,}".replace(",", "."),
                delta=1.2,
                is_positive=True,
                description="Variación quincenal contra plan operativo.",
            ),
            ScorecardMetric(
                label="Consumo semanal total (kg)",
                value=f"{int(total_weekly_feed):,}".replace(",", "."),
                delta=0.8,
                is_positive=True,
                description="Incremento controlado en línea con fase de postura.",
            ),
            ScorecardMetric(
                label="Uniformidad promedio",
                value=f"{average_uniformity}%",
                delta=-0.4,
                is_positive=False,
                description="Seguimiento semanal sobre desviación estándar de peso.",
            ),
            ScorecardMetric(
                label="Peso vivo promedio (kg)",
                value=f"{average_weight}",
                delta=0.02,
                is_positive=True,
                description="Comparativo con proyección genética del ciclo.",
            ),
        ]

        upcoming_milestones: List[UpcomingMilestone] = [
            UpcomingMilestone(
                title="Renovación Lote 3A",
                detail="Preparar retiro y limpieza profunda · Granja El Porvenir",
                due_on=base_date + timedelta(weeks=6),
            ),
            UpcomingMilestone(
                title="Auditoría de bioseguridad",
                detail="Verificación de protocolos · Granja San Lucas · Galpón 2",
                due_on=base_date + timedelta(days=10),
            ),
            UpcomingMilestone(
                title="Instalación sensores ambientales",
                detail="Integración IoT para consumo y confort térmico · Galpón 1",
                due_on=base_date + timedelta(days=21),
            ),
        ]

        barn_options = sorted(
            {
                f'{allocation["name"]} · {farm["name"]}'
                for farm in farms
                for lot in farm["lots"]
                for allocation in lot["barns"]
            }
        )

        filters = FilterConfig(
            farms=[farm["name"] for farm in farms],
            barns=barn_options,
            ranges=[
                "Últimas 4 semanas",
                "Últimos 3 meses",
                "Ciclo completo",
                "Personalizado…",
            ],
            breeds=sorted({lot["breed"] for lot in all_lots}),
            egg_sizes=["Huevos pequeños", "M", "L", "XL", "Jumbo", "Doble yema"],
        )

        context.update(
            {
                "farms": farms,
                "global_metrics": global_metrics,
                "filters": filters,
                "upcoming_milestones": upcoming_milestones,
                "total_lots": total_lots,
                "total_barn_allocations": total_barn_allocations,
                "total_initial_birds": total_initial_birds,
                "total_current_birds": total_current_birds,
                "total_feed_to_date": total_feed_to_date,
                "dashboard_generated_at": timezone.now(),
            }
        )
        return context


production_home_view = ProductionHomeView.as_view()
