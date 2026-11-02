"""Четверти превращаются в полугодия: данные сливаются, ничего не теряется.

В году было четыре четверти: 1–2 (сентябрь–декабрь) и 3–4 (январь–май).
Теперь полугодий два, поэтому четверти попарно сливаются:
предметы с одинаковым названием объединяются, группы с одинаковым названием
тоже (ученики и занятия переносятся), отметки сохраняются.
"""
from datetime import date

from django.db import migrations

# Границы полугодий: (месяц, день) начала и конца.
BOUNDS = (((9, 1), (12, 31)), ((1, 1), (5, 31)))


def _bounds(year):
    result = []
    for (start_month, start_day), (end_month, end_day) in BOUNDS:
        start_year = year.start_date.year if start_month >= 9 else year.end_date.year
        end_year = year.start_date.year if end_month >= 9 else year.end_date.year
        result.append(
            (date(start_year, start_month, start_day), date(end_year, end_month, end_day))
        )
    return result


def _move_term_data(apps, source, target):
    """Переносит предметы, группы, занятия и отметки из одной записи периода в другую."""
    Subject = apps.get_model("journal", "Subject")
    Group = apps.get_model("journal", "Group")
    Schedule = apps.get_model("journal", "Schedule")
    Attendance = apps.get_model("journal", "Attendance")

    for subject in Subject.objects.filter(term=source):
        target_subject = Subject.objects.filter(
            term=target, branch_id=subject.branch_id, name=subject.name
        ).first()
        if target_subject is None:
            target_subject = Subject.objects.create(
                name=subject.name,
                branch_id=subject.branch_id,
                term=target,
                teacher_id=subject.teacher_id,
            )
        elif target_subject.teacher_id is None and subject.teacher_id:
            target_subject.teacher_id = subject.teacher_id
            target_subject.save(update_fields=["teacher"])

        for group in Group.objects.filter(subject=subject):
            target_group = Group.objects.filter(
                term=target, subject=target_subject, name=group.name
            ).first()
            if target_group is None:
                group.subject = target_subject
                group.term = target
                group.save(update_fields=["subject", "term"])
                continue

            for student_id in group.students.values_list("pk", flat=True):
                target_group.students.add(student_id)
            for lesson in Schedule.objects.filter(group=group):
                existing = Schedule.objects.filter(
                    group=target_group,
                    day_of_week=lesson.day_of_week,
                    start_time=lesson.start_time,
                ).first()
                if existing is None:
                    lesson.group = target_group
                    lesson.save(update_fields=["group"])
                    continue
                # Такое занятие уже есть: переносим отметки, которые не задваиваются.
                for mark in Attendance.objects.filter(schedule=lesson):
                    Attendance.objects.get_or_create(
                        student_id=mark.student_id,
                        schedule=existing,
                        date=mark.date,
                        defaults={"status": mark.status},
                    )
                Attendance.objects.filter(schedule=lesson).delete()
                lesson.delete()
            group.delete()


def merge_terms(apps, schema_editor):
    AcademicYear = apps.get_model("journal", "AcademicYear")
    Term = apps.get_model("journal", "Term")

    for year in AcademicYear.objects.order_by("start_date"):
        terms = list(Term.objects.filter(year=year).order_by("number", "start_date"))
        if not terms:
            continue

        autumn = next((term for term in terms if term.start_date.month >= 8), terms[0])
        spring = next((term for term in terms if term.start_date.month < 8), None)
        if spring is None:
            spring = Term.objects.create(
                year=year,
                number=max(term.number for term in terms) + 1,
                start_date=_bounds(year)[1][0],
                end_date=_bounds(year)[1][1],
            )
            terms.append(spring)

        for term in terms:
            if term.pk in (autumn.pk, spring.pk):
                continue
            target = autumn if term.start_date.month >= 8 else spring
            _move_term_data(apps, term, target)
            term.delete()

        # Освобождаем номера и расставляем 1 и 2 (промежуточные номера — разные,
        # иначе уникальность «год + номер» не даст обновить обе записи).
        Term.objects.filter(pk=autumn.pk).update(number=98)
        Term.objects.filter(pk=spring.pk).update(number=99)
        autumn.start_date, autumn.end_date = _bounds(year)[0]
        spring.start_date, spring.end_date = _bounds(year)[1]
        Term.objects.filter(pk=autumn.pk).update(
            number=1, start_date=autumn.start_date, end_date=autumn.end_date
        )
        Term.objects.filter(pk=spring.pk).update(
            number=2, start_date=spring.start_date, end_date=spring.end_date
        )


class Migration(migrations.Migration):

    dependencies = [
        ("journal", "0014_subject_scope_required"),
    ]

    operations = [
        migrations.RunPython(merge_terms, migrations.RunPython.noop),
    ]
