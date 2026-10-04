"""
Self-intersection — port web de l'outil « Self-intersection » du plugin QGIS « Polygon validator EUDR 2 »
(si_hole_engine.py), avec shapely / GEOS (le moteur géométrique de QGIS).

Pipeline (dans l'ordre du plugin) :
  1. inventaire des erreurs d'origine (auto-intersection, anneau, sommets dupliqués…) ;
  2. réparation : make_valid -> parties polygonales -> sommets dupliqués retirés -> tampon 0 ;
  3. comblement des trous intérieurs (tous, ou seulement ceux <= N ha) ;
  4. éclatement des multi-polygones en polygones simples ;
  5. arrondi des bouts + retrait des pics / longs traits fins (ouverture morphologique à jointure ronde :
     tampon(-r) puis tampon(+r), on garde la plus grande partie — le polygone n'est jamais scindé) ;
  6. calcul de Surface_Ha et suppression des polygones de 0 à N ha (borne haute exclue, 0,25 ha par défaut) ;
  7. déduplication par code (Field_ID) : on garde la plus grande Surface_Ha.

Différence assumée avec le plugin : l'arrondi est calculé dans un repère local en mètres
(longitudes corrigées de cos(latitude)) au lieu d'une simple conversion degrés ≈ mètres / 111 320.
"""
import math
import time
from collections import defaultdict

import numpy as np
import shapely
from shapely.geometry import mapping, shape
from shapely.validation import explain_validity

from .deforestation import geodesic_area_ha

M_PER_DEG = 111_000.0
ROUND_SEGMENTS = 12     # segments par quart de cercle, comme le plugin


class Options:
    def __init__(self, data=None):
        data = data or {}

        def num(key, default):
            try:
                return float(data.get(key, default))
            except (TypeError, ValueError):
                return default

        def flag(key, default=True):
            v = data.get(key, default)
            return v if isinstance(v, bool) else str(v).lower() in ('1', 'true', 'oui', 'yes')

        self.fix_geometries = flag('fix_geometries')
        self.multipart_to_single = flag('multipart_to_single')
        self.fill_holes = flag('fill_holes')
        self.fill_holes_max_ha = max(0.0, num('fill_holes_max_ha', 0.0))     # 0 = tous les trous
        self.round_corners = flag('round_corners')
        self.round_radius_m = max(0.0, num('round_radius_m', 2.0))
        self.min_area_ha = max(0.0, num('min_area_ha', 0.25))
        self.remove_dup_code = flag('remove_dup_code')
        self.id_field = str(data.get('id_field') or '').strip()


# ─── Briques géométriques ─────────────────────────────────────────────────────

def _error_kind(message):
    m = (message or '').lower()
    if 'ring self-intersection' in m or ('ring' in m and 'self' in m):
        return 'ring_self_intersection'
    if 'self-intersection' in m or 'self intersection' in m:
        return 'self_intersection'
    if 'duplicate' in m or 'repeated' in m:
        return 'duplicate_vertices'
    if 'hole' in m or 'interior' in m or 'nested' in m:
        return 'polygon_hole_error'
    if 'collection' in m:
        return 'geometry_collection_error'
    if 'few points' in m:
        return 'too_few_points'
    return 'invalid_geometry'


def _polygon_parts(geom):
    """Parties polygonales simples d'une géométrie (liste éventuellement vide)."""
    if geom is None or geom.is_empty:
        return []
    t = geom.geom_type
    if t == 'Polygon':
        return [geom]
    if t in ('MultiPolygon', 'GeometryCollection'):
        out = []
        for g in geom.geoms:
            out.extend(_polygon_parts(g))
        return out
    return []


def _coerce_to_polygon(geom):
    parts = _polygon_parts(geom)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    try:
        merged = shapely.union_all(parts)
        if not merged.is_empty and merged.geom_type in ('Polygon', 'MultiPolygon'):
            return merged
    except Exception:  # noqa: BLE001
        pass
    return shapely.MultiPolygon(parts)


def validation_errors(geom):
    """Erreurs de validité + sommets dupliqués (comme geom_utils.validation_errors du plugin)."""
    errors = []
    if geom is None or geom.is_empty:
        return errors
    if not geom.is_valid:
        errors.append(_error_kind(explain_validity(geom)))
    try:
        if shapely.get_num_coordinates(shapely.remove_repeated_points(geom)) < shapely.get_num_coordinates(geom):
            errors.append('duplicate_vertices')
    except Exception:  # noqa: BLE001
        pass
    return errors


