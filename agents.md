# Proyecto `applacolina` – Apuntes para agentes

## Contexto general
- Aplicación Django ubicada en `applacolina/` con múltiples apps (`personal`, `granjas`, etc.).
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
  - Nota: el agente nunca debe correr tests contra la base de datos de la aplicación. Solo puede correr tests contra una base de datos de prueba.

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
Regla: El generador construye la asignación sugerida para un calendario, asumiendo que es un nuevo calendario o se está sobreescribiendo uno existente y la data anterior de asignaciones ha sido eliminada.
Regla: El generador es llamado en el contexto de una interfaz gráfica que permite diligenciar un nombre del calendario, fecha inicio y fecha fin; o en el contexto de la regeneración de un calendario existente que ya tiene u nombre, fecha inicio y fecha fin pero que sus asignaciones han sido eliminadas.
Regla: El generador recibe una fecha de inicio y fin; sobre las cuales debe realizar la asignación de turnos a las posiciones. 
Regla: El generador no permite constuir fechas en rangos de calendario solapados con otros calendarios. Cada rango de fechas del calendario deben ser únicos y excluyentes, pero complementarios.
Regla: El calendario está compuesto por asignaciones de turnos y de descansos. 
Regla: El generador asigna turnos al calendario en el orden determinado por las posiciones activas durante el periodo de vigencia de la posición (desde - hasta; incluyentes). Esto significa que el orden de la posición determina la importancia de la asignación, y el algoritmo intenta llenar todas asignaciones de esa posición para el periodo del calendario acorde a todas las reglas del generador. 
Regla: Se sabe si la posición está activa si: fecha de asignación >= fecha vigente desde de la posición && fecha de asignación <= fecha vigente hasta de la posición.
Regla: Si la posición no es activa se excluye por completo del calendario a generar.
Regla: Si la posición está fuera de vigencia total (en relación al rango del calendario), se excluye por completo del calendario a generar. 
Regla: Si la posición está en vigencia parcial respecto a las fechas del calendario a generar, se incluye en el calendario a generar, pero solo se realizan las asignaciones para el periodo de vigencia que coexiste con los slots de las posiciones activas del calendario, fechas incluyentes.
Regla: El generador asigna los colaboradores activos, considerando sus posiciones sugeridas, la configuración de descansos manuales y automáticos, así como la configuración del positioncategory asociado a cada posición sugerida. 
Regla: El generador determina si un colaborador está activo, si fecha asignación >= fecha ingreso del colaborador && fecha asignación <= fecha de retiro del colaborador (o la fecha de retiro está vacía).
Regla: El generador solo considera un colaborador para una posición si la posición a asignar está dentro del listado de posiciones sugeridas.
Regla: El generador considera a los colaboradores para una posición en el orden determinado por el historico de asignaciones para la posición fuera y dentro del calendario a generar. Es decir, el generador busca consistencia en la asignación de los turnos futuros, un colaborador debe seguir consistentemente en la última posición asignada.  Si se cambia la asignación manualmente, el generador debe considerar la última posición asignada para el colaborador como punto de partida de consistencia. Si no hay asignaciones historicas, el generador toma colaboradores aleatorios que cumplan con los demás criterios, para la primera asignación, y luego busca consistencia en las siguientes asignaciones de esa posición. 
Regla: Un colaborador solo puede estar asignado a una posición en el mismo día, ya sea de turno o de descanso. Son excluyentes. No puede estar de turno y de descanso a la vez el mismo día, tampoco puede estar asignado a dos posiciones el mismo día. 
Regla: Descansos: El colaborador puede tener descansos manuales asignados para un rango de fechas especifico. Estos descansos manuales tienen más peso que las asignaciones de turnos. Es decir, si un colaborador tiene descansos manuales programados, se le debe asignar el descanso y considerar otro colaborador para el turno.
### 2025-11-02 · Optimización iterativa
Regla: Tras la primera pasada de asignaciones, el generador revisa iterativamente las posiciones que quedaron sin colaborador elegible y reevalúa reasignaciones posibles respetando las reglas anteriores, liberando operadores de posiciones previas solo cuando exista un candidato alternativo válido. El proceso se repite tantas veces como sea necesario hasta que no mejore el número de asignaciones válidas.
### 2025-11-03 · Balance de descansos
Regla: El generador controla por colaborador la racha de días trabajados y el total de descansos del mes. Usa `rest_max_consecutive_days` de la categoría para forzar un descanso tras esa cantidad de turnos seguidos cuando aún queda cupo de descansos mensuales (`rest_monthly_days`); si la meta ya se alcanzó, omite descansos automáticos y permite que siga trabajando hasta fin de mes. Los descansos manuales existentes consumen el cupo y reinician la racha, mientras que los descansos post-turno siguen siendo obligatorios.
### 2025-11-04 · Rotación día/noche
Regla: Tras cada racha completada en posiciones de turno nocturno, el generador debe ubicar al colaborador en posiciones equivalentes de turno diurno (y viceversa) después de los descansos; solo si el colaborador no tiene posiciones sugeridas con el turno opuesto se mantiene la continuidad del mismo turno.
