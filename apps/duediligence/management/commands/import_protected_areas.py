"""
Import des aires protégées (WDPA) et des zones clés de biodiversité (KBA) — fichiers GeoJSON (WGS84).

    python manage.py import_protected_areas --wdpa wdpa_civ.geojson --wdpa-version Oct2026 --replace
    python manage.py import_protected_areas --kba kba_civ.geojson --kba-version 2026_02 --replace

Les données ne sont JAMAIS committées (dépôt public) : elles vont directement en base.
Conversion d'un shapefile : ogr2ogr -f GeoJSON -t_srs EPSG:4326 sortie.geojson entree.shp
Seuls les polygones sont importés (les points WDPA n'ont pas de contour).
"""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.duediligence.models import ProtectedArea

FIELDS = {
    'WDPA': {'source_id': ('WDPAID', 'SITE_ID', 'wdpaid'), 'name': ('NAME', 'NAME_ENG', 'ORIG_NAME', 'name'),
             'designation': ('DESIG', 'DESIG_ENG', 'desig'), 'iucn_category': ('IUCN_CAT', 'iucn_cat'),
             'status': ('STATUS', 'status'), 'iso3': ('ISO3', 'PARENT_ISO', 'iso3')},
    'KBA': {'source_id': ('SitRecID', 'SITRECID', 'kba_id', 'id'), 'name': ('IntName', 'NatName', 'Name', 'name'),
            'designation': ('KbaStatus', 'kbastatus'), 'iucn_category': (), 'status': ('KbaStatus',),
            'iso3': ('ISO3', 'Country', 'iso3')},
}


def _pick(props, keys):
    for k in keys:
        v = props.get(k)
        if v not in (None, ''):
            return str(v)
    return ''


class Command(BaseCommand):
    help = 'Importe les aires protégées WDPA et les KBA (GeoJSON WGS84) pour la diligence raisonnée.'

    def add_arguments(self, parser):
        parser.add_argument('--wdpa')
        parser.add_argument('--kba')
        parser.add_argument('--wdpa-version', default='')
        parser.add_argument('--kba-version', default='')
        parser.add_argument('--replace', action='store_true')

    def handle(self, **o):
        if not (o['wdpa'] or o['kba']):
            raise CommandError('Indiquez --wdpa et/ou --kba.')
        for src, path, version in (('WDPA', o['wdpa'], o['wdpa_version']), ('KBA', o['kba'], o['kba_version'])):
            if not path:
                continue
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            objs = []
            for feat in data.get('features', []):
                g = feat.get('geometry') or {}
                if g.get('type') not in ('Polygon', 'MultiPolygon'):
                    continue
                p = feat.get('properties') or {}
                m = FIELDS[src]
                objs.append(ProtectedArea(source=src, geometry=g, version=version,
                                          **{k: _pick(p, keys)[:255] for k, keys in m.items()}))
            with transaction.atomic():
                if o['replace']:
                    ProtectedArea.objects.filter(source=src).delete()
                ProtectedArea.objects.bulk_create(objs, batch_size=200)
            self.stdout.write(self.style.SUCCESS(f'{src} : {len(objs)} polygone(s) importé(s) ({version or "version non précisée"}).'))
