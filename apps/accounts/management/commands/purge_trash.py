from django.core.management.base import BaseCommand

from apps.accounts.purge import purge_expired


class Command(BaseCommand):
    help = 'Efface définitivement les éléments de la corbeille de plus de 30 jours (à planifier chaque jour).'

    def handle(self, **opts):
        purge_expired()
        self.stdout.write(self.style.SUCCESS('Corbeille nettoyée.'))
