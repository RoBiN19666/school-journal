"""Основной преподаватель предмета попадает в список ведущих.

Раньше у предмета был один преподаватель, теперь их может быть несколько.
Чтобы карточка предмета сразу показывала полный список, переносим основного
преподавателя в новое поле «преподаватели».
"""
from django.db import migrations


def fill_subject_teachers(apps, schema_editor):
    Subject = apps.get_model("journal", "Subject")
    for subject in Subject.objects.exclude(teacher__isnull=True):
        subject.teachers.add(subject.teacher_id)


class Migration(migrations.Migration):

    dependencies = [
        ("journal", "0017_schedule_ignore_teacher_conflict_subject_teachers_and_more"),
    ]

    operations = [
        migrations.RunPython(fill_subject_teachers, migrations.RunPython.noop),
    ]
