from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("personal", "0024_add_vacunador_category"),
        ("task_manager", "0017_taskassignment_allow_orphans"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskAssignmentEvidence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="task_assignment_evidence/%Y/%m/%d", verbose_name="Archivo")),
                (
                    "media_type",
                    models.CharField(
                        choices=[("photo", "Fotografía"), ("video", "Video"), ("other", "Otro")],
                        default="photo",
                        max_length=16,
                        verbose_name="Tipo de medio",
                    ),
                ),
                ("note", models.CharField(blank=True, max_length=255, verbose_name="Nota")),
                ("content_type", models.CharField(blank=True, max_length=120, verbose_name="Tipo de contenido")),
                ("file_size", models.PositiveIntegerField(default=0, verbose_name="Tamaño (bytes)")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True, verbose_name="Cargado en")),
                (
                    "assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="evidences",
                        to="task_manager.taskassignment",
                        verbose_name="Asignación",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="uploaded_task_assignment_evidence",
                        to="personal.userprofile",
                        verbose_name="Cargado por",
                    ),
                ),
            ],
            options={
                "verbose_name": "Evidencia de tarea",
                "verbose_name_plural": "Evidencias de tareas",
                "ordering": ("-uploaded_at",),
            },
        ),
    ]
