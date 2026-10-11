"""
Contrôles spatiaux d'une coopérative : chevauchements de parcelles et zones foncières (forêts classées, parcs…).

Avec PostGIS (production, migration 0006) : calcul en base sur l'index spatial GIST, surfaces géodésiques exactes.
Sans PostGIS (SQLite en local) : même résultat calculé en Python avec Shapely (surfaces approchées).

Les deux sources de polygones sont contrôlées ensemble : parcelles mappées par les agents (« parcel ») et
anciens polygones importés (« legacy »). Les chevauchements avec les parcelles des AUTRES coopératives sont
signalés aussi (un même champ déclaré par deux coopératives), sans révéler leurs données à un non-propriétaire.
"""
import json
import math

from django.db import connection

SOURCES = {
    'parcel': ('parcels_parcel', 'a.field_id'),
    'legacy': ('parcels_legacyparcel', "COALESCE(NULLIF(a.code, ''), NULLIF(a.name, ''), LEFT(a.id::text, 8))"),
}
DUPLICATE_PCT = 80.0       # au-delà : probablement le même champ relevé deux fois
MAX_RESULTS = 3000
OUTSIDE_TOLERANCE_PCT = 1.0  # part d'une parcelle d'enclave débordant dans la forêt classée, tolérée (imprécision GPS)

_ready = None


def postgis_ready():
    """PostGIS actif et colonnes `geom` en place (vérifié une fois par processus)."""
    global _ready
    if _ready is None:
        _ready = False
        if connection.vendor == 'postgresql':
            with connection.cursor() as c:
                c.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'parcels_parcel' AND column_name = 'geom'")
                _ready = c.fetchone() is not None
    return _ready


# ─── Chevauchements ─────────────────────────────────────────────────────────

def _overlap_sql(ka, kb, include_other):
    ta, la = SOURCES[ka]
    tb, lb = SOURCES[kb]
    lb = lb.replace('a.', 'b.')
    if ka == kb:
        order = 'a.id < b.id'
    else:
        order = 'TRUE' if ka == 'parcel' else 'FALSE'
    other = 'OR b.cooperative_id <> %(coop)s' if include_other else ''
    return f"""
      SELECT '{ka}', a.id::text, {la}, a.producer_id::text,
             '{kb}', b.id::text, {lb}, b.producer_id::text, b.cooperative_id::text,
             ST_Area(ST_Intersection(a.geom, b.geom)::geography) / 10000.0,
             ST_Area(a.geom::geography) / 10000.0, ST_Area(b.geom::geography) / 10000.0,
             ST_AsGeoJSON(ST_CollectionExtract(ST_Intersection(a.geom, b.geom), 3), 6)
        FROM {ta} a JOIN {tb} b ON a.geom && b.geom AND ST_Intersects(a.geom, b.geom) AND NOT ST_Touches(a.geom, b.geom)
       WHERE a.cooperative_id = %(coop)s AND ST_Dimension(a.geom) = 2 AND ST_Dimension(b.geom) = 2
         AND ((b.cooperative_id = %(coop)s AND {order}) {other})"""


def _overlaps_postgis(coop_id, include_other, min_ha):
    parts = [_overlap_sql(ka, kb, include_other) for ka in SOURCES for kb in SOURCES]
    sql = ('SELECT * FROM (' + ' UNION ALL '.join(parts) + ') AS o(ka, ida, la, pa, kb, idb, lb, pb, cb, ha, area_a, area_b, geom) '
           'WHERE o.ha >= %(min_ha)s ORDER BY o.ha DESC LIMIT %(limit)s')
    with connection.cursor() as c:
        c.execute(sql, {'coop': str(coop_id), 'min_ha': min_ha, 'limit': MAX_RESULTS})
        return c.fetchall()


def _area_ha(g):
    """Surface approchée (ha) d'une géométrie en degrés : échelle locale à la latitude du centre."""
    if g.is_empty:
        return 0.0
    lat = math.radians(g.centroid.y)
    return g.area * (111319.49 ** 2) * math.cos(lat) / 10000.0


def _load(qs, kind):
    import shapely
    from shapely.geometry import shape
    out = []
    for row in qs:
        try:
            g = shape(row['geometry'].get('geometry', row['geometry']) if row['geometry'].get('type') == 'Feature' else row['geometry'])
        except Exception:  # noqa: BLE001 — géométrie illisible : ignorée
            continue
        if not g.is_valid:
            g = shapely.make_valid(g)
        if g.is_empty or g.geom_type not in ('Polygon', 'MultiPolygon', 'GeometryCollection'):
            continue
        label = row.get('field_id') or row.get('code') or row.get('name') or str(row['id'])[:8]
        out.append((kind, str(row['id']), label, str(row['producer_id']) if row['producer_id'] else None, str(row['cooperative_id']), g))
    return out


