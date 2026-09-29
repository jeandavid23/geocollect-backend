"""
Importe les zones foncières (matrice RDUE) depuis des GeoPackage :

  python manage.py import_landuse --fc "Base des FC.gpkg" --enclaves "Base des enclaves.gpkg" --replace

- Forêts classées / parcs / réserves : champ « Type_d_Ft » (Forêt classée, Parc national, réserve).
- Enclaves : toutes les entités du fichier.
Les géométries sont reprojetées en WGS84 (EPSG:4326) et arrondies à 6 décimales (~10 cm).
"""
import sqlite3
import unicodedata

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rasterio.warp import transform_geom
from shapely import from_wkb
from shapely.geometry import mapping

from apps.parcels.models import LandZone


def _norm(v):
    return unicodedata.normalize('NFD', str(v or '')).encode('ascii', 'ignore').decode().lower().strip()


def category_for(type_ft):
    t = _norm(type_ft)
    if 'enclave' in t:
        return LandZone.Category.ENCLAVE
    if 'agro' in t:
        return LandZone.Category.AGROFORET
    if 'parc' in t or 'reserve' in t:
        return LandZone.Category.PARC
    if 'foret' in t or 'classe' in t:
        return LandZone.Category.FORET_CLASSEE
    return None


def _round(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 6), round(coords[1], 6)]
    return [_round(c) for c in coords]


def read_gpkg(path):
    """Entités d'un GeoPackage : (attributs, géométrie GeoJSON WGS84)."""
    db = sqlite3.connect(path)
    try:
        layers = db.execute('select table_name, column_name, srs_id from gpkg_geometry_columns').fetchall()
    except sqlite3.DatabaseError as exc:
        raise CommandError(f'{path} : GeoPackage illisible ({exc})')
    for table, geom_col, srs_id in layers:
        org, code = db.execute('select organization, organization_coordsys_id from gpkg_spatial_ref_sys where srs_id=?',
                               (srs_id,)).fetchone()
        src_crs = f'{org}:{code}'
        cur = db.execute(f'select * from "{table}"')
        cols = [d[0] for d in cur.description]
        for row in cur:
            rec = dict(zip(cols, row))
            blob = rec.pop(geom_col)
            if not blob:
                continue
            flags = blob[3]
            envelope = [0, 32, 48, 48, 64][(flags >> 1) & 7]
            geom = from_wkb(bytes(blob[8 + envelope:]))
            if geom.is_empty:
                continue
            gj = mapping(geom)
            if src_crs != 'EPSG:4326':
                gj = transform_geom(src_crs, 'EPSG:4326', gj)
            gj = {'type': gj['type'], 'coordinates': _round(gj['coordinates'])}
            yield {k: v for k, v in rec.items() if isinstance(v, (str, int, float)) or v is None}, gj


class Command(BaseCommand):
    help = 'Importe les forêts classées, parcs, réserves et enclaves (matrice RDUE).'

    def add_arguments(self, parser):
        parser.add_argument('--fc', help='GeoPackage des forêts classées / parcs / réserves (champ Type_d_Ft)')
        parser.add_argument('--enclaves', help='GeoPackage des enclaves')
        parser.add_argument('--replace', action='store_true', help='Remplace toutes les zones existantes')

    def handle(self, *args, **opts):
        if not opts['fc'] and not opts['enclaves']:
            raise CommandError('Indiquez --fc et/ou --enclaves.')
        zones, skipped = [], 0
        if opts['fc']:
            for attrs, geom in read_gpkg(opts['fc']):
                cat = category_for(attrs.get('Type_d_Ft') or attrs.get('cate'))
                if cat is None:
                    skipped += 1
                    continue
                zones.append(LandZone(category=cat, name=str(attrs.get('nom') or '')[:255],
                                      region=str(attrs.get('Region') or '')[:100], attributes=attrs,
                                      geometry=geom, source_file=opts['fc'].split('/')[-1]))
        if opts['enclaves']:
            for i, (attrs, geom) in enumerate(read_gpkg(opts['enclaves'])):
                zones.append(LandZone(category=LandZone.Category.ENCLAVE,
                                      name=str(attrs.get('Name') or f'Enclave {i + 1}')[:255], attributes=attrs,
                                      geometry=geom, source_file=opts['enclaves'].split('/')[-1]))
        with transaction.atomic():
            if opts['replace']:
                LandZone.objects.all().delete()
            LandZone.objects.bulk_create(zones, batch_size=100)
        counts = {}
        for z in zones:
            counts[z.category] = counts.get(z.category, 0) + 1
        self.stdout.write(self.style.SUCCESS(f'{len(zones)} zone(s) importée(s) : {counts} ; {skipped} ignorée(s).'))
