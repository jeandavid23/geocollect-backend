from django.db import migrations


def refresh(apps, schema_editor):
    """Codes de correspondance recalculés avec la comparaison tolérante (séparateurs, accents, apostrophe Excel)."""
    from apps.producers.matching import normalize_code
    Producer = apps.get_model('producers', 'Producer')
    LegacyParcel = apps.get_model('parcels', 'LegacyParcel')
    batch = []
    for p in Producer.objects.only('id', 'field_id_base', 'match_key').iterator(chunk_size=2000):
        k = normalize_code(p.field_id_base) or ''
        if k != p.match_key:
            p.match_key = k
            batch.append(p)
    Producer.objects.bulk_update(batch, ['match_key'], batch_size=1000)
    batch = []
    for lp in LegacyParcel.objects.exclude(code='').only('id', 'code', 'match_key').iterator(chunk_size=2000):
        k = normalize_code(lp.code) or ''
        if k != lp.match_key:
            lp.match_key = k
            batch.append(lp)
    LegacyParcel.objects.bulk_update(batch, ['match_key'], batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [
        ('producers', '0007_producer_match_key_alter_producer_field_id_base_and_more'),
        ('parcels', '0005_legacyparcel_code_legacyparcel_match_key_and_more'),
    ]
    operations = [migrations.RunPython(refresh, migrations.RunPython.noop)]