def _overlaps_shapely(coop_id, include_other, min_ha):
    import shapely
    from shapely.strtree import STRtree
    from .models import LegacyParcel, Parcel
    flt = {} if include_other else {'cooperative_id': coop_id}
    items = (_load(Parcel.objects.filter(**flt).values('id', 'field_id', 'producer_id', 'cooperative_id', 'geometry'), 'parcel')
             + _load(LegacyParcel.objects.filter(**flt).values('id', 'code', 'name', 'producer_id', 'cooperative_id', 'geometry'), 'legacy'))
    if not items:
        return []
    geoms = [it[5] for it in items]
    tree = STRtree(geoms)
    mine = [i for i, it in enumerate(items) if it[4] == str(coop_id)]
    left, right = tree.query([geoms[i] for i in mine], predicate='intersects')
    rows, order = [], {'parcel': 0, 'legacy': 1}
    for li, j in zip(left.tolist(), right.tolist()):
        i = mine[li]
        if i == j:
            continue
        a, b = items[i], items[j]
        if b[4] == a[4] and (order[a[0]], a[1]) >= (order[b[0]], b[1]):
            continue  # paire déjà vue dans l'autre sens
        inter = shapely.intersection(a[5], b[5])
        ha = _area_ha(inter)
        if ha < min_ha:
            continue
        rows.append((a[0], a[1], a[2], a[3], b[0], b[1], b[2], b[3], b[4], ha, _area_ha(a[5]), _area_ha(b[5]),
                     json.dumps(shapely.geometry.mapping(inter))))
    rows.sort(key=lambda r: -r[9])
    return rows[:MAX_RESULTS]


def overlaps(cooperative, include_other=True, min_ha=0.01, reveal_other=False):
    """Chevauchements de la coopérative (triés par surface décroissante)."""
    from apps.cooperatives.models import Cooperative
    from apps.producers.models import Producer
    engine = 'postgis' if postgis_ready() else 'shapely'
    rows = (_overlaps_postgis if engine == 'postgis' else _overlaps_shapely)(cooperative.id, include_other, min_ha)

    pids = {r[3] for r in rows if r[3]} | {r[7] for r in rows if r[7] and r[8] == str(cooperative.id)}
    producers = {str(p['id']): p for p in Producer.objects.filter(id__in=pids).values('id', 'field_id_base', 'last_name', 'first_name')}
    coops = dict(Cooperative.objects.filter(id__in={r[8] for r in rows}).values_list('id', 'name')) if reveal_other else {}
    coops = {str(k): v for k, v in coops.items()}

    def prod(pid):
        p = producers.get(pid or '')
        return {'id': pid, 'code': p['field_id_base'], 'name': f"{p['last_name']} {p['first_name']}".strip()} if p else None

    out = []
    for ka, ida, la, pa, kb, idb, lb, pb, cb, ha, area_a, area_b, geom in rows:
        same_coop = cb == str(cooperative.id)
        pct_a = 100.0 * ha / area_a if area_a else 0.0
        pct_b = 100.0 * ha / area_b if area_b else 0.0
        if not same_coop:
            kind = 'autre_cooperative'
        elif pa and pa == pb:
            kind = 'meme_producteur'
        else:
            kind = 'autres_producteurs'
        out.append({
            'type': kind,
            'doublon': max(pct_a, pct_b) >= DUPLICATE_PCT,
            'area_ha': round(ha, 4), 'pct_a': round(pct_a, 1), 'pct_b': round(pct_b, 1),
            'a': {'kind': ka, 'id': ida, 'label': la, 'area_ha': round(area_a, 4), 'producer': prod(pa)},
            'b': ({'kind': kb, 'id': idb, 'label': lb, 'area_ha': round(area_b, 4), 'producer': prod(pb)} if same_coop or reveal_other
                  else {'kind': kb, 'id': None, 'label': 'Parcelle d’une autre coopérative', 'area_ha': round(area_b, 4), 'producer': None}),
            'other_cooperative': (coops.get(cb, 'Autre coopérative') if reveal_other else 'Autre coopérative') if not same_coop else None,
            'geometry': json.loads(geom) if geom else None,
        })
    summary = {
        'engine': engine, 'total': len(out),
        'meme_producteur': sum(o['type'] == 'meme_producteur' for o in out),
        'autres_producteurs': sum(o['type'] == 'autres_producteurs' for o in out),
        'autre_cooperative': sum(o['type'] == 'autre_cooperative' for o in out),
        'doublons': sum(o['doublon'] for o in out),
        'area_ha': round(sum(o['area_ha'] for o in out), 2),
        'truncated': len(out) >= MAX_RESULTS,
    }
    return summary, out


# ─── Zones foncières (forêts classées, parcs / réserves, agro-forêts, enclaves) ─────────────────────

def _zones_postgis(coop_id):
    parts = []
    for k, (t, label) in SOURCES.items():
        parts.append(f"""
          SELECT '{k}', a.id::text, {label}, a.producer_id::text, z.category, z.name,
                 CASE WHEN ST_Dimension(a.geom) = 2 THEN ST_Area(ST_Intersection(a.geom, z.geom)::geography) / 10000.0 END,
                 CASE WHEN ST_Dimension(a.geom) = 2 THEN ST_Area(a.geom::geography) / 10000.0 END
            FROM {t} a JOIN parcels_landzone z ON a.geom && z.geom AND ST_Intersects(a.geom, z.geom)
           WHERE a.cooperative_id = %(coop)s""")
    with connection.cursor() as c:
        c.execute(' UNION ALL '.join(parts), {'coop': str(coop_id)})
        return c.fetchall()


