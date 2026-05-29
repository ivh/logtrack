# Split Lumber into a saleable batch + per-log source rows (through model).

import django.db.models.deletion
from django.db import migrations, models


def copy_to_sources(apps, schema_editor):
    """Each existing Lumber becomes a batch with one source: its log + count."""
    Lumber = apps.get_model("mill", "Lumber")
    LumberSource = apps.get_model("mill", "LumberSource")
    LumberSource.objects.bulk_create(
        LumberSource(lumber_id=l.id, log_id=l.log_id, count=l.count)
        for l in Lumber.objects.all()
    )


def restore_from_sources(apps, schema_editor):
    """Best-effort reverse: fold the first source back onto the lumber row."""
    Lumber = apps.get_model("mill", "Lumber")
    LumberSource = apps.get_model("mill", "LumberSource")
    for lumber in Lumber.objects.all():
        src = LumberSource.objects.filter(lumber_id=lumber.id).order_by("id").first()
        if src:
            lumber.log_id = src.log_id
            lumber.count = src.count
            lumber.save(update_fields=["log", "count"])


class Migration(migrations.Migration):

    dependencies = [
        ('mill', '0009_drying_to_green'),
    ]

    operations = [
        migrations.CreateModel(
            name='LumberSource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('count', models.PositiveSmallIntegerField(default=1, verbose_name='antal')),
                ('log', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lumber_sources', to='mill.log', verbose_name='stock')),
                ('lumber', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sources', to='mill.lumber', verbose_name='virkesparti')),
            ],
            options={
                'verbose_name': 'stockandel',
                'verbose_name_plural': 'stockandelar',
            },
        ),
        migrations.AddField(
            model_name='lumber',
            name='logs',
            field=models.ManyToManyField(related_name='lumber_batches', through='mill.LumberSource', to='mill.log', verbose_name='stockar'),
        ),
        migrations.AddConstraint(
            model_name='lumbersource',
            constraint=models.UniqueConstraint(fields=('lumber', 'log'), name='uniq_lumber_log'),
        ),
        migrations.RunPython(copy_to_sources, restore_from_sources),
        migrations.RemoveField(
            model_name='lumber',
            name='count',
        ),
        migrations.RemoveField(
            model_name='lumber',
            name='log',
        ),
    ]
