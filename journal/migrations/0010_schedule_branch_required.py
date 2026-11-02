"""Адрес занятия становится обязательным."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("journal", "0009_create_branches_and_rooms"),
    ]

    operations = [
        migrations.AlterField(
            model_name="schedule",
            name="branch",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="schedules",
                to="journal.branch",
                verbose_name="Адрес",
            ),
        ),
    ]
