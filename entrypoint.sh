#!/bin/sh
set -e

echo "==> Applying migrations..."
python manage.py migrate --noinput

# The demo database is kept apart: the working journal never holds demo data.
if [ "${DEMO_MODE:-0}" = "1" ]; then
    echo "==> Preparing the demo database (separate from the working one)..."
    python manage.py demo_setup || echo "The demo database is not ready - demo mode will be unavailable."
fi

echo "==> Collecting static files..."
python manage.py collectstatic --noinput >/dev/null

echo "==> Starting gunicorn on 0.0.0.0:8000"
# Threads matter so that a slow download (PNG/Excel) does not block the other tabs,
# and the longer timeout keeps gunicorn from answering "Internal Server Error"
# on a heavy request.
exec gunicorn school_journal.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 3 \
    --threads 4 \
    --timeout 180 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --max-requests 2000 \
    --max-requests-jitter 200 \
    --access-logfile - \
    --error-logfile -
