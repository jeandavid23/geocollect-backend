"""
PostGIS : colonne géométrique `geom` (EPSG:4326) + index spatial GIST sur les parcelles, les anciens polygones
et les zones foncières. La colonne est tenue à jour par un déclencheur à partir du champ JSON `geometry` :
le code Django n'a rien à changer. Sans PostgreSQL ou sans PostGIS (SQLite en local, extension refusée),
la migration ne fait rien et l'application calcule en Python (Shapely).
"""
import logging

from django.db import migrations, transaction

log = logging.getLogger(__name__)

TABLES = ['parcels_parcel', 'parcels_legacyparcel', 'parcels_landzone']

FUNCTIONS = """
CREATE OR REPLACE FUNCTION geocollect_geom_from_json(j jsonb) RETURNS geometry
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE g geometry;
BEGIN
  IF j IS NULL THEN RETURN NULL; END IF;
  IF j->>'type' = 'Feature' THEN j := j->'geometry'; END IF;
  IF j IS NULL OR j->>'type' IS NULL THEN RETURN NULL; END IF;
  g := ST_SetSRID(ST_GeomFromGeoJSON(j::text), 4326);
  IF NOT ST_IsValid(g) THEN g := ST_MakeValid(g); END IF;
  IF GeometryType(g) = 'GEOMETRYCOLLECTION' THEN g := ST_CollectionExtract(g, 3); END IF;
  IF ST_IsEmpty(g) THEN RETURN NULL; END IF;
  RETURN g;
EXCEPTION WHEN others THEN
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION geocollect_sync_geom() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.geometry IS DISTINCT FROM OLD.geometry THEN
    NEW.geom := geocollect_geom_from_json(NEW.geometry::jsonb);
  END IF;
  RETURN NEW;
END $$;
"""


def forward(apps, schema_editor):
    conn = schema_editor.connection
    if conn.vendor != 'postgresql':
        return
    try:
        with transaction.atomic(using=conn.alias):
            with conn.cursor() as c:
                c.execute('CREATE EXTENSION IF NOT EXISTS postgis')
    except Exception as exc:  # noqa: BLE001 — base sans PostGIS : calculs en Python
        log.warning('PostGIS indisponible (%s) : contrôles spatiaux en Python.', exc)
        return
    with conn.cursor() as c:
        c.execute(FUNCTIONS)
        for t in TABLES:
            c.execute(f'ALTER TABLE {t} ADD COLUMN IF NOT EXISTS geom geometry(Geometry, 4326)')
            c.execute(f'CREATE INDEX IF NOT EXISTS {t}_geom_gist ON {t} USING GIST (geom)')
            c.execute(f'DROP TRIGGER IF EXISTS {t}_geom_sync ON {t}')
            c.execute(f'CREATE TRIGGER {t}_geom_sync BEFORE INSERT OR UPDATE ON {t} '
                      f'FOR EACH ROW EXECUTE FUNCTION geocollect_sync_geom()')
            c.execute(f'UPDATE {t} SET geom = geocollect_geom_from_json(geometry::jsonb)')
            c.execute(f'ANALYZE {t}')


def backward(apps, schema_editor):
    conn = schema_editor.connection
    if conn.vendor != 'postgresql':
        return
    with conn.cursor() as c:
        c.execute("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")
        if not c.fetchone():
            return
        for t in TABLES:
            c.execute(f'DROP TRIGGER IF EXISTS {t}_geom_sync ON {t}')
            c.execute(f'ALTER TABLE {t} DROP COLUMN IF EXISTS geom')
        c.execute('DROP FUNCTION IF EXISTS geocollect_sync_geom()')
        c.execute('DROP FUNCTION IF EXISTS geocollect_geom_from_json(jsonb)')


class Migration(migrations.Migration):
    dependencies = [('parcels', '0005_legacyparcel_code_legacyparcel_match_key_and_more')]
    operations = [migrations.RunPython(forward, backward)]
