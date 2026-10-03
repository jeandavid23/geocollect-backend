"""
Rattache les coopératives existantes au premier super admin existant (le compte « admin »),
pour que son interface reste identique. Le propriétaire de la plateforme (rôle owner) voit tout.
"""
from django.db import migrations


def assign(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    Cooperative = apps.get_model('cooperatives', 'Cooperative')
    first_admin = User.objects.filter(role='super_admin').order_by('created_at').first()
    if first_admin:
        Cooperative.objects.filter(managed_by__isnull=True).update(managed_by=first_admin)


class Migration(migrations.Migration):
    dependencies = [
        ('cooperatives', '0002_cooperative_managed_by'),
        ('accounts', '0003_owner_role_adminlicense'),
    ]
    operations = [migrations.RunPython(assign, migrations.RunPython.noop)]
