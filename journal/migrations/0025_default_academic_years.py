"""Учебные годы создаются сразу: их не нужно добавлять вручную."""
from datetime import date

from django.db import migrations


def create_years(apps, schema_editor):
    """Три учебных года вокруг текущего — чтобы сразу можно было переключаться."""
    year_model = apps.get_model("journal", "AcademicYear")
    term_model = apps.get_model("journal", "Term")
    today = date.today()
    current = today.year if today.month >= 9 else today.year - 1
    for number in (current - 1, current, current + 1):
        start = date(number, 9, 1)
        # Год мог быть заведён с другими датами — ищем по году начала.
        year = year_model.objects.filter(start_date__year=number).first()
        if year is None:
            year = year_model.objects.create(
                start_date=start, end_date=date(number + 1, 8, 31)
            )
        if not term_model.objects.filter(year=year).exists():
            term_model.objects.create(
                year=year, number=1, start_date=start, end_date=date(number, 12, 31)
            )
            term_model.objects.create(
                year=year, number=2, start_date=date(number + 1, 1, 1),
                end_date=date(number + 1, 5, 31),
            )


class Migration(migrations.Migration):

    dependencies = [
        ("journal", "0024_superadmin_role"),
    ]

    operations = [
        migrations.RunPython(create_years, migrations.RunPython.noop),
    ]
