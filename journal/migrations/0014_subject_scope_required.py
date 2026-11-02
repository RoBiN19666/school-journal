"""Адрес и четверть предмета становятся обязательными, добавляется уникальность."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("journal", "0013_split_subjects_by_term_and_branch"),
    ]

    operations = [
        migrations.AlterField(
            model_name="subject",
            name="branch",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="subjects",
                to="journal.branch",
                verbose_name="Адрес",
            ),
        ),
        migrations.AlterField(
            model_name="subject",
            name="term",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="subjects",
                to="journal.term",
                verbose_name="Четверть",
            ),
        ),
        migrations.AddConstraint(
            model_name="subject",
            constraint=models.UniqueConstraint(
                fields=("term", "branch", "name"),
                name="unique_subject_name_per_term_branch",
            ),
        ),
    ]
