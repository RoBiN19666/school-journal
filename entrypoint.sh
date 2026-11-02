#!/bin/sh
set -e

echo "==> Применяю миграции..."
python manage.py migrate --noinput

# Демонстрационная база живёт отдельно: в рабочем журнале демо-данных не бывает.
if [ "${DEMO_MODE:-0}" = "1" ]; then
    echo "==> Готовлю демонстрационную базу (отдельно от рабочей)..."
    python manage.py demo_setup || echo "Демо-база не готова — демо-режим будет недоступен."
fi

echo "==> Собираю статику..."
python manage.py collectstatic --noinput >/dev/null

echo "==> Запускаю gunicorn на 0.0.0.0:8000"
# Потоки важны, чтобы медленная выгрузка (PNG/Excel) не блокировала остальные вкладки,
# а увеличенный timeout — чтобы gunicorn не отвечал "Internal Server Error" на тяжёлый запрос.
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
