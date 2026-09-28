"""
Polygon Validator — port web du moteur « Validator » du plugin QGIS « Polygon validator EUDR 2 »
(cleaner_engine.py), avec shapely / GEOS (le même moteur géométrique que QGIS).

Étapes (identiques au plugin) :
  1. correction réelle des géométries invalides (make_valid -> parties polygonales -> tampon 0),
     inventaire des erreurs d'origine (auto-intersection, anneau, trous…) ;
  2. tri par priorité de CONSERVATION : la plus valide, puis la plus grande, puis la plus ancienne ;
  3. suppressions : géométrie nulle/irréparable, surface < minimum, sliver, doublon exact,
     inclusion totale (>= 99 %), superposition > seuil (le moins prioritaire perd) ;
  4. annotation des superpositions restantes (<= seuil) : identifiants voisins + taux max.

Taux de superposition = aire commune / plus petite des deux surfaces (règle du plugin),
suppression si taux STRICTEMENT supérieur au seuil (18 % par défaut).

Conçu pour des dizaines de milliers de polygones : index spatial STRtree, calculs vectorisés.
"""
import math
import time
from collections import defaultdict

import numpy as np
import shapely
from shapely.geometry import shape, mapping
from shapely.validation import explain_validity

PCT_EPS = 1e-9
M2_PER_DEG2 = 111320.0 * 110574.0   # m² d'un degré² à l'équateur (corrigé par cos(latitude))


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

        self.threshold = num('threshold', 18.0)              # % de superposition
        self.min_area_ha = num('min_area_ha', 0.01)
        self.remove_exact_duplicates = flag('remove_exact_duplicates')
        self.remove_full_containment = flag('remove_full_containment')
        self.remove_over_threshold = flag('remove_over_threshold')
        self.remove_small = flag('remove_small')
        self.remove_slivers = flag('remove_slivers')
        self.fix_geometries = flag('fix_geometries')
        self.detect_overlaps = flag('detect_overlaps')
        self.sliver_thinness = num('sliver_thinness', 0.03)
        self.sliver_max_area_ha = num('sliver_max_area_ha', 0.05)
        self.containment_ratio = num('containment_ratio', 99.0)


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
    if 'few points' in m:
        return 'too_few_points'
    return 'invalid_geometry'


