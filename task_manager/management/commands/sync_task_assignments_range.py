from __future__ import annotations

from contextlib import nullcontext
from datetime import date, timedelta
from typing import Iterable, Optional, Tuple

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max, Min
from django.utils import timezone

from personal.models import ShiftCalendar
from task_manager.models import TaskDefinition
from task_manager.services import suppress_task_assignment_sync, sync_task_assignments


class Command(BaseCommand):
    help = "Genera o regenera TaskAssignment dentro de un rango de fechas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            dest="start_date",
            help="Fecha inicial (YYYY-MM-DD). Si se omite, se calcula con base en los calendarios existentes.",
        )
        parser.add_argument(
            "--end",
            dest="end_date",
            help="Fecha final (YYYY-MM-DD). Si se omite, se calcula con base en los calendarios existentes.",
        )
        parser.add_argument(
            "--chunk-size",
            dest="chunk_size",
            type=int,
            default=31,
            help="Cantidad de días por iteración (default: 31).",
        )
        parser.add_argument(
            "--suppress-signals",
            dest="suppress_signals",
            action="store_true",
            default=False,
            help="Desactiva las señales automáticas durante la sincronización manual.",
        )

    def handle(self, *args, **options):
        chunk_size: int = options["chunk_size"]
        if chunk_size <= 0:
            raise CommandError("El parámetro --chunk-size debe ser un entero positivo.")

        start_date, end_date = self._resolve_range(
            options.get("start_date"),
            options.get("end_date"),
        )

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Sincronizando asignaciones de tareas del {start_date.isoformat()} al {end_date.isoformat()}"
            )
        )

        context_manager = suppress_task_assignment_sync if options["suppress_signals"] else nullcontext

        processed_days = 0
        with context_manager():
            for window_start, window_end in self._iterate_windows(start_date, end_date, chunk_size):
                sync_task_assignments(start_date=window_start, end_date=window_end)
                processed_days += (window_end - window_start).days + 1
                self.stdout.write(
                    self.style.HTTP_INFO(
                        f"  → Sincronizado rango {window_start.isoformat()} - {window_end.isoformat()}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(f"Sincronización completada. Días procesados: {processed_days}.")
        )

    def _resolve_range(
        self,
        start_label: Optional[str],
        end_label: Optional[str],
    ) -> Tuple[date, date]:
        start_date = self._parse_date(start_label) if start_label else None
        end_date = self._parse_date(end_label) if end_label else None

        bounds = self._detect_default_bounds()
        default_start, default_end = bounds

        start_date = start_date or default_start or timezone.localdate()
        end_date = end_date or default_end or timezone.localdate()

        if start_date > end_date:
            raise CommandError("La fecha inicial no puede ser posterior a la final.")

        return start_date, end_date

    def _detect_default_bounds(self) -> Tuple[Optional[date], Optional[date]]:
        calendar_bounds = ShiftCalendar.objects.aggregate(
            min_start=Min("start_date"),
            max_end=Max("end_date"),
        )
        task_bounds = TaskDefinition.objects.aggregate(
            min_scheduled=Min("scheduled_for"),
            max_scheduled=Max("scheduled_for"),
        )

        min_candidate: Iterable[Optional[date]] = (
            calendar_bounds.get("min_start"),
            task_bounds.get("max_scheduled"),
        )
        max_candidate: Iterable[Optional[date]] = (
            calendar_bounds.get("max_end"),
            task_bounds.get("min_scheduled"),
        )

        default_start = self._min_date(min_candidate)
        default_end = self._max_date(max_candidate)

        today = timezone.localdate()
        if default_start and default_start > today:
            default_start = today
        if default_end and default_end < today:
            default_end = today
        if default_start and default_end and default_end < default_start:
            default_end = default_start

        return default_start, default_end

    @staticmethod
    def _iterate_windows(start_date: date, end_date: date, chunk_size: int):
        cursor = start_date
        delta = timedelta(days=chunk_size - 1)
        while cursor <= end_date:
            window_end = min(cursor + delta, end_date)
            yield cursor, window_end
            cursor = window_end + timedelta(days=1)

    @staticmethod
    def _parse_date(value: str) -> date:
        try:
            return date.fromisoformat(value)
        except (ValueError, TypeError):
            raise CommandError(f"La fecha '{value}' no tiene el formato esperado (YYYY-MM-DD).")

    @staticmethod
    def _min_date(values: Iterable[Optional[date]]) -> Optional[date]:
        candidates = [value for value in values if value is not None]
        return min(candidates) if candidates else None

    @staticmethod
    def _max_date(values: Iterable[Optional[date]]) -> Optional[date]:
        candidates = [value for value in values if value is not None]
        return max(candidates) if candidates else None