def repair_polygon(geom):
    """make_valid -> parties polygonales -> sommets dupliqués -> tampon 0 (forme préservée)."""
    if geom is None or geom.is_empty:
        return None
    g = geom
    try:
        mv = shapely.make_valid(g)
        if not mv.is_empty:
            g = mv
    except Exception:  # noqa: BLE001
        pass
    g = _coerce_to_polygon(g)
    if g is None:
        return None
    try:
        g = shapely.remove_repeated_points(g)
    except Exception:  # noqa: BLE001
        pass
    try:
        b = _coerce_to_polygon(g.buffer(0, quad_segs=1))
        if b is not None and b.is_valid:
            g = b
    except Exception:  # noqa: BLE001
        pass
    return None if g is None or g.is_empty else g


def fill_holes(geom, max_area_ha=0.0):
    """Comble les trous intérieurs (tous, ou seulement ceux <= max_area_ha). Renvoie (géométrie, nb de trous comblés)."""
    filled, n = [], 0
    for part in _polygon_parts(geom):
        keep = []
        for ring in part.interiors:
            if max_area_ha > 0:
                hole = shapely.Polygon(ring)
                if geodesic_area_ha(mapping(hole)) > max_area_ha:
                    keep.append(ring)
                    continue
            n += 1
        filled.append(shapely.Polygon(part.exterior, keep))
    if n == 0:
        return geom, 0
    if len(filled) == 1:
        return filled[0], n
    try:
        return _coerce_to_polygon(shapely.union_all(filled)), n
    except Exception:  # noqa: BLE001
        return shapely.MultiPolygon(filled), n


def _largest_part(geom):
    parts = _polygon_parts(geom)
    return max(parts, key=lambda p: p.area) if parts else geom


def round_and_despike(poly, radius_m):
    """
    Ouverture morphologique à jointure ronde, dans un repère local en mètres : tampon(-r) puis tampon(+r).
    Supprime les pics et traits plus fins que 2r, arrondit les angles, garde la plus grande partie.
    Renvoie toujours une géométrie non vide (repli sur l'entrée).
    """
    if radius_m <= 0 or poly is None or poly.is_empty:
        return poly
    lat = poly.representative_point().y
    k = max(math.cos(math.radians(lat)), 1e-6)
    to_local = np.array([k * M_PER_DEG, M_PER_DEG])
    try:
        local = shapely.transform(poly, lambda c: c * to_local)
        eroded = local.buffer(-radius_m, quad_segs=ROUND_SEGMENTS)
        if eroded.is_empty:
            # rayon trop grand pour ce polygone : simple fermeture douce, sans érosion
            out = local.buffer(radius_m, quad_segs=ROUND_SEGMENTS).buffer(-radius_m, quad_segs=ROUND_SEGMENTS)
        else:
            out = eroded.buffer(radius_m, quad_segs=ROUND_SEGMENTS)
        out = _coerce_to_polygon(out)
        if out is None or out.is_empty:
            return poly
        out = _largest_part(out)
        back = shapely.transform(out, lambda c: c / to_local)
        return back if not back.is_empty else poly
    except Exception:  # noqa: BLE001
        return poly


def _rounded(geom, decimals=7):
    """Coordonnées arrondies à 7 décimales (~1 cm) pour alléger la réponse."""
    return shapely.transform(geom, lambda c: np.round(c, decimals))


# ─── Orchestration ────────────────────────────────────────────────────────────

