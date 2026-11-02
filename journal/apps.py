from django.apps import AppConfig


class JournalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "journal"
    verbose_name = "Электронный журнал"

    def ready(self):
        # Защита «нельзя оставить журнал без администратора»: приёмники pre_save
        # и pre_delete у User подключаются при импорте journal/guards.py.
        from . import guards  # noqa: F401
