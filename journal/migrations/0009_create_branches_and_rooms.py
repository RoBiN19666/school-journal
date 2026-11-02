"""Филиалы с кабинетами и адрес у существующих занятий.

Данные и схема разнесены по разным миграциям: на PostgreSQL нельзя менять схему
в той же транзакции, где уже были вставки.
"""
from django.db import migrations

#: Стартовые адреса журнала. Они намеренно нейтральные (демонстрационные):
#: настоящие адреса организации администратор вводит сам в разделе адресов.
DEFAULT_BRANCHES = (
    (
        "ул. Примерная, 1",
        (
            "Класс информатики и математики (1 этаж)",
            "Лаборатория биологии",
            "Лаборатория химии",
            "Учебный класс (2 этаж)",
            "Лаборатория физики",
            "Конференц-зал",
        ),
    ),
    ("ул. Вторая, 10", ("№1", "№2", "№11", "№13")),
)


def create_branches(apps, schema_editor):
    Branch = apps.get_model("journal", "Branch")
    Room = apps.get_model("journal", "Room")
    Schedule = apps.get_model("journal", "Schedule")

    first_branch = None
    for branch_order, (name, rooms) in enumerate(DEFAULT_BRANCHES):
        branch, _ = Branch.objects.get_or_create(
            name=name, defaults={"order": branch_order}
        )
        if first_branch is None:
            first_branch = branch
        for room_order, room_name in enumerate(rooms):
            Room.objects.get_or_create(
                branch=branch, name=room_name, defaults={"order": room_order}
            )

    if first_branch is not None:
        Schedule.objects.filter(branch__isnull=True).update(branch=first_branch)


class Migration(migrations.Migration):

    dependencies = [
        ("journal", "0008_branch_schedule_branch_room_schedule_room_and_more"),
    ]

    operations = [
        migrations.RunPython(create_branches, migrations.RunPython.noop),
    ]
