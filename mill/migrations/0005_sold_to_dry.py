from django.db import migrations


def sold_to_dry(apps, schema_editor):
    Lumber = apps.get_model("mill", "Lumber")
    Lumber.objects.filter(status="sold").update(status="dry")


class Migration(migrations.Migration):
    dependencies = [
        ("mill", "0004_lumber_bokio_invoice_id_lumber_bokio_line_item_id_and_more"),
    ]
    operations = [
        migrations.RunPython(sold_to_dry, reverse_code=migrations.RunPython.noop),
    ]
