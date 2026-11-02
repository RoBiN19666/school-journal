"""Убираем номер урока: строки расписания определяет время занятия.

Существующие занятия не теряются: номер урока превращается во время
(09:00, 10:30, 12:00 …), после чего поле удаляется.
"""
from datetime import datetime, time, timedelta

from django.db import migrations, models

DAY_START = time(9, 0)
LESSON_LENGTH = timedelta(minutes=90)
LAST_START = time(22, 30)
SHIFT = timedelta(minutes=10)


def _combine(value):
    return datetime.combine(datetime(2000, 1, 1), value)


def lesson_number_to_time(apps, schema_editor):
    """Проставляет время занятиям, созданным до появления времени."""
    schedule_model = apps.get_model("journal", "Schedule")
    for schedule in schedule_model.objects.all():
        start = schedule.start_time
        end = schedule.end_time
        number = schedule.lesson_number or 1
        if start is None:
            start = (_combine(DAY_START) + (number - 1) * LESSON_LENGTH).time()
            if start > LAST_START:
                start = LAST_START
        if end is None:
            end = (_combine(start) + LESSON_LENGTH).time()
        schedule_model.objects.filter(pk=schedule.pk).update(
            start_time=start, end_time=end
        )


def free_duplicate_starts(apps, schema_editor):
    """Разводит занятия, у которых после переноса совпали группа, день и время."""
    schedule_model = apps.get_model("journal", "Schedule")
    seen = set()
    for schedule in schedule_model.objects.order_by(
        "group_id", "day_of_week", "start_time", "pk"
    ):
        key = (schedule.group_id, schedule.day_of_week, schedule.start_time)
        if key not in seen:
            seen.add(key)
            continue
        start, end = schedule.start_time, schedule.end_time
        for _ in range(60):
            start = (_combine(start) + SHIFT).time()
            end = (_combine(end) + SHIFT).time()
            if (schedule.group_id, schedule.day_of_week, start) not in seen:
                break
        seen.add((schedule.group_id, schedule.day_of_week, start))
        schedule_model.objects.filter(pk=schedule.pk).update(
            start_time=start, end_time=end
        )


class Migration(migrations.Migration):

    dependencies = [
        ("journal", "0003_alter_schedule_options_schedule_end_time_and_more"),
    ]

    operations = [
        migrations.RunPython(lesson_number_to_time, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="schedule", name="unique_lesson_per_group"
        ),
        migrations.AlterModelOptions(
            name="schedule",
            options={
                "ordering": ("day_of_week", "start_time"),
                "verbose_name": "Занятие",
                "verbose_name_plural": "Расписание",
            },
        ),
        migrations.RemoveField(model_name="schedule", name="lesson_number"),
        migrations.AlterField(
            model_name="schedule",
            name="start_time",
            field=models.TimeField(verbose_name="Начало занятия"),
        ),
        migrations.AlterField(
            model_name="schedule",
            name="end_time",
            field=models.TimeField(verbose_name="Окончание занятия"),
        ),
        migrations.RunPython(free_duplicate_starts, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="schedule",
            constraint=models.UniqueConstraint(
                fields=("group", "day_of_week", "start_time"),
                name="unique_group_lesson_start",
            ),
        ),
    ]
