"""
Prépare le cache des tuiles TMF (le serveur du JRC ne permet pas la lecture partielle : tuiles de 65 à 115 Mo).

    python manage.py warm_tmf_cache                 # Côte d'Ivoire
    python manage.py warm_tmf_cache --bbox -8.6 4.3 -2.5 10.8

À lancer une fois sur le disque persistant (EUDR_TMF_CACHE), puis régler EUDR_TMF_DOWNLOAD=0.
"""
from django.core.management.base import BaseCommand

CIV = (-8.6, 4.3, -2.5, 10.8)


class Command(BaseCommand):
    help = "Télécharge les tuiles TMF (forêt 2020, déforestation, dégradation) d'une emprise dans le cache."

    def add_arguments(self, parser):
        parser.add_argument('--bbox', nargs=4, type=float, metavar=('MINX', 'MINY', 'MAXX', 'MAXY'))

    def handle(self, **o):
        import os
        os.environ['EUDR_TMF_DOWNLOAD'] = '1'
        from eudr_evidence.readers.base import tmf_cache_dir, warm_tmf_cache
        paths = warm_tmf_cache(tuple(o['bbox'] or CIV), log=lambda m: self.stdout.write(m))
        self.stdout.write(self.style.SUCCESS(f'{len([p for p in paths if p])} tuile(s) en cache dans {tmf_cache_dir()}.'))
