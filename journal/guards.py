"""Защита «в журнале всегда остаётся активный администратор».

Раньше правило жило только в формах журнала (``journal/forms.py``,
``AccountSafetyMixin``): нельзя выключить себя и нельзя выключить или понизить
последнего активного администратора. Через админку Django, через код и через
команды правило обходилось — администратора можно было удалить или понизить и
остаться без доступа к журналу. Поэтому здесь оно перенесено на уровень модели:
приёмники ``pre_save`` и ``pre_delete`` у ``User`` (по образцу
``guard_term_save`` в ``journal/periods.py``).

Администратор — пользователь с ролью ``superadmin``/``admin`` или
``is_superuser=True``, при этом ``is_active=True``. Не допускается действие,
после которого активных администраторов в журнале не остаётся ни одного.

Проверяем только рабочую базу: демонстрационную пропускаем (как в других местах
проекта, см. ``journal/demo_router.py``), а состояние «таблиц ещё нет» (миграции,
первый запуск) аккуратно пропускаем, как ``guard_term_save``.
"""
from django.core.exceptions import ValidationError
from django.db import OperationalError, ProgrammingError, router
from django.db.models import Q, QuerySet
from django.db.models.signals import pre_delete, pre_save
from django.dispatch import receiver

from . import demo_router
from .models import User

#: Роли, которые дают доступ к управлению журналом, — «администраторы».
ADMIN_ROLE_VALUES = (User.Role.SUPERADMIN, User.Role.ADMIN)

#: Поля, от которых зависит, администратор ли эта учётная запись.
ADMIN_FIELDS = frozenset({"is_active", "role", "is_superuser"})

#: Понятное сообщение: журнал нельзя оставить без единого активного администратора.
LAST_ADMIN_ERROR = (
    "Нельзя оставить журнал без администратора. "
    "Сначала назначьте другого администратора."
)


# --- Кто считается администратором -------------------------------------------
def is_admin_account(user):
    """Администратор ли учётная запись по роли: главный администратор или администратор."""
    return bool(user.is_superuser or user.role in ADMIN_ROLE_VALUES)


def is_active_admin(user):
    """Активный администратор: роль администратора и учётная запись включена."""
    return bool(user.is_active) and is_admin_account(user)


def database_alias(instance, using=None):
    """База, к которой относится запись: та же, в которую её пишет Django."""
    alias = using or getattr(instance._state, "db", None)
    if alias:
        return alias
    return router.db_for_read(User, instance=instance) or "default"


def active_admins(alias, exclude_pk=None):
    """Активные администраторы базы ``alias`` (главный администратор тоже в счёт)."""
    query = (
        User.objects.using(alias)
        .filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(role__in=ADMIN_ROLE_VALUES))
    )
    if exclude_pk is not None:
        query = query.exclude(pk=exclude_pk)
    return query


def _stored_active_admin(instance, alias):
    """Записан ли в базе активный администратор с таким же pk (текущее состояние)."""
    if not instance.pk:
        return False
    return active_admins(alias).filter(pk=instance.pk).exists()


def last_admin_error(instance, using=None):
    """Понятное сообщение, если из-за этой записи журнал останется без администратора.

    Возвращает ``None``, когда запись можно сохранять: она и не была активным
    администратором, или остаётся им, или в журнале есть другой активный
    администратор. Одна и та же проверка нужна и приёмнику ``pre_save``, и
    модели (``User.clean``): благодаря ей админка Django показывает ошибку в
    форме, а не отдаёт пятисотую страницу.
    """
    alias = database_alias(instance, using)
    if alias == demo_router.DEMO_ALIAS:
        # Демонстрационная база: проверка не должна мешать демо-режиму.
        return None
    if not _stored_active_admin(instance, alias):
        return None
    if is_active_admin(instance):
        return None
    if active_admins(alias, exclude_pk=instance.pk).exists():
        return None
    return LAST_ADMIN_ERROR


# --- Сохранение ---------------------------------------------------------------
def _admin_status_changes(update_fields):
    """Трогает ли это сохранение поля, от которых зависит роль администратора."""
    if update_fields is None:
        return True
    return bool(ADMIN_FIELDS & set(update_fields))


def _queryset_admin_pks(origin):
    """pk записей, которые удалит этот QuerySet, или None при одиночном удалении."""
    if not isinstance(origin, QuerySet) or origin.model is not User:
        return None
    try:
        return set(origin.values_list("pk", flat=True))
    except Exception:  # noqa: BLE001 - набор удаляемых записей не узнать: считаем по одной
        return None


@receiver(pre_save, sender=User)
def guard_last_admin_save(sender, instance, using=None, update_fields=None, **kwargs):
    """Не даёт сохранить последнего активного администратора не-администратором.

    Ловит и выключение (``is_active=False``), и смену роли на не-администраторскую:
    и админка Django, и код, и команды сохраняют учётную запись через модель.
    """
    if not _admin_status_changes(update_fields):
        # Правка других полей (например, last_login) администратором запись не
        # делает и не перестаёт: в базе роль и включённость остаются прежними.
        return
    try:
        message = last_admin_error(instance, using=using)
    except (OperationalError, ProgrammingError):
        # Таблиц ещё нет — проверять нечего, запись делает сама база.
        return
    if message:
        raise ValidationError(message)


# --- Удаление -----------------------------------------------------------------
@receiver(pre_delete, sender=User)
def guard_last_admin_delete(sender, instance, using=None, origin=None, **kwargs):
    """Не даёт удалить последнего активного администратора.

    Django отправляет этот приёмник и при ``user.delete()``, и при массовом
    ``User.objects.filter(...).delete()`` — до первого удаления, для каждой
    записи (приёмник вообще отключает «быстрое» удаление без сигналов).
    """
    alias = database_alias(instance, using)
    if alias == demo_router.DEMO_ALIAS:
        # Демонстрационная база: проверка не должна мешать демо-режиму.
        return
    try:
        if not _stored_active_admin(instance, alias):
            return
        others = active_admins(alias, exclude_pk=instance.pk)
        # Массовое удаление: вычитаем всех, кого удаляет тот же QuerySet, иначе
        # каждый администратор по отдельности выглядел бы «не последним» и
        # удаление всех администраторов одной командой проходило бы.
        removed = _queryset_admin_pks(origin)
        if removed:
            others = others.exclude(pk__in=removed)
        if others.exists():
            return
    except (OperationalError, ProgrammingError):
        # Таблиц ещё нет — проверять нечего.
        return
    raise ValidationError(LAST_ADMIN_ERROR)
