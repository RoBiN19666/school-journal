"""Разделение рабочей и демонстрационной баз.

Пока пользователь находится в демо-режиме (в его сессии стоит флаг ``demo``),
все обращения к данным журнала и к учётным записям идут в отдельную базу ``demo``.
Рабочая база при этом не меняется вообще: демонстрация физически не может
записать в неё ни одной строки.

Сессии и служебные таблицы Django всегда остаются в рабочей базе — иначе
пользователь потерял бы свою сессию при входе в демо-режим.
"""
import threading

DEMO_ALIAS = "demo"
MAIN_ALIAS = "default"

# Приложения, которые обязаны оставаться в рабочей базе.
STAY_IN_MAIN = {"sessions", "contenttypes", "admin"}
# Модели, которые остаются в рабочей базе: журнал доступа и правила по IP
# нужны и во время демонстрации (иначе записи откатывались бы вместе с ней).
STAY_IN_MAIN_MODELS = {"visitlog", "iprule", "mailsettings"}

_state = threading.local()


def demo_active():
    """Активен ли демо-режим в текущем потоке (запросе)."""
    return bool(getattr(_state, "active", False))


def set_demo_active(value):
    _state.active = bool(value)


class _DemoActive:
    """Контекстный менеджер: включает демо-базу на время работы."""

    def __enter__(self):
        self.previous = demo_active()
        set_demo_active(True)
        return self

    def __exit__(self, *exc_info):
        set_demo_active(self.previous)
        return False


activate = _DemoActive


class DemoRouter:
    """Направляет данные журнала в демо-базу, пока активен демо-режим."""

    def _target(self, model):
        if not demo_active():
            return None
        if model._meta.app_label in STAY_IN_MAIN:
            return None
        if model._meta.model_name in STAY_IN_MAIN_MODELS:
            return None
        return DEMO_ALIAS

    def db_for_read(self, model, **hints):
        return self._target(model)

    def db_for_write(self, model, **hints):
        return self._target(model)

    def allow_relation(self, obj1, obj2, **hints):
        # Связи между рабочей и демо-базой не разрешаем: это разные журналы.
        first = obj1._state.db
        second = obj2._state.db
        if first and second and first != second:
            return False
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # Миграции применяются к каждой базе отдельно, поэтому «своя» база разрешена.
        if db == DEMO_ALIAS:
            return True
        if db == MAIN_ALIAS and app_label in STAY_IN_MAIN:
            return True
        return None
