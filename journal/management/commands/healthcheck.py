"""Самопроверка журнала: python manage.py healthcheck.

Команда ничего не меняет в базе и не требует запущенного сервера: её можно
выполнять сразу после обновления, чтобы увидеть, всё ли на месте. Если есть
ошибки — код возврата ненулевой, поэтому команду можно ставить в автоматику.
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
    ("Пользователи", "User"),
    ("Ученики", "Student"),
    ("Группы", "Group"),
    ("Программы", "Subject"),
    ("Занятия", "Schedule"),
    ("Посещаемость", "Attendance"),
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
    tail = f" и ещё {len(items) - limit}" if len(items) > limit else ""
    return ", ".join(str(item) for item in items[:limit]) + tail


class Check:
    """Набор результатов: строка «[OK]» или «[ОШИБКА]» на каждую проверку."""

    def __init__(self):
        self.results = []

    def run(self, title, func):
        """Выполняет одну проверку; неожиданная ошибка тоже становится «[ОШИБКА]»."""
        try:
            status, message, note = func()
        except Exception as error:  # noqa: BLE001 - проверка не должна ронять команду
            status, message, note = "error", f"неожиданная ошибка: {error}", None
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
                out(f"[ИНФО] {title}")
                continue
            mark = "[OK]" if status == "ok" else "[ОШИБКА]"
            line = f"{mark} {title}"
            if message:
                line += f": {message}"
            out(line)
            if note:
                out(f"       {note}")
        out("")
        checked = len([item for item in self.results if item[0] != "info"])
        out(f"Проверок: {checked}, ошибок: {len(self.errors)}")


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
        return "error", f"файл базы отсутствовал и создан пустым ({name})", None
    return "ok", f"соединение есть ({engine}, {name or 'без имени'})", None


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
        return "error", f"не применено миграций: {len(missing)}", short_list(missing)
    return "ok", f"применены все ({len(on_disk)})", None


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
        return "error", "модели недоступны", short_list(problems, limit=3)
    return "ok", f"доступны {len(CORE_MODELS)} моделей, строк всего: {total}", None


def check_static():
    """Находит ли Django файлы оформления и задан ли STATIC_ROOT."""
    if not getattr(settings, "STATIC_ROOT", None):
        return "error", "не задан STATIC_ROOT (collectstatic некуда складывать)", None
    missing = [name for name in CORE_STATIC if not finders.find(name)]
    if missing:
        return "error", f"не найдены файлы: {len(missing)}", short_list(missing)
    return "ok", f"найдены все файлы оформления ({len(CORE_STATIC)})", None


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
        return "error", f"не собираются шаблоны: {len(broken)} из {len(templates)}", short_list(broken, limit=3)
    return "ok", f"собираются все шаблоны ({len(templates)})", None


def check_themes():
    """Число тем, одинаковый набор токенов у всех и тема по умолчанию."""
    items = themes.THEMES
    problems = []
    if len(items) != EXPECTED_THEMES:
        problems.append(f"тем {len(items)}, а ожидается {EXPECTED_THEMES}")
    expected = set(themes.TOKENS)
    for item in items:
        have = set(item.get("vars", {}))
        if have != expected:
            problems.append(
                f"тема «{item.get('value')}»: не хватает {short_list(sorted(expected - have), 3)}"
            )
    if not any(item.get("value") == themes.DEFAULT for item in items):
        problems.append(f"нет темы по умолчанию «{themes.DEFAULT}»")
    if problems:
        return "error", "оформление тем сломано", short_list(problems, limit=3)
    return "ok", f"тем {len(items)}, у всех одинаковые токены ({len(expected)})", None


def check_site_settings():
    """Настройки оформления: запись-одиночка читается (или берутся значения по умолчанию)."""
    from journal.models import SiteSettings

    count = SiteSettings.objects.count()
    instance = SiteSettings.load()
    if not count:
        return "ok", f"записи нет, работают настройки по умолчанию («{instance}»)", None
    return "ok", f"запись прочитана: «{instance.organization_name}»", None


def check_demo():
    """Демонстрационная база: её отсутствие — не ошибка."""
    if not getattr(settings, "DEMO_MODE", False):
        return "ok", "демо-режим выключен", "демо-база не подготовлена"
    if not demo.demo_database_ready():
        return "ok", "демо-режим включён, базы нет", "демо-база не подготовлена"
    counts = demo.demo_database_counts() or {}
    filled = ", ".join(f"{label.lower()}: {value}" for label, value in counts.items())
    return "ok", "демо-база готова", filled or None


def check_media():
    """Можно ли записать файл в MEDIA_ROOT (создаём и сразу удаляем)."""
    root = getattr(settings, "MEDIA_ROOT", None)
    if not root:
        return "error", "не задан MEDIA_ROOT", None
    folder = Path(root)
    folder.mkdir(parents=True, exist_ok=True)
    probe = folder / ".healthcheck.tmp"
    probe.write_text("проверка записи", encoding="utf-8")
    probe.unlink()
    return "ok", f"папка доступна на запись ({folder})", None


def check_teacher_names():
    """У преподавателей корректно считается полное имя."""
    from journal.models import User

    problems = [user.username for user in User.objects.filter(role=User.Role.TEACHER) if not user.full_name.strip()]
    if problems:
        return "error", f"без полного имени: {len(problems)}", short_list(problems, limit=3)
    return "ok", "полное имя считается у всех преподавателей", None


def check_lesson_dates():
    """У разовых занятий день недели совпадает со своей датой."""
    from journal.models import Schedule

    problems = [
        f"занятие {schedule.pk}: {schedule.lesson_date:%d.%m.%Y} — {schedule.get_day_of_week_display()}"
        for schedule in Schedule.objects.filter(lesson_date__isnull=False)
        if schedule.lesson_date.weekday() != schedule.day_of_week
    ]
    if problems:
        return "error", f"день недели не совпадает с датой: {len(problems)}", short_list(problems, limit=3)
    total = Schedule.objects.filter(lesson_date__isnull=False).count()
    return "ok", f"разовых занятий с датой: {total}, все совпадают с днём недели", None


class Command(BaseCommand):
    help = "Проверяет работоспособность журнала и печатает отчёт. Ничего не меняет."

    def handle(self, *args, **options):
        checks = Check()
        checks.run("База данных", check_database)
        checks.run("Миграции приложения journal", check_migrations)
        checks.run("Основные модели", check_models)
        checks.run("Статика", check_static)
        checks.run("Шаблоны", check_templates)
        checks.run("Темы оформления", check_themes)
        checks.run("Настройки оформления", check_site_settings)
        checks.run("Демонстрационная база", check_demo)
        checks.run("Права на запись в media", check_media)
        checks.run("Полные имена преподавателей", check_teacher_names)
        checks.run("Разовые занятия", check_lesson_dates)

        checks.info(
            f"Версия журнала: {__version__}; часовой пояс: {settings.TIME_ZONE}; "
            f"USE_TZ: {'да' if settings.USE_TZ else 'нет'}; Django: {self._django_version()}"
        )

        checks.render(self.stdout.write)

        if checks.errors:
            # Ненулевой код возврата: команду можно использовать в автоматике.
            raise CommandError(f"Проверка не пройдена: ошибок — {len(checks.errors)}.")

    @staticmethod
    def _django_version():
        import django

        return django.get_version()
