Para trabajar desde tu máquina con la base de Railway (conexión y restauraciones), estos son los comandos que
  funcionan:

  Conexión interactiva (desde tu host, si psql soporta TLS)

  PGPASSWORD=bJlNPILmHOnPFPrbWibGesgBTFDcaXAN \
  psql "postgresql://postgres@yamabiko.proxy.rlwy.net:45075/railway?sslmode=require"

  Restaurar un backup (desde tu host)

  PGPASSWORD=bJlNPILmHOnPFPrbWibGesgBTFDcaXAN \
  psql "postgresql://postgres@yamabiko.proxy.rlwy.net:45075/railway?sslmode=require" \
    -v ON_ERROR_STOP=on \
    -f backup.sql

  Si tu psql local falla (pasa en algunas instalaciones de macOS), hazlo desde el contenedor Postgres del proyecto:

  Conexión interactiva vía Docker

  docker compose exec -T db \
    sh -c 'PGPASSWORD=bJlNPILmHOnPFPrbWibGesgBTFDcaXAN \
    psql "postgresql://postgres@yamabiko.proxy.rlwy.net:45075/railway?sslmode=require"'

  Restaurar backup.sql vía Docker (recomendado)

  cat backup.sql | docker compose exec -T db \
    sh -c 'PGPASSWORD=bJlNPILmHOnPFPrbWibGesgBTFDcaXAN \
    psql "postgresql://postgres@yamabiko.proxy.rlwy.net:45075/railway?sslmode=require" \
    -v ON_ERROR_STOP=on'

  Antes de cargar un backup, si quieres un lienzo limpio, lanza:

  docker compose exec -T db \
    sh -c 'PGPASSWORD=bJlNPILmHOnPFPrbWibGesgBTFDcaXAN \
    psql "postgresql://postgres@yamabiko.proxy.rlwy.net:45075/railway?sslmode=require" \
    -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO postgres; GRANT ALL ON SCHEMA
  public TO public;"'

  Luego verifica con algo como:

  docker compose exec -T db \
    sh -c 'PGPASSWORD=bJlNPILmHOnPFPrbWibGesgBTFDcaXAN \
    psql "postgresql://postgres@yamabiko.proxy.rlwy.net:45075/railway?sslmode=require" \
    -c "\dt"'

  Esos comandos cubren conexión interactiva y carga de backups usando tu entorno o el contenedor.