def _zones_shapely(coop_id):
    import shapely
    from shapely.strtree import STRtree
    from shapely.geometry import shape
    from .models import LandZone, LegacyParcel, Parcel
    zones = []
    for cat, name, g in LandZone.objects.values_list('category', 'name', 'geometry'):
        try:
            zg = shape(g)
            zones.append((cat, name, zg if zg.is_valid else shapely.make_valid(zg)))
        except Exception:  # noqa: BLE001
            continue
    if not zones:
        return []
    tree = STRtree([z[2] for z in zones])
    items = (_load(Parcel.objects.filter(cooperative_id=coop_id).values('id', 'field_id', 'producer_id', 'cooperative_id', 'geometry'), 'parcel')
             + _load(LegacyParcel.objects.filter(cooperative_id=coop_id).values('id', 'code', 'name', 'producer_id', 'cooperative_id', 'geometry'), 'legacy'))
    rows = []
    for kind, pid, label, prod, _, g in items:
        for j in tree.query(g, predicate='intersects').tolist():
            cat, name, zg = zones[j]
            rows.append((kind, pid, label, prod, cat, name, _area_ha(shapely.intersection(g, zg)), _area_ha(g)))
    return rows


def zones(cooperative):
    """Parcelles de la coopérative situées (même en partie) dans une zone foncière de référence."""
    from apps.producers.models import Producer
    from . import rdue
    from .models import LandZone
    engine = 'postgis' if postgis_ready() else 'shapely'
    zone_count = LandZone.objects.count()
    rows = (_zones_postgis if engine == 'postgis' else _zones_shapely)(cooperative.id) if zone_count else []

    by_parcel = {}
    for kind, pid, label, prod, cat, name, inter, area in rows:
        e = by_parcel.setdefault((kind, pid), {'kind': kind, 'id': pid, 'label': label, 'producer_id': prod,
                                               'area_ha': round(area, 4) if area else None, 'zones': []})
        pct = 100.0 * inter / area if (area and inter is not None) else 100.0
        e['zones'].append({'category': cat, 'name': name, 'pct': round(pct, 1), 'area_ha': round(inter, 4) if inter is not None else None})

    pids = {e['producer_id'] for e in by_parcel.values() if e['producer_id']}
    producers = {str(p['id']): p for p in Producer.objects.filter(id__in=pids).values('id', 'field_id_base', 'last_name', 'first_name')}
    priority = {c: i for i, c in enumerate(rdue.CATEGORY_PRIORITY)}
    out = []
    for e in by_parcel.values():
        e['zones'].sort(key=lambda z: (priority.get(z['category'], 99), -z['pct']))
        main = e['zones'][0]
        e['category'] = main['category']
        e['zone'] = main['name']
        e['pct'] = max(z['pct'] for z in e['zones'] if z['category'] == main['category'])
        e['legalite'] = rdue.CATEGORY_LEGALITY.get(main['category'], rdue.LEGAL_CHECK)
        # Une enclave (ou agro-forêt) ne couvre souvent qu'une partie de la parcelle : la part restée
        # dans la forêt classée / le parc l'emporte dès qu'elle dépasse la tolérance.
        illegal = [z for z in e['zones'] if rdue.CATEGORY_LEGALITY.get(z['category']) == rdue.LEGAL_NO]
        if illegal and e['legalite'] != rdue.LEGAL_NO:
            covered = min(100.0, sum(z['pct'] for z in e['zones'] if rdue.CATEGORY_LEGALITY.get(z['category']) == rdue.LEGAL_OK))
            worst = max(illegal, key=lambda z: z['pct'])
            exposed = max(0.0, worst['pct'] - covered)
            if exposed > OUTSIDE_TOLERANCE_PCT:
                e.update(category=worst['category'], zone=worst['name'], pct=round(exposed, 1), legalite=rdue.LEGAL_NO,
                         note=f"{round(exposed, 1)} % de la parcelle hors de l'enclave « {main['name']} »")
        p = producers.get(e.pop('producer_id') or '')
        e['producer'] = {'code': p['field_id_base'], 'name': f"{p['last_name']} {p['first_name']}".strip()} if p else None
        out.append(e)
    out.sort(key=lambda e: (e['legalite'] != rdue.LEGAL_NO, -e['pct']))
    cats = {}
    for e in out:
        cats[e['category']] = cats.get(e['category'], 0) + 1
    summary = {'engine': engine, 'zones_loaded': zone_count, 'parcels_in_zones': len(out),
               'illegal': sum(e['legalite'] == rdue.LEGAL_NO for e in out), 'by_category': cats}
    return summary, out
