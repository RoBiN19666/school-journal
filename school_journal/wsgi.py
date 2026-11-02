"""WSGI-конфигурация проекта «Электронный журнал»."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "school_journal.settings")

application = get_wsgi_application()
