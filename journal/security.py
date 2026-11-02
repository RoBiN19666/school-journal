"""Безопасность: IP-адреса, правила доступа, защита от подбора пароля."""
import ipaddress
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import IpRule, VisitLog
from . import demo_router

logger = logging.getLogger(__name__)

LOOPBACK = ("127.0.0.1", "::1")
MAX_FAILED_ATTEMPTS = 5
MAX_FAILED_ATTEMPTS_IP = 30
LOCK_MINUTES = 15
LOG_RETENTION_DAYS = 30
PRUNE_INTERVAL = 3600  # чистим журнал не чаще раза в час на воркер
_PRUNE_STATE = {}


def is_valid_ip(value):
    try:
        ipaddress.ip_address(str(value).strip())
        return True
    except ValueError:
        return False


def get_client_ip(request):
    """IP клиента. Заголовки прокси учитываем только если включён TRUST_PROXY_HEADERS.

    Обратный прокси (nginx, traefik) обычно передаёт адрес в X-Forwarded-For,
    а если настроен только он — в X-Real-IP. Порядок именно такой: сначала
    X-Forwarded-For (там может быть цепочка адресов), затем X-Real-IP как
    запасной вариант, и только потом REMOTE_ADDR.
    """
    if getattr(settings, "TRUST_PROXY_HEADERS", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        for candidate in forwarded.split(","):
            candidate = candidate.strip()
            if is_valid_ip(candidate):
                return candidate
        real_ip = str(request.META.get("HTTP_X_REAL_IP", "")).strip()
        if is_valid_ip(real_ip):
            return real_ip
    address = request.META.get("REMOTE_ADDR") or ""
    return address if is_valid_ip(address) else ""


def _network(value):
    try:
        return ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError:
        return None


def match_rule(address):
    """Возвращает правило, под которое попадает адрес: allow, block или None.

    Более точные правила (с большей маской) важнее общих. Просроченные
    автоблокировки не учитываются — они снимаются сами.
    """
    if not is_valid_ip(address):
        return None
    ip = ipaddress.ip_address(address)
    matched = None
    matched_prefix = -1
    now = timezone.now()
    for rule in IpRule.objects.all():
        if rule.expires_at and rule.expires_at <= now:
            continue
        network = _network(rule.address)
        if network is None or ip not in network:
            continue
        if network.prefixlen > matched_prefix:
            matched = rule.kind
            matched_prefix = network.prefixlen
    return matched


def allow_rules_exist():
    return IpRule.objects.filter(kind=IpRule.Kind.ALLOW).exists()


# --- Защита от сканеров -------------------------------------------------------
#: Адреса, которых в журнале быть не может: их ищут автоматические сканеры
#: уязвимостей (файлы настроек, панели, GraphQL, служебные эндпоинты).
PROBE_MARKERS = (
    ".env", ".git", ".svn", ".ds_store", ".vscode", "sftp.json", "config.json",
    "wp-admin", "wp-login", "wp-content", "xmlrpc.php", "phpmyadmin", "pma/",
    "info.php", "adminer", "mysql/admin", "db.php", "sql.php",
    "graphql", "api/gql", "api/users", "api/v1/users", "api/tokens",
    "actuator", "/debug/", "telescope", "server-status", "trace.axd",
    "_catalog", "login.action", "json/version", "proxy_subdomain",
    "pom.properties", "exporttool", "security.txt", "cgi-bin",
    "vendor/phpunit", "solr/", "manager/html", "jenkins", "hudson",
    "eval-stdin", "autodiscover", "/ecp/", "druid", "nacos", "swagger",
    "phpinfo", ".ssh", "id_rsa", "composer.lock", "package-lock.json",
    "@vite", "vite/env", "sftp", ".well-known/security.txt", "proxy_subdomain",
    "backup.sql", "dump.sql", "database.sql", "www.zip", "backup.zip",
)


def looks_like_probe(path):
    """Похож ли адрес на попытку найти чужую служебную страницу или файл."""
    if not path:
        return False
    lowered = path.lower()
    return any(marker in lowered for marker in PROBE_MARKERS)


def recent_probes(address, minutes=None):
    """Сколько подозрительных запросов пришло с адреса за последнее время."""
    if not address:
        return 0
    minutes = minutes or getattr(settings, "AUTO_BLOCK_WINDOW_MINUTES", 15)
    since = timezone.now() - timedelta(minutes=minutes)
    return VisitLog.objects.filter(
        kind=VisitLog.Kind.PROBE, ip=address, created_at__gte=since
    ).count()


def is_private(address):
    """Локальные и служебные адреса не блокируем автоматически, чтобы не запереть себя."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def block_address(address, comment="", hours=None, automatic=True):
    """Ставит блокировку адреса (или продлевает её).

    ``hours=0`` — блокировка навсегда, ``hours=None`` — срок по умолчанию
    (для повторной автоблокировки тоже навсегда).

    В демонстрации правило не создаётся: демо-сессия не должна менять рабочую
    базу (``demo_router`` оставляет правила по IP в основной базе).
    """
    if demo_router.demo_active():
        logger.info("Демо-режим: правило для адреса %s не создаётся", address)
        return None, False
    if hours is None and automatic and IpRule.objects.filter(
        address=address, automatic=True
    ).exists():
        # Адрес блокируют повторно — значит правило не подействовало: блокируем навсегда.
        hours = 0
    hours = hours if hours is not None else getattr(settings, "AUTO_BLOCK_HOURS", 24)
    expires = timezone.now() + timedelta(hours=hours) if hours else None
    rule, created = IpRule.objects.update_or_create(
        address=address,
        defaults={
            "kind": IpRule.Kind.BLOCK,
            "comment": comment[:200],
            "expires_at": expires,
            "automatic": automatic,
        },
    )
    return rule, created


def prune_expired_rules():
    """Убирает просроченные автоблокировки, чтобы список не засорялся."""
    return (
        IpRule.objects.filter(automatic=True, expires_at__lte=timezone.now()).delete()[0]
    )


def access_denied(address):
    """Запрещён ли доступ с этого адреса."""
    if not address:
        return False
    if address in LOOPBACK:
        return False  # локальный доступ не блокируем, иначе легко запереть себя
    rule = match_rule(address)
    if rule == IpRule.Kind.BLOCK:
        return True
    if allow_rules_exist() and rule != IpRule.Kind.ALLOW:
        return True
    return False


# --- Защита от подбора пароля -------------------------------------------------
def failed_attempts(address, username=None, minutes=LOCK_MINUTES):
    """Сколько неудачных входов было с адреса (и, если указан, по этому логину)."""
    since = timezone.now() - timedelta(minutes=minutes)
    queryset = VisitLog.objects.filter(
        kind=VisitLog.Kind.LOGIN_FAILED, ip=address, created_at__gte=since
    )
    if username:
        queryset = queryset.filter(username__iexact=username)
    return queryset.count()


def is_login_locked(address, username=None):
    """Блокируем подбор: 5 попыток на пару «адрес + логин» или 30 на адрес.

    Счёт по логину нужен, чтобы один человек, ошибившийся паролем, не запирал
    журнал всем, кто работает с того же адреса (офис часто выходит через один IP).
    """
    if not address:
        return False
    if failed_attempts(address) >= MAX_FAILED_ATTEMPTS_IP:
        return True
    if username and failed_attempts(address, username=username) >= MAX_FAILED_ATTEMPTS:
        return True
    return False


def record_event(kind, request=None, user=None, username="", path="", detail="",
                 status_code=None, address=None):
    """Пишет событие в журнал. Ошибка журнала не должна ломать страницу."""
    ip = address
    if ip is None and request is not None:
        ip = get_client_ip(request)
    user_agent = ""
    if request is not None:
        user_agent = request.META.get("HTTP_USER_AGENT", "")[:255]
    try:
        VisitLog.objects.create(
            kind=kind,
            ip=ip or None,
            user=user if getattr(user, "pk", None) else None,
            username=(username or "")[:150],
            path=(path or "")[:255],
            method=(getattr(request, "method", "") or "")[:10],
            status_code=status_code,
            user_agent=user_agent,
            detail=(detail or "")[:255],
        )
    except Exception as error:  # noqa: BLE001 - журнал не важнее самой страницы
        logger.warning("Не удалось записать событие журнала (%s): %s", kind, error)
        return None
    maybe_prune()
    return True


def prune_log(batch=500):
    """Удаляет старые записи журнала небольшими порциями.

    Небольшая порция и отдельные транзакции снижают риск блокировок при
    одновременных запросах (вкладки, выгрузки, несколько воркеров).
    """
    limit = timezone.now() - timedelta(days=LOG_RETENTION_DAYS)
    removed = 0
    for _ in range(20):  # максимум 10 000 записей за один вызов
        ids = list(
            VisitLog.objects.filter(created_at__lt=limit)
            .values_list("pk", flat=True)[:batch]
        )
        if not ids:
            break
        try:
            VisitLog.objects.filter(pk__in=ids).delete()
        except Exception as error:  # noqa: BLE001 - чистка не должна ломать запрос
            logger.warning("Не удалось почистить журнал: %s", error)
            break
        removed += len(ids)
        if len(ids) < batch:
            break
    return removed


def maybe_prune(force=False):
    """Чистит журнал редко: раз в час на воркер, а не на каждый запрос."""
    stamp = _PRUNE_STATE.get("at")
    now = timezone.now()
    if not force and stamp is not None and (now - stamp).total_seconds() < PRUNE_INTERVAL:
        return 0
    _PRUNE_STATE["at"] = now
    return prune_log()


def clear_journal(kinds=None):
    """Очистка журнала (используется на странице безопасности и в команде)."""
    queryset = VisitLog.objects.all()
    if kinds:
        queryset = queryset.filter(kind__in=kinds)
    return queryset.delete()[0]
