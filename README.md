# applacolina

## Ejecutar con Docker

1. Copia el archivo de variables de entorno y ajusta los valores necesarios:
   ```bash
   cp .env.example .env
   ```

2. Levanta los servicios de Django y PostgreSQL:
   ```bash
   docker compose up --build
   ```

3. La aplicación queda disponible en `http://localhost:8000/`. El contenedor de `web` ejecuta las migraciones automáticamente antes de iniciar el servidor.

## Variables de entorno relevantes

- `DJANGO_SECRET_KEY`: clave secreta para producción (obligatoria fuera de desarrollo).
- `DJANGO_DEBUG`: activa/desactiva modo debug (`True`/`False`).
- `DJANGO_ALLOWED_HOSTS`: lista separada por comas con los hosts permitidos.
- `DJANGO_CSRF_TRUSTED_ORIGINS`: orígenes válidos para CSRF (por ejemplo, `https://tu-app.up.railway.app`).
- `DATABASE_URL`: cadena de conexión completa; Railway la provee automáticamente.
- `POSTGRES_*`: valores utilizados para el entorno local (usuario, contraseña, base de datos, host, puerto).

## Despliegue en Railway

Railway expone las variables `DATABASE_URL`, `PORT` y `RAILWAY_STATIC_URL`. Para desplegar:

1. Define `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` y `DJANGO_CSRF_TRUSTED_ORIGINS` en Railway.
2. Usa el `Dockerfile` incluido para construir la imagen.
3. Configura el comando de ejecución a `gunicorn applacolina.wsgi:application --bind 0.0.0.0:${PORT}` (Railway inyecta el puerto).