def run(features, options=None):
    """
    features : [{'id': str, 'geometry': GeoJSON, 'properties': dict}] en WGS84.
    Retourne {'summary': {...}, 'results': [...]} : un résultat par polygone produit (un polygone d'origine
    multi-parties peut en donner plusieurs), statut 'kept' | 'deleted' | 'dup_code'.
    """
    t0 = time.time()
    o = options or Options()
    topology = defaultdict(int)
    counts = defaultdict(int)
    singles = []      # morceaux après réparation / éclatement
    results = []

    # ── Étapes 1 à 5 ──
    for i, f in enumerate(features):
        fid = str(f.get('id') or f'#{i + 1}')
        props = f.get('properties') if isinstance(f.get('properties'), dict) else {}
        try:
            src = shape(f['geometry']) if f.get('geometry') else None
        except Exception:  # noqa: BLE001
            src = None
        if src is None or src.is_empty:
            results.append({'index': i, 'part': 0, 'id': fid, 'status': 'deleted', 'reason': 'geometrie_nulle',
                            'area_ha': 0.0, 'errors': [], 'corrected': False, 'holes_filled': 0, 'rounded': False})
            counts['null'] += 1
            continue

        errors = validation_errors(src)
        for e in errors:
            topology[e] += 1

        corrected = False
        geom = src
        if o.fix_geometries and errors:
            geom = repair_polygon(src)
            corrected = True
            counts['fixed'] += 1
        else:
            geom = _coerce_to_polygon(src)
        if geom is None or geom.is_empty:
            results.append({'index': i, 'part': 0, 'id': fid, 'status': 'deleted', 'reason': 'geometrie_irreparable',
                            'area_ha': 0.0, 'errors': errors, 'corrected': corrected, 'holes_filled': 0,
                            'rounded': False, 'geometry': f['geometry']})
            counts['null'] += 1
            continue

        holes = 0
        if o.fill_holes:
            geom, holes = fill_holes(geom, o.fill_holes_max_ha)
            if holes:
                counts['holes'] += 1

        parts = _polygon_parts(geom)
        rounded_any = False
        if o.round_corners and o.round_radius_m > 0:
            new_parts = [round_and_despike(p, o.round_radius_m) for p in parts]
            rounded_any = any(n is not p for n, p in zip(new_parts, parts))
            parts = new_parts
            if rounded_any:
                counts['rounded'] += 1
        if not o.multipart_to_single and len(parts) > 1:
            parts = [shapely.MultiPolygon(parts)]

        for j, part in enumerate(parts):
            if part is None or part.is_empty:
                continue
            singles.append({'index': i, 'part': j, 'id': fid, 'props': props, 'geom': part, 'errors': errors,
                            'corrected': corrected, 'holes_filled': holes, 'rounded': rounded_any})

    after_split = len(singles)

    # ── Étape 6 : Surface_Ha et petites surfaces ──
    kept = []
    initial_area = 0.0
    small_reason = f'surface_inferieure_{o.min_area_ha:g}ha'
    for s in singles:
        gj = mapping(s['geom'])
        a = geodesic_area_ha(gj)
        initial_area += a
        s['area_ha'] = a
        if 0.0 <= a < o.min_area_ha:
            s['status'], s['reason'] = 'deleted', small_reason
            counts['small'] += 1
        else:
            s['status'], s['reason'] = 'kept', ''
            kept.append(s)

    # ── Étape 7 : déduplication par code ──
    if o.remove_dup_code and o.id_field:
        groups = defaultdict(list)
        for s in kept:
            code = s['props'].get(o.id_field)
            code = '' if code is None else str(code).strip()
            groups[code].append(s)
        for code, recs in groups.items():
            if code == '' or len(recs) <= 1:
                continue
            for s in sorted(recs, key=lambda r: -r['area_ha'])[1:]:
                s['status'], s['reason'], s['ref_id'] = 'dup_code', 'doublon_field_id', code
                counts['dup'] += 1

    final_area = 0.0
    for s in singles:
        if s['status'] == 'kept':
            final_area += s['area_ha']
        results.append({
            'index': s['index'], 'part': s['part'], 'id': s['id'], 'status': s['status'], 'reason': s['reason'],
            'ref_id': s.get('ref_id', ''), 'area_ha': round(s['area_ha'], 4), 'errors': s['errors'],
            'corrected': s['corrected'], 'holes_filled': s['holes_filled'], 'rounded': s['rounded'],
            'geometry': mapping(_rounded(s['geom'])),
        })
    results.sort(key=lambda r: (r['index'], r['part']))

    n_kept = sum(1 for r in results if r['status'] == 'kept')
    summary = {
        'initial_count': len(features),
        'after_split_count': after_split,
        'final_count': n_kept,
        'deleted_count': sum(1 for r in results if r['status'] == 'deleted'),
        'small_removed': counts['small'],
        'null_removed': counts['null'],
        'dup_code_removed': counts['dup'],
        'geometries_fixed': counts['fixed'],
        'holes_filled': counts['holes'],
        'rounded_count': counts['rounded'],
        'initial_area_ha': round(initial_area, 4),
        'final_area_ha': round(final_area, 4),
        'topology_errors': dict(topology),
        'min_area_ha': o.min_area_ha,
        'id_field': o.id_field,
        'processing_time': round(time.time() - t0, 2),
    }
    return {'summary': summary, 'results': results}