def _polygonal(geom):
    """Ne garde que les parties (multi)polygonales d'une géométrie."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in ('Polygon', 'MultiPolygon'):
        return geom
    if geom.geom_type == 'GeometryCollection':
        parts = []
        for g in geom.geoms:
            p = _polygonal(g)
            if p is not None:
                parts.extend(p.geoms if p.geom_type == 'MultiPolygon' else [p])
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else shapely.MultiPolygon(parts)
    return None


def _repair(geom):
    """Correction qui préserve la forme : make_valid, sinon tampon 0."""
    try:
        g = _polygonal(shapely.make_valid(geom))
        if g is not None and g.is_valid and not g.is_empty:
            return g
    except Exception:  # noqa: BLE001
        pass
    try:
        g = _polygonal(geom.buffer(0))
        if g is not None and not g.is_empty:
            return g
    except Exception:  # noqa: BLE001
        pass
    return None


def _safe_intersection_area(a, b):
    try:
        return a.intersection(b).area
    except Exception:  # noqa: BLE001 — TopologyException GEOS
        try:
            return a.buffer(0).intersection(b.buffer(0)).area
        except Exception:  # noqa: BLE001
            return 0.0


def _overlap_pct(inter, area_a, area_b):
    smaller = min(area_a, area_b)
    if smaller <= 0 or inter <= 0:
        return 0.0
    return inter / smaller * 100.0


def _thinness(geom, lat):
    """Compacité 4πA/P² calculée en mètres (0 = très fin, 1 = disque)."""
    try:
        k = math.cos(math.radians(lat))
        g = shapely.transform(geom, lambda c: c * np.array([k, 1.0]))
        perim = g.length
        return (4.0 * math.pi * g.area) / (perim * perim) if perim > 0 else 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def run(features, options=None):
    """
    features : [{'id': str, 'geometry': GeoJSON}]  (WGS84)
    Retourne {'summary': {...}, 'results': [...]} ; results alignés sur features.
    Les géométries ne sont renvoyées que si elles ont été corrigées.
    """
    t0 = time.time()
    o = options or Options()
    n = len(features)
    ids = [str(f.get('id') or f'#{i + 1}') for i, f in enumerate(features)]

    geoms = [None] * n            # géométrie corrigée (ou d'origine si valide)
    area = np.zeros(n)            # aire en degrés² (ratios)
    area_ha = np.zeros(n)
    lat = np.zeros(n)
    err_count = np.zeros(n, dtype=int)
    results = [None] * n
    topology = defaultdict(int)
    fixed_count = 0
    null_count = 0

    # ── Étape 1 : lecture, inventaire des erreurs, correction ────────────────
    for i, f in enumerate(features):
        res = {'index': i, 'id': ids[i], 'status': 'kept', 'reason': '', 'ref_id': '',
               'corrected': False, 'errors': [], 'area_ha': 0.0, 'vertices': 0,
               'overlap_ids': '', 'overlap_pct': 0.0, 'is_ovlp': 'NON'}
        results[i] = res
        try:
            g = shape(f.get('geometry')) if f.get('geometry') else None
        except Exception:  # noqa: BLE001
            g = None
        if g is None or g.is_empty:
            err_count[i] = 9999
            res['errors'].append('geometrie_nulle')
            continue
        try:
            res['vertices'] = int(shapely.get_num_coordinates(g))
        except Exception:  # noqa: BLE001
            pass
        if not g.is_valid:
            kind = _error_kind(explain_validity(g))
            res['errors'].append(kind)
            topology[kind] += 1
            if o.fix_geometries:
                fixed = _repair(g)
                res['corrected'] = True
                fixed_count += 1
                if fixed is None:
                    err_count[i] = 9999
                    continue
                g = fixed
                err_count[i] = 0 if g.is_valid else 1
            else:
                err_count[i] = 1
        g = _polygonal(g)
        if g is None:
            err_count[i] = 9999
            res['errors'].append('pas_de_polygone')
            continue
        geoms[i] = g
        c = g.centroid
        lat[i] = c.y
        area[i] = g.area
        area_ha[i] = g.area * M2_PER_DEG2 * math.cos(math.radians(c.y)) / 10000.0
        res['area_ha'] = round(float(area_ha[i]), 4)
        if res['corrected']:
            res['geometry'] = mapping(g)

    initial_area = float(area_ha.sum())

    # Index spatial sur toutes les géométries exploitables
    valid_idx = [i for i in range(n) if geoms[i] is not None]
    tree = shapely.STRtree([geoms[i] for i in valid_idx]) if valid_idx else None

    def neighbours(i):
        if tree is None:
            return []
        return [valid_idx[j] for j in tree.query(geoms[i]) if valid_idx[j] != i]

    # ── Étape 2 : priorité de conservation ───────────────────────────────────
    order = sorted(range(n), key=lambda i: (err_count[i], -area[i], i))

    # ── Étape 3 : suppressions ───────────────────────────────────────────────
    kept = np.zeros(n, dtype=bool)
    counts = defaultdict(int)

    def remove(i, reason, ref=''):
        results[i]['status'] = 'removed'
        results[i]['reason'] = reason
        results[i]['ref_id'] = ids[ref] if isinstance(ref, (int, np.integer)) else ref
        counts[reason] += 1

    for i in order:
        g = geoms[i]
        if g is None:
            remove(i, 'geometrie_nulle' if 'geometrie_nulle' in results[i]['errors'] else 'geometrie_irreparable')
            null_count += 1
            continue
        if o.remove_small and area_ha[i] < o.min_area_ha:
            remove(i, 'surface_inferieure_seuil')
            continue
        if o.remove_slivers and area_ha[i] <= o.sliver_max_area_ha and _thinness(g, lat[i]) < o.sliver_thinness:
            remove(i, 'sliver')
            topology['sliver_polygon'] += 1
            continue

        removed = False
        for j in neighbours(i):
            if not kept[j]:
                continue
            other = geoms[j]
            if o.remove_exact_duplicates:
                same_area = abs(area[i] - area[j]) <= 1e-9 * max(area[i], area[j], 1e-18)
                try:
                    if same_area and (g.equals(other) or shapely.equals_exact(g, other, 1e-12)):
                        remove(i, 'doublon_exact', j)
                        removed = True
                        break
                except Exception:  # noqa: BLE001
                    pass
            inter = _safe_intersection_area(g, other)
            if inter <= 0:
                continue
            if o.remove_full_containment and area[i] > 0 and inter / area[i] * 100.0 >= o.containment_ratio:
                remove(i, 'inclusion_totale', j)
                removed = True
                break
            rate = _overlap_pct(inter, area[i], area[j])
            if o.remove_over_threshold and rate > o.threshold + PCT_EPS:
                remove(i, 'superposition_sup_seuil', j)
                results[i]['overlap_pct'] = round(rate, 2)
                removed = True
                break
        if not removed:
            kept[i] = True

    # ── Étape 4 : superpositions restantes (<= seuil) entre polygones conservés ─
    overlaps_detected = 0
    if o.detect_overlaps:
        for i in range(n):
            if not kept[i]:
                continue
            ids_near, max_pct = [], 0.0
            for j in neighbours(i):
                if not kept[j]:
                    continue
                inter = _safe_intersection_area(geoms[i], geoms[j])
                if inter <= 0:
                    continue
                pct = _overlap_pct(inter, area[i], area[j])
                ids_near.append(ids[j])
                max_pct = max(max_pct, pct)
            if ids_near:
                results[i]['overlap_ids'] = ' / '.join(ids_near[:20])
                results[i]['overlap_pct'] = round(max_pct, 2)
                results[i]['is_ovlp'] = 'OUI'
                overlaps_detected += 1

    final_area = float(area_ha[kept].sum())
    summary = {
        'initial_count': n,
        'final_count': int(kept.sum()),
        'deleted_count': int(n - kept.sum()),
        'duplicates_removed': counts['doublon_exact'],
        'containment_removed': counts['inclusion_totale'],
        'over_threshold_removed': counts['superposition_sup_seuil'],
        'small_removed': counts['surface_inferieure_seuil'],
        'slivers_removed': counts['sliver'],
        'null_removed': null_count,
        'geometries_fixed': fixed_count,
        'overlaps_detected': overlaps_detected,
        'initial_area_ha': round(initial_area, 4),
        'final_area_ha': round(final_area, 4),
        'topology_errors': dict(topology),
        'threshold': o.threshold,
        'min_area_ha': o.min_area_ha,
        'processing_time': round(time.time() - t0, 2),
    }
    return {'summary': summary, 'results': results}
