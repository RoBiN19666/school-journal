"""Предметы становятся своими для каждой четверти и каждого адреса.

Раньше предмет был общим: одна запись на всю школу. Теперь предмет живёт внутри
четверти и адреса, поэтому существующие предметы нужно разложить по парам
(четверть, адрес) — по тем группам и занятиям, которые на них ссылаются.
Одинаковые названия в одной паре склеиваются в один предмет.
"""
from django.db import migrations


def split_subjects(apps, schema_editor):
    Subject = apps.get_model("journal", "Subject")
    Group = apps.get_model("journal", "Group")
    Schedule = apps.get_model("journal", "Schedule")
    Branch = apps.get_model("journal", "Branch")
    Term = apps.get_model("journal", "Term")

    branches = list(Branch.objects.order_by("order", "name"))
    default_branch_id = branches[0].pk if branches else None
    current_term = (
        Term.objects.order_by("-year__start_date", "number").first()
        or Term.objects.order_by("number").first()
    )
    current_term_id = current_term.pk if current_term else None

    seen = {}
    for subject in Subject.objects.order_by("pk"):
        groups = list(Group.objects.filter(subject_id=subject.pk))
        pairs = {}
        for group in groups:
            lesson = (
                Schedule.objects.filter(group_id=group.pk)
                .exclude(branch__isnull=True)
                .first()
            )
            branch_id = lesson.branch_id if lesson is not None else default_branch_id
            pairs.setdefault((group.term_id, branch_id), []).append(group.pk)
        if not pairs:
            pairs = {(current_term_id, default_branch_id): []}

        keeps_source = False
        first_pair = True
        for (term_id, branch_id), group_ids in sorted(
            pairs.items(), key=lambda item: (item[0][0] or 0, item[0][1] or 0)
        ):
            key = (term_id, branch_id, subject.name.strip().lower())
            target_id = seen.get(key)
            if target_id is None:
                if first_pair:
                    # Первая пара остаётся на самом предмете.
                    Subject.objects.filter(pk=subject.pk).update(
                        term_id=term_id, branch_id=branch_id
                    )
                    target_id = subject.pk
                    keeps_source = True
                else:
                    # Остальные пары получают свою запись предмета.
                    clone = Subject.objects.create(
                        name=subject.name,
                        teacher_id=subject.teacher_id,
                        term_id=term_id,
                        branch_id=branch_id,
                    )
                    target_id = clone.pk
                seen[key] = target_id
            first_pair = False
            if group_ids:
                Group.objects.filter(pk__in=group_ids).update(
                    subject_id=target_id, term_id=term_id
                )

        if not keeps_source and not Group.objects.filter(subject_id=subject.pk).exists():
            subject.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("journal", "0012_subject_branch_subject_term"),
    ]

    operations = [
        migrations.RunPython(split_subjects, migrations.RunPython.noop),
    ]
