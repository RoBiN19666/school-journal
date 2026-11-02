"""Middleware безопасности: доступ по IP, журнал заходов и разделение демо-базы."""
import logging

from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect

from . import demo_router, notifications
from . import setup as setup_tools
from .models import VisitLog
from .security import (
    access_denied,
    block_address,
    get_client_ip,
    is_private,
    looks_like_probe,
    recent_probes,
    record_event,
)

logger = logging.getLogger(__name__)

SKIP_PREFIXES = ("/static/", "/favicon.ico", "/admin/jsi18n/")
SKIP_SUFFIXES = (".css", ".js", ".png", ".jpg", ".ico", ".svg", ".woff", ".woff2")


class DemoDatabaseMiddleware:
    """Пока в сессии стоит флаг демо-режима, все данные читаются и пишутся в демо-базу.

    Рабочая база остаётся нетронутой: демонстрация не может в неё ничего записать.
    Флаг хранится в сессии, поэтому разные посетители не мешают друг другу,
    а администратор в своей сессии видит только рабочий журнал.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        active = False
        try:
            session = getattr(request, "session", None)
            active = bool(session and session.get("demo") and settings_demo_enabled())
            if active and not demo_database_usable():
                # Демо-база пропала (например, её очистили) — работаем как обычно.
                session.pop("demo", None)
                session.pop("demo_role", None)
                active = False
        except Exception as error:  # noqa: BLE001 - демо не важнее страницы
            logger.warning("Не удалось определить демо-режим: %s", error)
            active = False

        if not active:
            return self.get_response(request)
        # Демонстрация ничего не сохраняет: всё, что посетитель нажимает, работает
        # как обычно (страницы, сообщения, пересчёты), но в конце запроса изменения
        # откатываются. Ни копий баз, ни следов в данных.
        session.set_expiry(0)
        with demo_router.activate():
            with transaction.atomic(using=demo_router.DEMO_ALIAS):
                response = self.get_response(request)
                transaction.set_rollback(True, using=demo_router.DEMO_ALIAS)
            return response


def demo_database_usable():
    """Быстрая проверка, что демо-база подключена (без запросов к таблицам)."""
    from django.db import connections

    try:
        connection = connections[demo_router.DEMO_ALIAS]
        connection.ensure_connection()
        return connection.is_usable()
    except Exception:  # noqa: BLE001
        return False


def settings_demo_enabled():
    from django.conf import settings

    return bool(getattr(settings, "DEMO_MODE", False))


def demo_session(request):
    """Идёт ли запрос в демонстрационном режиме.

    Флаг стоит в сессии посетителя, а на время запроса демо-база включена
    через ``demo_router``; проверяем оба признака, чтобы демонстрация ни при
    каких обстоятельствах не оставила следов в рабочей базе.
    """
    try:
        session = getattr(request, "session", None)
        if session is not None and session.get("demo"):
            return True
    except Exception as error:  # noqa: BLE001 - демо не важнее страницы
        logger.warning("Не удалось определить демо-сессию: %s", error)
    return demo_router.demo_active()


class FirstRunSetupMiddleware:
    """Первый запуск: пока в журнале нет администратора, все страницы ведут на /setup/.

    Так «чистая» база сразу предлагает создать первую учётную запись и задать
    название журнала, а работающая установка (есть активный администратор или
    главный администратор) ничего не замечает: редиректа нет. Демонстрация,
    статика, загруженные знаки, проверка работоспособности и админка доступны
    всегда — они не зависят от того, настроена ли рабочая база.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self.needs_setup(request):
            return redirect(setup_tools.SETUP_URL_NAME)
        return self.get_response(request)

    @staticmethod
    def needs_setup(request):
        """Нужно ли увести этот запрос на страницу первоначальной настройки."""
        if demo_session(request):
            # Демонстрация живёт в отдельной базе и настройки рабочего журнала
            # не требует: иначе вход в демо на чистой базе упирался бы в мастер.
            return False
        if setup_tools.is_setup_exempt(getattr(request, "path", "")):
            return False
        return setup_tools.setup_required()


