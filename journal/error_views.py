"""Страницы ошибок: понятные посетителю, без служебных подробностей.

Подробности (код ответа, адрес, трассировка) пишутся только в журнал сервера —
на странице их нет ни для кого. Тема оформления берётся из cookie.

Отрисовка идёт по трём ступеням: обычный рендер с контекст-процессорами, рендер
без запроса (если процессор упал) и простой текст (если недоступен сам шаблон).
"""
import logging

from django.http import HttpResponse
from django.shortcuts import render
from django.template import loader

from . import themes

logger = logging.getLogger(__name__)

PLAIN_TEXT = (
    "Ошибка {status}: что-то пошло не так. Обновите страницу или вернитесь назад."
)


def error_context(request, message=None):
    """Минимум данных для страницы ошибки: только тема оформления."""
    context = {
        "theme": themes.from_request(request),
        "themes": [dict(item, preview=themes.preview(item)) for item in themes.THEMES],
        "theme_light": themes.THEMES[0]["vars"],
        "theme_cookie": themes.COOKIE_NAME,
    }
    if message:
        context["message"] = message
    return context


def render_error(request, template_name, context, status):
    """Отрисовывает страницу ошибки так, чтобы она не могла упасть сама."""
    response = _render_error(request, template_name, context, status)
    # Страницы ошибок не индексируем и не кэшируем: они не должны попадать в поиск.
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    return response


def _render_error(request, template_name, context, status):
    try:
        return render(request, template_name, context, status=status)
    except Exception:  # noqa: BLE001 — например, контекст-процессор обратился к базе
        logger.exception("Страница ошибки %s: не удалось собрать контекст", status)
    try:
        # Без request: контекст-процессоры не вызываются, тема уже в контексте.
        return HttpResponse(loader.get_template(template_name).render(context), status=status)
    except Exception:  # noqa: BLE001
        logger.exception("Страница ошибки %s: не удалось отрисовать шаблон", status)
    return HttpResponse(
        PLAIN_TEXT.format(status=status), status=status, content_type="text/plain; charset=utf-8"
    )


def page_not_found(request, exception=None, template_name="404.html"):
    return render_error(request, template_name, error_context(request), 404)


def bad_request(request, exception=None, template_name="400.html"):
    return render_error(request, template_name, error_context(request), 400)


def permission_denied(request, exception=None, template_name="403.html"):
    context = error_context(request)
    if template_name == "journal/blocked.html":
        # Отдельная страница: каркас журнала (меню, шапка, данные) не подключается.
        context["standalone"] = True
    return render_error(request, template_name, context, 403)


def csrf_failure(request, reason="", template_name="403.html"):
    """Проверка CSRF не прошла: объясняем по-человечески, а не сухим «Forbidden»."""
    context = error_context(
        request,
        message=(
            "Форма устарела или открыта в другой вкладке — защита от подделки запросов "
            "не пропустила отправку. Обновите страницу (F5) и отправьте форму заново."
        ),
    )
    return render_error(request, template_name, context, 403)


def server_error(request, template_name="500.html"):
    """Пятисотая: та же аккуратная страница, но с кодом 500."""
    return render_error(request, template_name, error_context(request), 500)
