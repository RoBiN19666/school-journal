"""Дополнительные данные для всех шаблонов."""
from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

from . import __author__, __product__, __version__
from . import periods as periods_tools
from . import themes as theme_tools
from .models import AcademicYear, Branch


#: Адрес исходного кода. Хранится в одном месте: меняется аккаунт или имя
#: репозитория — правится только эта строка, а не каждый шаблон.
SOURCE_REPOSITORY_URL = "https://github.com/robin19666/school-journal"


def product(request):
    """Название продукта, версия и автор — для подвала и страницы «О продукте»."""
    return {
        "app_product": __product__,
        "app_version": __version__,
        "app_author": __author__,
        "app_source_url": SOURCE_REPOSITORY_URL,
        "app_fund": getattr(settings, "FUND_NAME", "Образовательная организация"),
        # Tailwind лежит в контейнере: интернет для оформления не нужен.
        # Сроки входа — показываем на странице входа и в справке.
        "remember_days": getattr(settings, "REMEMBER_ME_DAYS", 30),
        "session_hours": round(getattr(settings, "SESSION_COOKIE_AGE", 12 * 3600) / 3600),
    }


def theme(request):
    """Тема оформления из cookie и список доступных тем."""
    return {
        "theme": theme_tools.from_request(request),
        # Для карточек выбора темы сразу считаем образец цветов.
        "themes": [
            dict(item, preview=theme_tools.preview(item)) for item in theme_tools.THEMES
        ],
        "theme_light": theme_tools.THEMES[0]["vars"],
        "theme_cookie": theme_tools.COOKIE_NAME,
    }


def demo_mode(request):
    """Показывает шаблонам, включён ли демо-режим и под какой ролью вошли."""
    session = getattr(request, "session", None)
    in_demo = bool(session and session.get("demo"))
    return {
        "demo_mode": getattr(settings, "DEMO_MODE", False),
        "demo_active": in_demo,
        "demo_role": session.get("demo_role") if session else None,
        "demo_database": (
            settings.DATABASES.get("demo", {}).get("NAME") if in_demo else None
        ),
    }


def periods(request):
    """Учебные годы, полугодия, филиалы и активные значения для шапки."""
    try:
        # Новый учебный год появляется сам: в сентябре 2028 добавится 2028–2029.
        periods_tools.ensure_academic_years()
        year, term = periods_tools.resolve_period(request)
        branch = periods_tools.resolve_branch(request)
        years = list(AcademicYear.objects.order_by("-start_date"))
        terms = list(year.terms.all()) if year is not None else []
        branches = list(Branch.objects.order_by("order", "name"))
    except (OperationalError, ProgrammingError):
        # Таблиц ещё нет (например, до миграций) — страницы просто без периодов.
        return {
            "academic_years": [],
            "active_year": None,
            "active_term": None,
            "active_year_terms": [],
            "active_branch": None,
            "branches": [],
        }

    today = timezone.localdate()
    return {
        "academic_years": years,
        "active_year": year,
        "active_term": term,
        "active_year_terms": terms,
        "today": today,
        "active_term_is_current": bool(term is not None and term.contains(today)),
        # Период, в который попадает сегодняшняя дата: для кнопки возврата.
        "current_term": periods_tools.current_term(),
        "branches": branches,
        "active_branch": branch,
    }


def branding(request):
    """Название организации, логотип и фавикон из настроек журнала.

    Идёт после processора product и подменяет его значения, если настройки заданы.
    """
    data = {}
    try:
        from .models import SiteSettings

        site = SiteSettings.load()
    except Exception:  # таблицы ещё нет — страницы работают со значениями по умолчанию
        return data
    data["app_organization"] = site.organization_name
    data["app_product"] = site.product_name or "Электронный журнал"
    data["app_fund"] = site.organization_name
    data["app_short_logo"] = site.short_logo or "ЭЖ"
    data["app_logo_url"] = site.logo.url if site.logo else ""
    data["app_favicon_url"] = site.favicon.url if site.favicon else ""
    data["app_login_hint"] = site.login_hint
    data["app_remember_days"] = site.remember_days
    data["app_session_hours"] = site.session_hours
    return data