class AccessControlMiddleware:
    """Блокирует адреса из чёрного списка и ведёт журнал посещений.

    Любая ошибка журнала или правил не должна превращаться в 500 на странице,
    поэтому доступ проверяется «мягко»: при сбое проверки запрос пропускается.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or ""
        skip = path.startswith(SKIP_PREFIXES) or path.endswith(SKIP_SUFFIXES)

        address = ""
        try:
            address = get_client_ip(request)
            denied = access_denied(address)
        except Exception as error:  # noqa: BLE001
            logger.warning("Проверка доступа по IP не удалась: %s", error)
            denied = False

        if denied:
            if not skip:
                self._log(
                    VisitLog.Kind.BLOCKED,
                    request,
                    path,
                    detail=_("доступ с адреса запрещён"),
                    address=address,
                )
                try:
                    notifications.notify(
                        "ip_blocked",
                        "Заблокированный доступ",
                        f"С адреса {address} попытались открыть {path}.",
                    )
                except Exception as error:  # noqa: BLE001
                    logger.warning("Уведомление о блокировке не отправлено: %s", error)
            # Отдаём ту же понятную страницу, что и при обычном отказе в доступе.
            from .error_views import permission_denied

            # Заблокированному показываем только саму страницу блокировки:
            # без меню, шапки и данных журнала.
            return permission_denied(
                request,
                PermissionDenied("Доступ с вашего IP-адреса запрещён администратором журнала."),
                template_name="journal/blocked.html",
            )

        # Сканеры уязвимостей ходят по адресам, которых в журнале быть не может.
        probe = False
        response = None
        if not skip and looks_like_probe(path):
            probe = True
            if self._handle_probe(request, path, address):
                from .error_views import permission_denied

                response = permission_denied(
                    request,
                    PermissionDenied("Слишком много подозрительных запросов с вашего адреса."),
                )

        if response is None:
            response = self.get_response(request)

        if not skip:
            user = getattr(request, "user", None)
            is_real_user = bool(getattr(user, "is_authenticated", False))
            # В демонстрации учётка живёт в демо-базе, и её номер совпадает с номером
            # совсем другого человека в рабочей базе. Поэтому связь с пользователем
            # не записываем — только логин и пометку, что это демонстрация.
            demo = demo_session(request)
            username = "" if demo else getattr(user, "username", "")
            if demo and is_real_user:
                username = f"{getattr(user, 'username', '')} (демонстрация)"
            # Подозрительный запрос записывается в журнал ровно один раз — здесь,
            # вместе с кодом ответа. Раньше его писал ещё и _handle_probe, из-за чего
            # одна попытка сканера давала две записи и автоблокировка срабатывала
            # вдвое раньше заявленного порога.
            self._log(
                VisitLog.Kind.PROBE if probe else VisitLog.Kind.VISIT,
                request,
                path,
                user=None if demo else (user if is_real_user else None),
                status_code=getattr(response, "status_code", None),
                detail=_("похоже на сканер уязвимостей") if probe else "",
                address=address,
                username=username,
            )
        return response

    @staticmethod
    def _handle_probe(request, path, address):
        """Подозрительный запрос: решаем, не пора ли заблокировать адрес.

        В журнал здесь ничего не пишем — запись делает вызывающий (``_log``),
        поэтому на запрос приходится ровно одна запись. Текущий запрос считаем
        в уме: он ещё не записан, а порог ``AUTO_BLOCK_THRESHOLD`` должен
        срабатывать именно на нём.

        Возвращает True, если адрес только что заблокирован.
        """
        if demo_session(request):
            # Демонстрация не должна оставлять следов в рабочей базе: правила по IP
            # в демо-сессии не создаём вовсе (журнал посещений остаётся как есть).
            logger.info("Демо-режим: автоблокировка адреса %s пропущена (%s)", address, path)
            return False

        if not getattr(settings, "AUTO_BLOCK_PROBES", True) or not address:
            return False
        if getattr(settings, "AUTO_BLOCK_PRIVATE", False) is False and is_private(address):
            return False
        threshold = getattr(settings, "AUTO_BLOCK_THRESHOLD", 5)
        try:
            seen = recent_probes(address)
        except Exception as error:  # noqa: BLE001 — подсчёт не важнее страницы
            logger.warning("Не удалось посчитать подозрительные запросы с %s: %s", address, error)
            return False
        if seen + 1 < threshold:
            return False
        try:
            _, created = block_address(
                address,
                comment=f"Автоблокировка: {threshold}+ подозрительных запросов, например {path}"[:200],
            )
        except Exception as error:  # noqa: BLE001
            logger.warning("Не удалось заблокировать адрес %s: %s", address, error)
            return False
        if created:
            logger.warning("Адрес %s заблокирован автоматически: сканирование %s", address, path)
            try:
                notifications.notify(
                    "ip_blocked",
                    "Адрес заблокирован автоматически",
                    f"С адреса {address} шло сканирование (например, {path}). "
                    f"Блокировка поставлена на {getattr(settings, 'AUTO_BLOCK_HOURS', 24)} ч.",
                )
            except Exception as error:  # noqa: BLE001
                logger.warning("Уведомление об автоблокировке не отправлено: %s", error)
        return created

    @staticmethod
    def _log(kind, request, path, user=None, status_code=None, detail="", address=None,
             username=""):
        try:
            record_event(
                kind,
                request=request,
                user=user,
                path=path,
                status_code=status_code,
                detail=detail,
                address=address,
                username=username,
            )
        except Exception as error:  # noqa: BLE001 - журнал не важнее страницы
            logger.warning("Запись в журнал не удалась: %s", error)


class NiceErrorPagesMiddleware:
    """Отдаёт нашу страницу 404 даже при DEBUG=1 (в бою это делает обработчик handler404).

    В режиме отладки Django показывает собственную страницу с перечнем маршрутов;
    чтобы «красивая» страница была видна и в демо-режиме, подменяем такой ответ.
    Django превращает исключение в ответ раньше, чем оно доходит до этого middleware,
    поэтому смотрим на готовый ответ, а не на исключение. Текст ошибки остаётся
    Ответ попадает в журнал сервера. Служебные адреса не трогаем.
    """

    SKIP = ("/static/", "/admin/", "/favicon.ico", "/media/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not (settings.DEBUG and getattr(settings, "NICE_ERRORS_IN_DEBUG", True)):
            return response
        if response.status_code != 404 or request.path.startswith(self.SKIP):
            return response
        if "text/html" not in response.get("Content-Type", ""):
            return response
        if "text/html" not in request.headers.get("Accept", "text/html"):
            return response
        from .error_views import page_not_found

        return page_not_found(request, Http404(request.path))