from django.db import migrations


def run(apps, schema_editor):
    """Classe les notifications existantes d'après leur titre (sécurité, messages, analyses, abonnement)."""
    N = apps.get_model('accounts', 'Notification')
    rules = [
        ('security', ['Nouvelle connexion', 'Tentatives de connexion', 'Mot de passe', 'mot de passe', 'Votre compte est']),
        ('message', ['— message de']),
        ('subscription', ['abonnement', 'Abonnement', 'licence']),
        ('tool', ['terminé', 'Analyse', 'Rapport']),
    ]
    for cat, words in rules:
        for w in words:
            N.objects.filter(category='activity', title__contains=w).update(category=cat)


class Migration(migrations.Migration):
    dependencies = [('accounts', '0006_notification_category')]
    operations = [migrations.RunPython(run, migrations.RunPython.noop)]
