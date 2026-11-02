"""Владельцы журнала получают роль «Главный администратор»."""
from django.db import migrations


def make_superadmins(apps, schema_editor):
    """Первый администратор становится главным администратором."""
    user_model = apps.get_model("journal", "User")
    user_model.objects.filter(is_superuser=True).update(role="superadmin")


class Migration(migrations.Migration):

    dependencies = [
        ("journal", "0023_alter_user_role"),
    ]

    operations = [
        migrations.RunPython(make_superadmins, migrations.RunPython.noop),
    ]
