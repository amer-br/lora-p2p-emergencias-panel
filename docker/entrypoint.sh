#!/bin/sh
# Prepara la base de datos antes de arrancar el proceso del contenedor.
set -e

python - <<'EOF'
import os, time, psycopg
for intento in range(30):
    try:
        psycopg.connect(
            host=os.environ["POSTGRES_HOST"], dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
        ).close()
        break
    except psycopg.OperationalError:
        print("Esperando a PostgreSQL...")
        time.sleep(1)
else:
    raise SystemExit("PostgreSQL no respondió")
EOF

if [ "$PREPARAR_BD" = "1" ]; then
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    python manage.py cargar_recursos
    python manage.py crear_responsable
    # En segundo plano: si hay internet, guarda las teselas del campus para
    # que el mapa funcione después sin conexión; si no hay, termina enseguida.
    python manage.py precargar_teselas &
fi

exec "$@"
