"""Четверть у группы становится обязательной, уникальность имени — внутри четверти."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("journal", "0006_create_periods_and_assign_groups"),
    ]

    operations = [
        migrations.AlterField(
            model_name="group",
            name="term",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="groups",
                to="journal.term",
                verbose_name="Четверть",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="group", name="unique_group_name_per_subject"
        ),
        migrations.AddConstraint(
            model_name="group",
            constraint=models.UniqueConstraint(
                fields=("term", "subject", "name"),
                name="unique_group_name_per_subject_term",
            ),
        ),
    ]
