# applacolina

Plataforma Django para la gestión operativa de granjas, calendarios de turnos y automatización de asignaciones.

## Arquitectura funcional
- `personal`: núcleo del calendario, reglas de turnos/descansos y portal de operadores.
- `production`: entidades de granja, lotes de aves, registros de producción.
- `task_manager`: mini apps y tareas operativas.
- `applacolina/settings.py`: configuración central (pendiente de modularizar por entorno).

Consulta los lineamientos ampliados en `agents.md`.

## Stack y herramientas recomendadas
- **Backend:** Python 3.12+, Django 5, PostgreSQL 15.
- **Infraestructura local:** Docker + Docker Compose.
- **Calidad:** `ruff`, `mypy`, `pytest-django`, `factory_boy`, `coverage`, `pre-commit`.
- **CI/CD sugerido:** GitHub Actions con jobs de lint → tests → build → deploy.

## Puesta en marcha local
1. Instala dependencias del sistema para Docker y docker-compose v2.
2. Clona el repositorio y copia las variables base:
   ```bash
   cp .env.example .env
   ```
3. Construye e inicia los servicios:
   ```bash
   docker compose up --build
   ```
4. La aplicación queda disponible en `http://localhost:8000/`. El servicio `web` aplica migraciones al iniciar.

### Comandos de gestión (ejecutar siempre dentro del contenedor)
```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py test
```
> Nota: nunca ejecutes pruebas contra la base viva ni expongas credenciales reales en fixtures.

## Flujo de desarrollo recomendado
- Trabaja en ramas `feature/<descripcion>` o `bugfix/<descripcion>` y crea PRs pequeños.
- Usa `pre-commit` para ejecutar `ruff`, `ruff format`, `mypy` y pruebas rápidas antes de cada commit.
- Añade o actualiza tests con `pytest` al tocar reglas de negocio; preferir factories sobre fixtures estáticos.
- Documenta los cambios relevantes en `plan.md` (refactors) y en los readmes específicos por módulo cuando corresponda.

## Configuración y variables de entorno
| Variable | Entorno | Descripción |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | prod | Clave secreta; generar una distinta por despliegue. |
| `DJANGO_DEBUG` | todos | Controla modo debug (`True`/`False`). |
| `DJANGO_ALLOWED_HOSTS` | prod | Lista separada por comas con hosts permitidos. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | prod | Orígenes válidos para CSRF (ej. `https://app.midominio.com`). |
| `DATABASE_URL` | prod | Cadena Postgres completa; Railway la provee automáticamente. |
| `POSTGRES_*` | local | Credenciales para el servicio `db` definido en `docker-compose.yml`. |
| `DJANGO_SECURE_*` | prod | Configuración opcional de seguridad (HSTS, cookies seguras, SSL redirect). |

## Pruebas y cobertura
- Ejecuta suites completas con `pytest` (próxima adopción) o `manage.py test` mientras conviven.
- Mantén cobertura ≥85 % en los módulos críticos (`personal/services/scheduler.py`, APIs de calendario).
- Usa `pytest --reuse-db` para acelerar los ciclos locales.

## Despliegue (Railway u otros PaaS)
1. Configura variables `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` y `DATABASE_URL`.
2. Construye la imagen con el `Dockerfile` del repositorio.
3. Define el comando:
   ```bash
   gunicorn applacolina.wsgi:application --bind 0.0.0.0:${PORT}
   ```
4. Ejecuta migraciones en un job previo al despliegue (o manualmente: `docker run --rm imagen python manage.py migrate`).
5. Configura monitoreo y alertas (Sentry, logs en JSON, health checks).

## Documentación adicional
- `agents.md`: manual operativo y estándares de ingeniería.
- `README - *.md`: notas históricas por módulo; migrarlas gradualmente a `/docs`.
- `plan.md`: hoja de ruta viva con refactors incrementales (ver sección de Roadmap).
