from django.db import migrations

BATCH = 500


def recompute(apps, schema_editor):
    """Production estimée relue depuis le registre : la colonne est en tonnes (convertie en kg)."""
    from apps.producers.production import from_extra
    Producer = apps.get_model('producers', 'Producer')
    changed = []
    for p in Producer.objects.exclude(extra_data={}).only('id', 'extra_data', 'estimated_production_kg').iterator(chunk_size=BATCH):
        kg = from_extra(p.extra_data)
        if kg is not None and (p.estimated_production_kg is None or float(p.estimated_production_kg) != kg):
            p.estimated_production_kg = kg
            changed.append(p)
    Producer.objects.bulk_update(changed, ['estimated_production_kg'], batch_size=BATCH)


class Migration(migrations.Migration):

    dependencies = [
        ('producers', '0009_estimated_production'),
    ]

    operations = [
        migrations.RunPython(recompute, migrations.RunPython.noop),
    ]
