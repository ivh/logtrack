from django.db import migrations


def drying_to_green(apps, schema_editor):
    Lumber = apps.get_model("mill", "Lumber")
    Lumber.objects.filter(status="drying").update(status="green")


class Migration(migrations.Migration):
    dependencies = [
        ("mill", "0008_alter_lumber_status"),
    ]
    operations = [
        migrations.RunPython(drying_to_green, reverse_code=migrations.RunPython.noop),
    ]
