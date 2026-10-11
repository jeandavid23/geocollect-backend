from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Réinitialise la double authentification d'un compte (téléphone perdu) : python manage.py reset_mfa <identifiant>"

    def add_arguments(self, parser):
        parser.add_argument('username')

    def handle(self, username, **opts):
        u = User.objects.filter(username=username).first()
        if u is None:
            raise CommandError(f'Compte introuvable : {username}')
        u.totp_enabled, u.totp_secret, u.totp_recovery = False, '', []
        u.save(update_fields=['totp_enabled', 'totp_secret', 'totp_recovery'])
        self.stdout.write(self.style.SUCCESS(f'Double authentification réinitialisée pour {username}.'))
