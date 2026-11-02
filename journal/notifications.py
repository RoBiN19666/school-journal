"""Уведомления на почту главному администратору."""
import logging

from django.conf import settings
from django.core.mail import get_connection, send_mail
from django.utils import timezone

from .models import MailSettings

logger = logging.getLogger(__name__)

EVENT_FIELDS = {
    "logins": "notify_logins",
    "failed_logins": "notify_failed_logins",
    "ip_blocked": "notify_ip_blocked",
    "backup": "notify_backup",
    "accounts": "notify_accounts",
}


def notify(event, subject, message):
    """Отправляет письмо, если событие включено в настройках. Возвращает True/False."""
    field = EVENT_FIELDS.get(event)
    if field is None:
        return False
    try:
        config = MailSettings.load()
    except Exception:  # noqa: BLE001 - база может быть ещё не готова
        return False
    if not getattr(config, field, False) or not config.is_configured:
        return False

    body = (
        f"{message}\n\n"
        f"— Электронный журнал\n"
        f"Время сервера: {timezone.localtime():%d.%m.%Y %H:%M}\n"
    )
    try:
        connection = get_connection(
            backend=getattr(
                settings,
                "NOTIFY_EMAIL_BACKEND",
                "django.core.mail.backends.smtp.EmailBackend",
            ),
            host=config.host,
            port=config.port,
            username=config.username or None,
            password=config.password or None,
            use_tls=config.use_tls,
            use_ssl=config.use_ssl,
            timeout=15,
            fail_silently=False,
        )
        send_mail(
            subject=f"[Электронный журнал] {subject}",
            message=body,
            from_email=config.from_email or settings.DEFAULT_FROM_EMAIL,
            recipient_list=config.recipient_list,
            connection=connection,
            fail_silently=False,
        )
        return True
    except Exception as error:  # noqa: BLE001 - письмо не должно ломать работу сайта
        logger.warning("Не удалось отправить уведомление (%s): %s", event, error)
        return False


def send_test_email(address):
    """Проверка настроек: письмо на указанный адрес. Возвращает (успех, текст)."""
    config = MailSettings.load()
    if not config.host:
        return False, "Не указан SMTP-сервер."
    try:
        connection = get_connection(
            backend=getattr(
                settings,
                "NOTIFY_EMAIL_BACKEND",
                "django.core.mail.backends.smtp.EmailBackend",
            ),
            host=config.host,
            port=config.port,
            username=config.username or None,
            password=config.password or None,
            use_tls=config.use_tls,
            use_ssl=config.use_ssl,
            timeout=15,
            fail_silently=False,
        )
        send_mail(
            subject="[Электронный журнал] Проверка настроек почты",
            message="Это тестовое письмо. Значит, уведомления будут приходить.",
            from_email=config.from_email or settings.DEFAULT_FROM_EMAIL,
            recipient_list=[address],
            connection=connection,
            fail_silently=False,
        )
        return True, f"Письмо отправлено на {address}."
    except Exception as error:  # noqa: BLE001
        return False, f"Ошибка отправки: {error}"
