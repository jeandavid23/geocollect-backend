"""
Crée (ou promeut) le compte propriétaire de la plateforme (Super Super Admin).

  python manage.py create_owner --username jdkonan --full-name "Jean David Konan" --email jeandavidkyao@gmail.com

Affiche le mot de passe généré une seule fois (ou utilise --password).
"""
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.credentials import generate_password
from apps.accounts.models import User


class Command(BaseCommand):
    help = 'Crée ou promeut le compte propriétaire de la plateforme (rôle owner).'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True)
        parser.add_argument('--full-name', default='Propriétaire de la plateforme')
        parser.add_argument('--email', default='')
        parser.add_argument('--phone', default='')
        parser.add_argument('--password', default='')

    def handle(self, *args, **o):
        email = (o['email'] or '').strip() or None
        if email and User.objects.filter(email__iexact=email).exclude(username=o['username']).exists():
            raise CommandError(f"L'adresse {email} est déjà utilisée par un autre compte.")
        password = o['password'] or generate_password(14)
        user, created = User.objects.get_or_create(username=o['username'], defaults={'full_name': o['full_name']})
        user.full_name = o['full_name']
        user.email = email
        user.phone = o['phone']
        user.role = User.Role.OWNER
        user.cooperative = None
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(
            f"{'Créé' if created else 'Promu'} : {user.username} (Super Super Admin)\nMot de passe : {password}"))
