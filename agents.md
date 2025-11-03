# Proyecto `applacolina` – Manual para agentes de desarrollo

## 0. Coordinación entre agentes
- Asume que hay varias sesiones de Codex trabajando en paralelo; antes de editar revisa `git status` y los diffs relevantes para entender el contexto vigente.
- Evita sobrescribir o revertir cambios ajenos: si detectas modificaciones recientes en las mismas rutas, integra tu trabajo de forma incremental (usa `git add -p` o actualiza bloques concretos en lugar de reescribir archivos completos).
- Deja trazabilidad breve en `plan.md` o en el mensaje final cuando tomes decisiones que afecten a otras áreas, así los siguientes agentes comprenden el estado del trabajo.
- Ante tareas concretas que no pidan refactor explícito, evalúa si puedes introducir refactors incrementales alineados con este manual (renombrar métodos, extraer lógica, separar archivos, añadir tests, etc.) sin desviar el objetivo principal.
- Tras cada guardado valida otra vez con `git status` que solo se incluyen tus cambios y sincroniza con el repositorio remoto cuando aplique antes de iniciar nuevas tareas.
- Si surge un conflicto inevitable, pausa y comunica la situación en el canal indicado por la coordinación (o en el resumen final) para que otro agente pueda resolverlo sin perder avances.

## 1. Panorama del sistema
- Plataforma Django 5 que agrupa varias apps de dominio (`personal`, `production`, `task_manager`, `notifications`) bajo la configuración central `applacolina/`.
- Servicios críticos:
  - Core de calendarios y asignaciones automáticas (`personal`).
  - Gestión agrícola y de producción (`production`).
  - Integración con mini apps de Telegram y tareas (`task_manager`).
  - Eventos y mensajería (app `notifications`).
- Persistencia en PostgreSQL con infraestructura local y en Railway; los contenedores Docker montan el repositorio en `/app` para desarrollo en caliente.

## 2. Flujo operativo diario

### 2.1 Entorno y servicios
- Usa `docker compose` para cualquier comando que acceda a la base de datos o a dependencias del proyecto.
- Comprobación de servicios: `docker compose ps`.
- Inicio local:
  ```bash
  docker compose up --build
  ```
- Las variables de entorno se definen en `.env`; parte del stack (Railway) inyecta valores adicionales en despliegue.

### 2.2 Gestión con `manage.py`
- Desde el host, evita `python` y `python3`; ambos fallan por versión o resolución del host `db`.
- Forma correcta:
  ```bash
  docker compose exec web python manage.py <comando>
  ```
- Comandos frecuentes:
  - Migraciones: `docker compose exec web python manage.py migrate`
  - Estado de migraciones: `docker compose exec web python manage.py showmigrations`
  - Pruebas: `docker compose exec web python manage.py test`
- Nunca ejecutes pruebas contra la base viva; confía en la BD de pruebas que genera Django.

### 2.3 Observaciones operativas
- Los contenedores pueden requerir permisos elevados según el entorno donde se ejecute Codex CLI.
- Los cambios locales se reflejan de inmediato en el contenedor gracias al volumen compartido.

## 3. Estándares de ingeniería
- **Arquitectura modular:** mantén la lógica de dominio en servicios o módulos dedicados (p. ej. `personal/services/`) y deja las vistas como adaptadores finos. Prioriza dependencias de dominio → infraestructura, nunca al revés.
- **Tipado y linting:** todo código nuevo debe incluir `typing` explícito. Usa `ruff` (PEP 8 + reglas de calidad), `black` o `ruff format` para formato, y `mypy`/`django-stubs` para chequeo estático.
- **Gestión de dependencias:** usa `pip-tools` o `uv` para fijar versiones reproducibles. Agrupa dependencias por entorno (`base`, `dev`, `test`) y documenta cualquier paquete adicional.
- **Segregación de settings:** evoluciona `settings.py` hacia módulos por entorno (`base.py`, `local.py`, `production.py`) y aprovecha `django-environ` o `pydantic-settings` para validar configuración.
- **Seguridad:** aplica cabeceras seguras, fuerza HTTPS en producción, rota la `SECRET_KEY` y valida hosts/orígenes. Usa cuentas de servicio mínimas en PostgreSQL.
- **API y serialización:** documenta contratos JSON (especialmente para el calendario) con `drf-spectacular` o `django-ninja`. Versiona endpoints y evita romper el front existente.
- **Control de estado y concurrencia:** encapsula actualizaciones complejas de calendarios en transacciones (`transaction.atomic()`), registra eventos en `AssignmentChangeLog` y mantén reglas de bloqueo optimistas.
- **Registros y observabilidad:** centraliza logs en JSON, añade trazas de auditoría y prepara hooks para herramientas como Sentry.

### 3.1 Guías por lenguaje
- **Python:** prioriza funciones y clases pequeñas con nombres descriptivos, aplica tipado estático, documenta invariantes complejos y mantén lógica de dominio en servicios/selector/DTOs en lugar de vistas o comandos. Evita side effects en import.
- **HTML:** construye templates extendiendo layouts base, usa partials (`include`/`{% block %}`) para fragmentos repetidos y nunca mezcles lógica de negocio en el template; limita la lógica a presentación (`if`, `for` simples).
- **CSS:** organiza estilos por feature en `static/<feature>/css/`, aplica convenciones como BEM o utilidades con prefijos claros, evita `!important` salvo justificación documentada y reutiliza variables/Custom Properties cuando sea posible.
- **JavaScript:** encapsula la lógica en módulos ES6 o clases, evita variables globales, usa `data-*` para enlazar DOM y lógica, y documenta contratos de eventos/requests. Los scripts deben vivir en `static/<feature>/js/` y cargarse de forma deferida.

