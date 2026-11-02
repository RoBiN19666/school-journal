"""Создаём текущий учебный год с четвертями и привязываем к нему существующие группы.

Это отдельная миграция только с данными: на PostgreSQL нельзя менять схему
в той же транзакции, где уже были вставки (ошибка «pending trigger events»).
"""
from datetime import date

from django.db import migrations

QUARTER_BOUNDARIES = (
    ((9, 1), (10, 26)),
    ((11, 5), (12, 28)),
    ((1, 9), (3, 22)),
    ((4, 1), (5, 31)),
)


def academic_year_bounds(day):
    if day.month >= 9:
        return date(day.year, 9, 1), date(day.year + 1, 8, 31)
    return date(day.year - 1, 9, 1), date(day.year, 8, 31)


def quarter_bounds(year):
    bounds = []
    for (start_month, start_day), (end_month, end_day) in QUARTER_BOUNDARIES:
        start_year = year.start_date.year if start_month >= 9 else year.end_date.year
        end_year = year.start_date.year if end_month >= 9 else year.end_date.year
        bounds.append(
            (
                date(start_year, start_month, start_day),
                date(end_year, end_month, end_day),
            )
        )
    return bounds


def create_periods(apps, schema_editor):
    AcademicYear = apps.get_model("journal", "AcademicYear")
    Term = apps.get_model("journal", "Term")
    Group = apps.get_model("journal", "Group")

    today = date.today()
    start, end = academic_year_bounds(today)
    year, _ = AcademicYear.objects.get_or_create(start_date=start, end_date=end)

    terms = []
    for number, (term_start, term_end) in enumerate(quarter_bounds(year), start=1):
        term, _ = Term.objects.get_or_create(
            year=year,
            number=number,
            defaults={"start_date": term_start, "end_date": term_end},
        )
        terms.append(term)

    current = next(
        (term for term in terms if term.start_date <= today <= term.end_date), None
    )
    if current is None:
        current = terms[0] if terms else None
    if current is not None:
        Group.objects.filter(term__isnull=True).update(term=current)


class Migration(migrations.Migration):

    dependencies = [
        ("journal", "0005_academicyear_term_academicyear_unique_academic_year_and_more"),
    ]

    operations = [
        migrations.RunPython(create_periods, migrations.RunPython.noop),
    ]
