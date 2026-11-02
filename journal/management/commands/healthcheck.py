"""Самопроверка журнала: python manage.py healthcheck.

Команда ничего не меняет в базе и не требует запущенного сервера: её можно
выполнять сразу после обновления, чтобы увидеть, всё ли на месте. Если есть
ошибки — код возврата ненулевой, поэтому команду можно ставить в автоматику.

Вывод команды — на английском: её запускает установщик и советует README,
а журнал публикуется для международной аудитории.
"""
import os
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.template.loader import get_template

from journal import __version__, demo, themes

#: Модели, которые обязаны быть доступны через ORM (проверяем count()).
CORE_MODELS = (
    ("Users", "User"),
    ("Students", "Student"),
    ("Groups", "Group"),
    ("Subjects", "Subject"),
    ("Lessons", "Schedule"),
    ("Attendance", "Attendance"),
)

#: Файлы оформления, которые Django обязан находить.
CORE_STATIC = (
    "journal/app.css",
    "journal/widgets.js",
    "journal/collapse.js",
    "journal/logo-crop.js",
    "journal/vendor/tailwind.js",
)

#: Сколько тем ожидается сейчас (см. journal/themes.py).
EXPECTED_THEMES = 6


def short_list(items, limit=5):
    """Первые несколько значений строкой: длинные списки читать неудобно."""
    items = list(items)
    tail = f" and {len(items) - limit} more" if len(items) > limit else ""
    return ", ".join(str(item) for item in items[:limit]) + tail


class Check:
    """Набор результатов: строка «[OK]» или «[ERROR]» на каждую проверку."""

    def __init__(self):
        self.results = []

    def run(self, title, func):
        """Выполняет одну проверку; неожиданная ошибка тоже становится «[ERROR]»."""
        try:
            status, message, note = func()
        except Exception as error:  # noqa: BLE001 - проверка не должна ронять команду
            status, message, note = "error", f"unexpected error: {error}", None
        self.results.append((status, title, message, note))
        return status

    def info(self, message):
        """Справочная строка: в счёт проверок не входит."""
        self.results.append(("info", message, "", None))

    @property
    def errors(self):
        return [item for item in self.results if item[0] == "error"]

    def render(self, out):
        for status, title, message, note in self.results:
            if status == "info":
                out(f"[INFO] {title}")
                continue
            mark = "[OK]" if status == "ok" else "[ERROR]"
            line = f"{mark} {title}"
            if message:
                line += f": {message}"
            out(line)
            if note:
                out(f"       {note}")
        out("")
        checked = len([item for item in self.results if item[0] != "info"])
        out(f"Checks: {checked}, errors: {len(self.errors)}")


def check_database():
    """Соединение с базой и простой запрос."""
    engine = connection.settings_dict.get("ENGINE", "").rsplit(".", 1)[-1]
    name = os.path.basename(str(connection.settings_dict.get("NAME", "")))
    existed = os.path.exists(str(connection.settings_dict.get("NAME", "")))
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    if not existed:
        # SQLite молча создаёт пустой файл, поэтому сообщаем об этом прямо:
        # дальше проверка миграций покажет, что база не готова.
        return "error", f"the database file was missing and was created empty ({name})", None
    return "ok", f"connection works ({engine}, {name or 'unnamed'})", None


def check_migrations():
    """Все ли миграции приложения journal применены."""
    executor = MigrationExecutor(connection)
    applied = set(executor.loader.applied_migrations)
    on_disk = {
        key
        for key in executor.loader.disk_migrations
        if key[0] == "journal" and key not in executor.loader.replacements
    }
    missing = sorted(name for app, name in on_disk - applied if app == "journal")
    if missing:
        return "error", f"migrations not applied: {len(missing)}", short_list(missing)
    return "ok", f"all applied ({len(on_disk)})", None


def check_models():
    """Доступны ли основные таблицы и читаются ли из них данные."""
    from journal import models

    problems = []
    total = 0
    for label, name in CORE_MODELS:
        try:
            total += getattr(models, name).objects.count()
        except Exception as error:  # noqa: BLE001
            problems.append(f"{label}: {error}")
    if problems:
        return "error", "models are unavailable", short_list(problems, limit=3)
    return "ok", f"{len(CORE_MODELS)} models available, rows in total: {total}", None


def check_static():
    """Находит ли Django файлы оформления и задан ли STATIC_ROOT."""
    if not getattr(settings, "STATIC_ROOT", None):
        return "error", "STATIC_ROOT is not set (collectstatic has nowhere to write)", None
    missing = [name for name in CORE_STATIC if not finders.find(name)]
    if missing:
        return "error", f"files not found: {len(missing)}", short_list(missing)
    return "ok", f"all static files found ({len(CORE_STATIC)})", None