## 4. Calidad, pruebas y CI
- **Estrategia de pruebas:** cubre dominio crítico con `pytest-django`, `factory_boy` y `pytest-freezegun`. Aísla cada app en `tests/` dedicados y usa fixtures de base de datos mínimas.
- **Cobertura mínima:** establece objetivos (≥85 % en el generador de calendarios). Agrega suites específicas para reglas de negocio y para API.
- **CI/CD:** configura pipelines (GitHub Actions) con pasos de linting, tipado, pruebas, build de imagen y despliegue. Bloquea merges si fallan.
- **Revisión de código:** adopta PR templates que exijan impacto, pruebas realizadas y checklist de seguridad/migraciones. Exige revisiones cruzadas.
- **Datos de prueba:** usa factories y fixtures versionadas; evita cargar `backup.sql` en entornos compartidos sin anonimizar.

## 4. Estrategia de refactor incremental
- **Fase 0 · Línea base:** antes de tocar código, ejecuta la suite actual (`docker compose exec web python manage.py test`), registra fallas conocidas y mapea dependencias entre apps. Documenta hallazgos en `docs/audits/diagnostico-inicial.md`.
- **Fase 1 · Red de seguridad mínima:** crea o refuerza pruebas unitarias/funcionales solo para los flujos que se van a refactorizar primero (generación de calendarios, descansos, asignaciones). Añade factories ligeros y un checklist de smoke tests manuales. Calcula cobertura con `coverage` para tener un punto de partida.
- **Fase 2 · Modularización temprana:** reorganiza cada feature en directorios autocontenidos (`personal/calendar/`, `personal/operators/`, etc.). Expone servicios, selectors y DTOs explícitos; mantiene vistas delgadas que delegan la lógica pesada.
- **Fase 3 · Presentación limpia:** separa HTML, CSS y JS por módulo; convierte los templates en layouts base + partials reutilizables. Documenta en `docs/frontend.md` la convención de nombres y patrones para evitar “spaghetti code”.
- **Fase 4 · Contratos de API:** asegura que los endpoints JSON delegan en servicios de dominio, documenta entradas/salidas y cubre cada ruta crítica con pruebas ligeras.
- **Fases 5-7 (diferidas):** una vez estabilizado el refactor inicial, aborda la modernización de settings y dependencias (`pip-tools`/`uv`, `django-environ`), observabilidad (Sentry, health checks, métricas) y experiencia de desarrollador (`pre-commit`, `Makefile`, devcontainers). Estos pasos son futuros, no bloquean la modularización actual.
- **Revisión continua:** después de cada fase, actualiza `plan.md`, ejecuta el checklist de smoke tests y confirma que la cobertura de módulos críticos se mantiene o mejora.

## 5. Git, ramas y despliegue
- Usa ramas `feature/`, `bugfix/`, `hotfix/`, `chore/` con commits atómicos y mensajes imperativos.
- Activa `pre-commit` con hooks de formato, linting y migración-filename-check.
- Construye imágenes reproducibles con el `Dockerfile`; el entrypoint final debe ejecutar `gunicorn` y health checks.
- Para Railway u otros PaaS:
  1. Define `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`.
  2. Usa el comando `gunicorn applacolina.wsgi:application --bind 0.0.0.0:${PORT}`.
  3. Ejecuta migraciones en un paso previo o job separado.

## 6. Referencia de interfaz del calendario
- La grilla principal mantiene columnas por día y filas ordenadas con `PositionDefinition.display_order`; cada celda muestra asignación y banderas de alerta.
- La barra lateral reutiliza el JSON de calendarios para filtros por granja, galpón y categoría.
- El panel inferior lista descansos aunque no se calculen automáticamente; la estructura JSON debe mantenerse.
- Endpoints (`calendar_detail`, `calendar_generate`, `rest_periods`) conservan rutas y formatos actuales para garantizar compatibilidad con el front existente.

## 7. Registro de reglas del generador

### 2025-10-29 · Iteración de reglas
- Construye una asignación sugerida para calendarios nuevos o regenerados (sin asignaciones previas).
- Recibe nombre, fecha de inicio y fin; el rango no puede solaparse con calendarios existentes.
- Solo incluye posiciones activas en el rango (`valid_from` ≤ fecha ≤ `valid_until` o `null`).
- Si una posición tiene vigencia parcial, se generan asignaciones solo dentro del periodo válido.
- Las posiciones se llenan según `display_order`; la prioridad de asignación respeta ese orden.
- Un colaborador es elegible si está activo (`employment_start_date`/`employment_end_date`) y sugiere la posición.
- El generador procura la consistencia con el histórico de asignaciones; si no hay historia, elige candidatos random iniciales y luego mantiene continuidad.
- Un colaborador solo puede ocupar una posición por día; descanso y turno son eventos mutuamente excluyentes.
- Los descansos manuales tienen prioridad absoluta sobre los turnos.

### 2025-11-02 · Optimización iterativa
- Recorre iterativamente posiciones sin candidato, intentando reasignaciones que mejoren la cobertura sin violar reglas previas.

### 2025-11-03 · Balance de descansos
- Controla rachas de trabajo y descansos mensuales por colaborador usando `rest_max_consecutive_days` y `rest_monthly_days`.
- Descansos manuales consumen cupo y reinician rachas; descansos post-turno son obligatorios.

### 2025-11-04 · Rotación día/noche
- Tras una racha nocturna completa, rota al colaborador a turno diurno equivalente (y viceversa) después de los descansos, salvo que no exista posición sugerida para el turno opuesto.

---

Este documento sirve como guía viva; actualízalo cuando cambien procesos, tooling o reglas de negocio.
