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

