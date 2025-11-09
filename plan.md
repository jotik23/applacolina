# Hoja de ruta de refactor para `applacolina`

El objetivo es modernizar la base de código con pasos incrementales que prioricen la seguridad al refactorizar. Las primeras fases se concentran en asegurar cobertura y estructura modular antes de abordar cambios de infraestructura o tooling avanzados.

## Fase 0 · Línea base y diagnóstico
- Ejecutar la suite actual (`docker compose exec web python manage.py test`) y documentar el estado de los casos fallidos o pendientes.
- Identificar rutas críticas (generador de calendarios, vistas JSON, autenticación de portal) y listar los archivos clave por app.
- Elaborar un mapa de dependencias entre apps (`personal`, `production`, `task_manager`) destacando puntos de acoplamiento.
- Registrar hallazgos en `docs/audits/diagnostico-inicial.md` para que el equipo conozca riesgos antes de tocar código.

## Fase 1 · Red de seguridad mínima para refactors
- Aumentar cobertura solo en los flujos críticos que se van a refactorizar primero (calendario, descansos, asignaciones); usar `manage.py test` o `pytest` si ya está instalado, sin migrar toda la suite.
- Crear factories o fixtures ligeros que permitan levantar calendarios y operadores sin depender de datos masivos.
- Documentar un checklist de smoke tests manuales (evento de generación, edición de asignaciones, actualización de descansos) para ejecutar tras cada refactor.
- Añadir medición de cobertura (`coverage run/manage.py test` + `coverage report`) para tener un número de referencia antes de los cambios estructurales.

## Fase 2 · Modularización temprana del dominio
- Reorganizar cada feature en módulos autocontenidos (`personal/calendar/`, `personal/operators/`, etc.) que agrupen vistas, formularios, servicios, templates y assets específicos.
- Crear capas explícitas de servicios, repositorios/selectors y DTOs para encapsular reglas de negocio y consultas complejas.
- Mantener las vistas como adaptadores finos que orquesten servicios; documentar contratos internos para evitar dependencias circulares.
- Actualizar importaciones y tests afectados por la nueva estructura, asegurando que el árbol de módulos siga siendo lineal y predecible.

## Fase 3 · Presentación y assets organizados
- Separar HTML, CSS y JS por funcionalidad: templates en `templates/<feature>/`, estilos en `static/<feature>/css/`, scripts en `static/<feature>/js/`.
- Convertir layouts existentes en plantillas base con bloques claramente definidos y fragmentos reutilizables (headers, tablas, formularios).
- Encapsular la lógica de cliente en módulos ES6 o archivos por componente; evitar scripts inline y variables globales.
- Documentar en `docs/frontend.md` la convención de nombres, estructura de carpetas y buenas prácticas para mantener el front libre de “spaghetti code”.

## Fase 4 · Contratos de APIs y separación de responsabilidades
- Revisar endpoints JSON existentes, asegurar que delegan en servicios y devuelven respuestas consistentes; agregar tests de smoke para cada ruta crítica.
- Documentar formatos de entrada/salida en markdown o schemas ligeros para sostener el contrato con el front actual.
- Añadir selectores o consultas dedicadas para alimentar la UI sin exponer lógica de negocio en las vistas.

## Fase 5 · Modernización de infraestructura y configuración (diferida)
- Dividir `settings.py` en módulos por entorno (`base.py`, `local.py`, `production.py`) y adoptar validación de variables (`django-environ` o `pydantic-settings`).
- Migrar la gestión de dependencias a `pip-tools` o `uv`, con archivos separados para `base`, `dev`, `test`.
- Revisar Dockerfile/docker-compose para alinearlos con la nueva estructura de settings y dependencias.
- Introducir herramientas de linting/typing globales (`ruff`, `mypy`, `pre-commit`) una vez que la modularización del código esté estable.

## Fase 6 · Observabilidad y operaciones (diferida)
- Estandarizar logging estructurado (JSON) y enriquecer `AssignmentChangeLog` con metadata relevante.
- Integrar Sentry u otra herramienta de monitoreo, junto con endpoints de health/readiness y métricas de negocio.
- Automatizar despliegues (Railway u otro PaaS) con pasos de migración, backup previo y smoke tests post-despliegue.

## Fase 7 · Experiencia de desarrollador (diferida)
- Construir `Makefile` o scripts `just` que empaqueten los comandos habituales (`make test`, `make lint`, `make up`).
- Configurar un entorno reproducible (`devcontainer`, documentación de setup local) y consolidar la wiki del proyecto.
- Revisar y mantener `plan.md` como documento vivo, actualizando el progreso y los aprendizajes de cada fase.

## Criterios de salida
- La cobertura de los módulos críticos se mantiene o mejora tras cada refactor.
- Las nuevas estructuras de módulos y templates cuentan con documentación y ejemplos.
- Las fases diferidas (infraestructura, observabilidad, DX) quedan planificadas sin bloquear el refactor inmediato.
- El equipo dispone de un checklist de pruebas regresivas para validar los cambios antes de desplegar.
