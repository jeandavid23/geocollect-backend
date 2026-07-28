from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('producers', '0001_initial'),
    ]

    operations = [
        # ── EXPLOITATION AGRICOLE ──
        migrations.AddField(
            model_name='producer',
            name='national_farm_id',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name="Identifiant national d'exploitation"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='producer',
            name='district',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='District / État / Province'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='producer',
            name='total_area_ha',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name='Superficie totale (ha)'),
        ),
        migrations.AddField(
            model_name='producer',
            name='farm_type',
            field=models.CharField(choices=[('small', 'Petite'), ('large', 'Grande')], default='small', max_length=10, verbose_name="Type d'exploitation"),
        ),
        migrations.AddField(
            model_name='producer',
            name='num_units',
            field=models.PositiveSmallIntegerField(default=1, verbose_name="Nombre d'unités agricoles"),
        ),
        migrations.AddField(
            model_name='producer',
            name='certification_year',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Année de certification'),
        ),
        # ── PROPRIÉTAIRE ──
        migrations.AddField(
            model_name='producer',
            name='owner_first_name',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='Prénom (propriétaire)'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='producer',
            name='owner_last_name',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='Nom (propriétaire)'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='producer',
            name='owner_phone',
            field=models.CharField(blank=True, default='', max_length=20, verbose_name='Téléphone (propriétaire)'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='producer',
            name='owner_national_id',
            field=models.CharField(blank=True, default='', max_length=50, verbose_name="N° identification (propriétaire)"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='producer',
            name='owner_gender',
            field=models.CharField(blank=True, choices=[('M', 'Homme'), ('F', 'Femme')], default='', max_length=1, verbose_name='Genre (propriétaire)'),
            preserve_default=False,
        ),
        # ── TRAVAILLEURS ──
        migrations.AddField(
            model_name='producer',
            name='permanent_workers',
            field=models.PositiveSmallIntegerField(default=0, verbose_name='Travailleurs permanents'),
        ),
        migrations.AddField(
            model_name='producer',
            name='temporary_workers',
            field=models.PositiveSmallIntegerField(default=0, verbose_name='Travailleurs temporaires / an'),
        ),
        # ── INSPECTION INTERNE ──
        migrations.AddField(
            model_name='producer',
            name='inspector_name',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name="Nom de l'inspecteur interne"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='producer',
            name='inspection_year',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="Année d'inspection"),
        ),
        migrations.AddField(
            model_name='producer',
            name='inspection_month',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="Mois d'inspection"),
        ),
        migrations.AddField(
            model_name='producer',
            name='inspection_day',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="Jour d'inspection"),
        ),
    ]
