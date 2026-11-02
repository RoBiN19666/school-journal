"""Подготовка демонстрационной базы: создание, миграции, демо-данные.

Миграции к демо-базе применяются отдельным процессом, в котором она сама
объявляется основной базой. Так исторические миграции с данными (они работают
с «основной» базой) отрабатывают на ней ровно так же, как на рабочей, и схема
получается одинаковой.
"""
import os
import subprocess
import sys

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections

from journal import demo, demo_router


def ensure_database_exists():
    """Создаёт демо-базу, если её ещё нет (для PostgreSQL — через служебную базу)."""
    from django.conf import settings as django_settings

    settings_dict = connections[demo_router.DEMO_ALIAS].settings_dict
    engine = settings_dict.get("ENGINE", "")
    name = settings_dict.get("NAME", "")
    if "sqlite" in engine:
        return "sqlite", True
    if "postgresql" not in engine:
        return engine, False

    # Подключаемся к служебной базе postgres обычным соединением Django: оно
    # работает в режиме autocommit, а CREATE DATABASE внутри транзакции запрещён.
    admin_alias = "postgres_admin"
    config = dict(settings_dict)
    config["NAME"] = "postgres"
    config["CONN_MAX_AGE"] = 0
    config.pop("CONN_HEALTH_CHECKS", None)
    connections.databases[admin_alias] = config
    try:
        connection = connections[admin_alias]
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name])
            if cursor.fetchone():
                return "postgres", True
            cursor.execute(f'CREATE DATABASE "{name}"')
        return "postgres", True
    except Exception as error:  # noqa: BLE001
        first_error = error
    finally:
        try:
            connections[admin_alias].close()
        except Exception:  # noqa: BLE001
            pass
        connections.databases.pop(admin_alias, None)

    # Запасной путь: psycopg2 с явно включённым autocommit.
    try:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        raw = psycopg2.connect(
            dbname="postgres",
            user=settings_dict.get("USER"),
            password=settings_dict.get("PASSWORD"),
            host=settings_dict.get("HOST"),
            port=settings_dict.get("PORT"),
        )
        raw.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with raw.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name])
            if not cursor.fetchone():
                cursor.execute(f'CREATE DATABASE "{name}"')
        raw.close()
        return "postgres", True
    except Exception as error:  # noqa: BLE001
        return f"postgres ({first_error}; {error})", False


def migrate_demo_database():
    """Применяет миграции к демо-базе отдельным процессом."""
    settings_dict = connections[demo_router.DEMO_ALIAS].settings_dict
    engine = settings_dict.get("ENGINE", "")
    environment = os.environ.copy()
    environment["DEMO_MODE"] = "0"  # в дочернем процессе демо не поднимаем
    if "sqlite" in engine:
        environment["DB_ENGINE"] = "sqlite"
        environment["DB_SQLITE_NAME"] = str(settings_dict.get("NAME"))
    else:
        environment["POSTGRES_DB"] = str(settings_dict.get("NAME"))
    subprocess.check_call(
        [sys.executable, "manage.py", "migrate", "--noinput"],
        cwd=str(settings.BASE_DIR),
        env=environment,
    )


class Command(BaseCommand):
    help = "Creates the demo database and fills it with demo data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-create",
            action="store_true",
            help="Do not create the database (it already exists).",
        )
        parser.add_argument(
            "--skip-migrate",
            action="store_true",
            help="Do not apply migrations.",
        )

    def handle(self, *args, **options):
        alias = demo_router.DEMO_ALIAS
        name = connections[alias].settings_dict.get("NAME")
        self.stdout.write(f"Demo database: {alias} → {name}")


        if not options["skip_create"]:
            where, created = ensure_database_exists()
            if created:
                self.stdout.write(self.style.SUCCESS(f"Database is ready ({where})."))
            else:
                self.stdout.write(self.style.WARNING(f"Could not create the database: {where}"))
                self.stdout.write(
                    "Create it manually (CREATE DATABASE) or set DEMO_DB_NAME."
                )
                return

        if not options["skip_migrate"]:
            self.stdout.write("Applying migrations to the demo database...")
            migrate_demo_database()
            self.stdout.write(self.style.SUCCESS("Migrations applied."))

        with demo_router.activate():
            created = demo.ensure_demo_data()
        self.stdout.write(
            self.style.SUCCESS(
                "Demo data " + ("created." if created else "was already in place.")
            )
        )
        counts = demo.english_labels(demo.demo_database_counts() or {})
        for label, value in counts.items():
            self.stdout.write(f"  {label}: {value}")
        self.stdout.write("The working database is untouched. Enter the demo at: /demo/")
