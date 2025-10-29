# Proyecto `applacolina` – Apuntes para agentes

## Contexto general
- Aplicación Django ubicada en `applacolina/` con múltiples apps (`calendario`, `granjas`, `users`, etc.).
- El repositorio incluye un `docker-compose.yml` que define dos servicios:
  - `web`: construye la imagen a partir del `Dockerfile`, monta el repositorio en `/app`, y al iniciar ejecuta `python manage.py migrate` seguido de `runserver`.
  - `db`: contenedor `postgres:15-alpine`, credenciales leídas desde `.env`, y expone `5432`.
- La base de datos usada por Django es PostgreSQL (configurada para resolverse con el hostname `db` dentro de la red de Docker).

## Ejecución de comandos de gestión (`manage.py`)
- Desde el host, `python` apunta a la versión 2.7 (`python --version`), lo cual provoca errores de sintaxis al ejecutar `manage.py`.
- Incluso con `python3` desde el host, la conexión a la base de datos falla porque el hostname `db` solo existe dentro de la red de Docker.
- **Forma correcta:** usar el contenedor `web` con Docker Compose.
  ```bash
  docker compose exec web python manage.py <comando>
  ```
  Ejemplos útiles:
  - Migraciones: `docker compose exec web python manage.py migrate`
  - Listar migraciones: `docker compose exec web python manage.py showmigrations`
  - Pruebas: `docker compose exec web python manage.py test`

## Notas operativas
- Los contenedores actuales se pueden verificar con `docker compose ps`.
- Los comandos de Docker pueden requerir permisos elevados cuando se ejecutan desde entornos restringidos (p. ej., Codex CLI).
- El proyecto usa un volumen que monta el directorio actual dentro del contenedor, por lo que los cambios locales se reflejan al instante en `web`.

## Configuración visual del calendario
- La grilla mantiene columnas por día y filas ordenadas con `PositionDefinition.display_order`; cada celda muestra la asignación (o vacío) y banderas de alerta.
- La sección lateral usa los mismos datos para filtros por granja/galpón/categoría sin modificar rutas actuales.
- El panel inferior de descansos lista filas con operadores y motivos; se debe seguir enviando estructura aunque no se calculen descansos automáticamente.
- Las vistas existentes (`calendar_detail`, `calendar_generate`, `rest_periods`) deben conservar sus endpoints y formatos JSON para reutilizar los componentes del front. 

## Registro – Generador de calendarios
### 2025-10-29 · Iteración de reglas
- El generador asume que trabaja sobre un calendario nuevo o que las asignaciones previas ya fueron eliminadas antes de regenerar.
- El calendario llega desde la interfaz con nombre, fecha inicio y fecha fin definidos; en regeneraciones esos valores ya existen aunque las asignaciones se hayan purgado.
- Se recibe un rango [fecha inicio, fecha fin] y se deben poblar turnos para las posiciones activas en ese intervalo.
- Los rangos de calendario no pueden solaparse con otros calendarios existentes; cada calendario usa un intervalo exclusivo.
- El calendario debe reflejar tanto turnos como descansos.
- Las posiciones se iteran según `display_order`, siempre que estén vigentes en la fecha asignada (valid_from/valid_until incluyentes). Posiciones fuera de vigencia total se omiten; si están vigentes parcialmente solo se cubre la intersección del rango.
- Un colaborador se considera para una posición solo si está activo en la fecha (entre ingreso y retiro, si aplica) y la posición está listada en sus sugeridas.
- Se prioriza consistencia histórica: por posición se respeta el orden de las últimas asignaciones (incluyendo cambios manuales) y, en ausencia de historial, se recurre a candidatos que cumplan las demás reglas.
- Un colaborador no puede tener más de una asignación el mismo día ni combinar turno y descanso en la misma fecha.
- Los descansos manuales prevalecen sobre los turnos y deben respetarse junto con los descansos automáticos configurados por colaborador y la configuración de la categoría de la posición.
