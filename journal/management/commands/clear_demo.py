"""Очистка демонстрационных данных."""
from django.core.management.base import BaseCommand

from journal import demo


class Command(BaseCommand):
    help = "Удаляет демонстрационные данные. Рабочий журнал не затрагивает."

    def handle(self, *args, **options):
        counts = demo.demo_database_counts()
        if counts is None:
            self.stdout.write("Демонстрационная база недоступна или пуста.")
            return
        self.stdout.write("Демонстрационная база:")
        for label, value in counts.items():
            self.stdout.write(f"  {label}: {value}")
        removed = demo.wipe_demo_data()
        self.stdout.write(
            self.style.SUCCESS(
                "Очищено: "
                + ", ".join(f"{label} — {count}" for label, count in removed.items() if count)
            )
        )
