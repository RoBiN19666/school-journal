"""Очистка демонстрационных данных."""
from django.core.management.base import BaseCommand

from journal import demo


class Command(BaseCommand):
    help = "Removes demo data. The working journal is not affected."

    def handle(self, *args, **options):
        if not demo.demo_database_ready():
            self.stdout.write("The demo database is unavailable or empty.")
            return
        removed = demo.english_labels(demo.wipe_demo_data())
        self.stdout.write(
            self.style.SUCCESS(
                "Removed: "
                + ", ".join(f"{label}: {count}" for label, count in removed.items() if count)
            )
        )
        left = demo.demo_branches_with_data()
        if left:
            self.stdout.write(
                self.style.WARNING(
                    "Branches with related data left in place: " + ", ".join(left)
                )
            )
