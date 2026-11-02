"""Очистка демонстрационных данных."""
from django.core.management.base import BaseCommand

from journal import demo


class Command(BaseCommand):
    help = "Удаляет демонстрационные данные. Рабочий журнал не затрагивает."

    def handle(self, *args, **options):
        if not demo.demo_database_ready():
            self.stdout.write("Демонстрационная база недоступна или пуста.")
            return
        removed = demo.wipe_demo_data()
        self.stdout.write(
            self.style.SUCCESS(
                "Очищено: "
                + ", ".join(f"{label} — {count}" for label, count in removed.items() if count)
            )
        )
        left = demo.demo_branches_with_data()
        if left:
            self.stdout.write(
                self.style.WARNING(
                    "Не тронуты адреса со связанными данными: " + ", ".join(left)
                )
            )
