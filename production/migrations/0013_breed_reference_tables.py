import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def _normalize_breed_name(raw_name: str | None) -> str:
    name = (raw_name or "").strip()
    return name or "Sin definir"


def populate_breed_reference(apps, schema_editor):
    BirdBatch = apps.get_model("production", "BirdBatch")
    BreedReference = apps.get_model("production", "BreedReference")
    db_alias = schema_editor.connection.alias

    breed_cache: dict[str, models.Model] = {}
    for batch in BirdBatch.objects.using(db_alias).all():
        breed_name = _normalize_breed_name(getattr(batch, "breed", None))
        breed_obj = breed_cache.get(breed_name)
        if breed_obj is None:
            breed_obj, _ = BreedReference.objects.using(db_alias).get_or_create(name=breed_name)
            breed_cache[breed_name] = breed_obj

        setattr(batch, "breed_reference", breed_obj)
        batch.save(update_fields=["breed_reference"])


def rollback_breed_reference(apps, schema_editor):
    BirdBatch = apps.get_model("production", "BirdBatch")
    db_alias = schema_editor.connection.alias

    for batch in BirdBatch.objects.select_related("breed_reference").using(db_alias).all():
        breed_obj = getattr(batch, "breed_reference", None)
        breed_name = breed_obj.name if breed_obj else "Sin definir"
        setattr(batch, "breed", breed_name)
        batch.save(update_fields=["breed"])


class Migration(migrations.Migration):

    dependencies = [
        ("production", "0012_production_records_in_eggs"),
    ]

    operations = [
        migrations.CreateModel(
            name="BreedReference",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=150, unique=True, verbose_name="Nombre")),
            ],
            options={
                "verbose_name": "Raza",
                "verbose_name_plural": "Razas",
                "ordering": ("name",),
            },
        ),
        migrations.AddField(
            model_name="birdbatch",
            name="breed_reference",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="production.breedreference",
                verbose_name="Raza",
            ),
        ),
        migrations.RunPython(populate_breed_reference, rollback_breed_reference),
        migrations.AlterField(
            model_name="birdbatch",
            name="breed",
            field=models.CharField(blank=True, max_length=150, null=True, verbose_name="Raza"),
        ),
        migrations.RemoveField(
            model_name="birdbatch",
            name="breed",
        ),
        migrations.RenameField(
            model_name="birdbatch",
            old_name="breed_reference",
            new_name="breed",
        ),
        migrations.AlterField(
            model_name="birdbatch",
            name="breed",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="batches",
                to="production.breedreference",
                verbose_name="Raza",
            ),
        ),
        migrations.CreateModel(
            name="BreedWeeklyGuide",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "week",
                    models.PositiveSmallIntegerField(
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(100),
                        ],
                        verbose_name="Semana (vida)",
                    ),
                ),
                (
                    "posture_percentage",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=5,
                        null=True,
                        verbose_name="% postura",
                    ),
                ),
                (
                    "haa",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=6, null=True, verbose_name="H.A.A"
                    ),
                ),
                (
                    "egg_weight_g",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=6,
                        null=True,
                        verbose_name="Peso huevo (g)",
                    ),
                ),
                (
                    "grams_per_bird",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=6,
                        null=True,
                        verbose_name="Gr/ave/día",
                    ),
                ),
                (
                    "cumulative_feed",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=9,
                        null=True,
                        verbose_name="Consumo alimento acumulado (kg)",
                    ),
                ),
                (
                    "conversion_index",
                    models.DecimalField(
                        blank=True,
                        decimal_places=3,
                        max_digits=6,
                        null=True,
                        verbose_name="Índice de conversión",
                    ),
                ),
                (
                    "cumulative_conversion",
                    models.DecimalField(
                        blank=True,
                        decimal_places=3,
                        max_digits=6,
                        null=True,
                        verbose_name="Conversión acumulada",
                    ),
                ),
                (
                    "weekly_mortality_percentage",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=5,
                        null=True,
                        verbose_name="% mortalidad semanal",
                    ),
                ),
                (
                    "body_weight_g",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=6,
                        null=True,
                        verbose_name="Peso corporal (g)",
                    ),
                ),
                (
                    "breed",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="weekly_guides",
                        to="production.breedreference",
                        verbose_name="Raza",
                    ),
                ),
            ],
            options={
                "verbose_name": "Guía semanal de raza",
                "verbose_name_plural": "Guías semanales de raza",
                "ordering": ("breed__name", "week"),
            },
        ),
        migrations.AddConstraint(
            model_name="breedweeklyguide",
            constraint=models.UniqueConstraint(
                fields=("breed", "week"), name="unique_breed_week_reference"
            ),
        ),
    ]