def check_templates():
    """Собираются ли все шаблоны проекта."""
    folder = Path(settings.BASE_DIR) / "journal" / "templates"
    broken = []
    templates = []
    for path in sorted(folder.rglob("*.html")):
        name = path.relative_to(folder).as_posix()
        templates.append(name)
        try:
            get_template(name)
        except Exception as error:  # noqa: BLE001
            broken.append(f"{name}: {error}")
    if broken:
        return "error", f"templates that fail to load: {len(broken)} of {len(templates)}", short_list(broken, limit=3)
    return "ok", f"all templates load ({len(templates)})", None


def check_themes():
    """Число тем, одинаковый набор токенов у всех и тема по умолчанию."""
    items = themes.THEMES
    problems = []
    if len(items) != EXPECTED_THEMES:
        problems.append(f"{len(items)} themes, {EXPECTED_THEMES} expected")
    expected = set(themes.TOKENS)
    for item in items:
        have = set(item.get("vars", {}))
        if have != expected:
            problems.append(
                f"theme “{item.get('value')}”: missing {short_list(sorted(expected - have), 3)}"
            )
    if not any(item.get("value") == themes.DEFAULT for item in items):
        problems.append(f"no default theme “{themes.DEFAULT}”")
    if problems:
        return "error", "theme definitions are broken", short_list(problems, limit=3)
    return "ok", f"{len(items)} themes, identical tokens in every one ({len(expected)})", None


def check_site_settings():
    """Настройки оформления: запись-одиночка читается (или берутся значения по умолчанию)."""
    from journal.models import SiteSettings

    count = SiteSettings.objects.count()
    instance = SiteSettings.load()
    if not count:
        return "ok", f"no record yet, default settings are in use (“{instance}”)", None
    return "ok", f"record read: “{instance.organization_name}”", None


def check_demo():
    """Демонстрационная база: её отсутствие — не ошибка."""
    if not getattr(settings, "DEMO_MODE", False):
        return "ok", "demo mode is off", "the demo database is not prepared"
    if not demo.demo_database_ready():
        return "ok", "demo mode is on, no database yet", "the demo database is not prepared"
    counts = demo.demo_database_counts() or {}
    counts = demo.english_labels(counts)
    filled = ", ".join(f"{label.lower()}: {value}" for label, value in counts.items())
    return "ok", "the demo database is ready", filled or None


def check_media():
    """Можно ли записать файл в MEDIA_ROOT (создаём и сразу удаляем)."""
    root = getattr(settings, "MEDIA_ROOT", None)
    if not root:
        return "error", "MEDIA_ROOT is not set", None
    folder = Path(root)
    folder.mkdir(parents=True, exist_ok=True)
    probe = folder / ".healthcheck.tmp"
    probe.write_text("write test", encoding="utf-8")
    probe.unlink()
    return "ok", f"directory is writable ({folder})", None


def check_teacher_names():
    """У преподавателей корректно считается полное имя."""
    from journal.models import User

    problems = [user.username for user in User.objects.filter(role=User.Role.TEACHER) if not user.full_name.strip()]
    if problems:
        return "error", f"teachers without a full name: {len(problems)}", short_list(problems, limit=3)
    return "ok", "full name resolves for every teacher", None


def check_lesson_dates():
    """У разовых занятий день недели совпадает со своей датой."""
    from journal.models import Schedule

    problems = [
        f"lesson {schedule.pk}: {schedule.lesson_date:%d.%m.%Y} - {schedule.get_day_of_week_display()}"
        for schedule in Schedule.objects.filter(lesson_date__isnull=False)
        if schedule.lesson_date.weekday() != schedule.day_of_week
    ]
    if problems:
        return "error", f"weekday does not match the date: {len(problems)}", short_list(problems, limit=3)
    total = Schedule.objects.filter(lesson_date__isnull=False).count()
    return "ok", f"one-off lessons with a date: {total}, all match their weekday", None


class Command(BaseCommand):
    help = "Checks that the journal is healthy and prints a report. Changes nothing."

    def handle(self, *args, **options):
        checks = Check()
        checks.run("Database", check_database)
        checks.run("Migrations of the journal app", check_migrations)
        checks.run("Core models", check_models)
        checks.run("Static files", check_static)
        checks.run("Templates", check_templates)
        checks.run("Color themes", check_themes)
        checks.run("Branding settings", check_site_settings)
        checks.run("Demo database", check_demo)
        checks.run("Write access to media", check_media)
        checks.run("Teacher full names", check_teacher_names)
        checks.run("One-off lessons", check_lesson_dates)

        checks.info(
            f"Journal version: {__version__}; time zone: {settings.TIME_ZONE}; "
            f"USE_TZ: {'yes' if settings.USE_TZ else 'no'}; Django: {self._django_version()}"
        )

        checks.render(self.stdout.write)

        if checks.errors:
            # Ненулевой код возврата: команду можно использовать в автоматике.
            raise CommandError(f"Self-check failed: {len(checks.errors)} error(s).")

    @staticmethod
    def _django_version():
        import django

        return django.get_version()
