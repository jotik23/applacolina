release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
web: gunicorn applacolina.wsgi:application --bind 0.0.0.0:$PORT
