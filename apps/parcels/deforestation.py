"""
Analyse de déforestation EUDR / Rainforest Alliance — port web du plugin QGIS « Deforestation check ».

Données : Hansen Global Forest Change (UMD / Google), lues DIRECTEMENT sur le stockage public
de Google (aucun compte Earth Engine) :
  - lossyear      : année de perte de couvert (1..24 = 2001..2024, 0 = pas de perte) ;
  - treecover2000 : couvert arboré en 2000 (%), forêt au sens FAO si >= 10 %.

Les tuiles (40 000 x 40 000 px, 10° x 10°) sont découpées par lignes : on ne télécharge que
les lignes de pixels qui couvrent les parcelles (~13 Ko par ligne pour les deux couches).
Pour traiter des milliers de parcelles, elles sont regroupées par bandes de latitude et chaque
groupe est lu en UNE fenêtre ; le navigateur envoie les parcelles triées du nord au sud.

Règles (identiques au plugin) :
  - perte APRÈS l'année de coupure : EUDR 2020 (pertes 2021+), RA 2013 (pertes 2014+),
    EUDR+RA = la plus stricte (2013) ;
  - seule la perte sur un pixel forestier en 2000 (couvert >= 10 %) compte comme déforestation ;
  - perte <= tolérance (0,01 ha, un bord de pixel) -> Conforme ; <= 1 % de la parcelle -> À risque ;
    au-delà -> Non conforme ;
  - niveau de risque : 0 % Faible, <= 2 % Modéré, <= 10 % Élevé, > 10 % Très élevé ;
  - petites parcelles (moins d'un pixel de 30 m) : pixels touchés, sinon pixel du centroïde.
"""
import math
import threading

import numpy as np
from decouple import config

HANSEN_VERSION = config('HANSEN_VERSION', default='GFC-2024-v1.12')
HANSEN_BASE = f'https://storage.googleapis.com/earthenginepartners-hansen/{HANSEN_VERSION}'
HANSEN_LAST_YEAR = 2000 + int(HANSEN_VERSION.split('-')[1][2:]) if HANSEN_VERSION.startswith('GFC-') else 2024

PIXEL_DEG = 0.00025                 # 1 seconde d'arc = ~30 m
TILE_PX = 40000                     # 10° / 0,00025°
MAX_WINDOW_PX = 30_000_000          # pixels lus en une fenêtre (par couche) : ~30 Mo en uint8
EARTH_RADIUS_M = 6371007.2

EUDR_CUTOFF_YEAR = 2020
RA_CUTOFF_YEAR = 2013
FAO_TREECOVER_MIN = 10
DEFAULT_POINT_RADIUS_M = 100        # point sans superficie : cercle de 100 m (~3,1 ha)

STAT_OK = 'Conforme'
STAT_RISK = 'A risque'
STAT_FAIL = 'Non conforme'
STAT_NODATA = 'Indetermine'

RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_VHIGH = 'Faible', 'Modere', 'Eleve', 'Tres eleve'

GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif',
    GDAL_HTTP_MULTIRANGE='YES',
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES='YES',
    GDAL_HTTP_MAX_RETRY='4',
    GDAL_HTTP_RETRY_DELAY='1',
    GDAL_HTTP_TIMEOUT='60',
    VSI_CACHE='TRUE',
    VSI_CACHE_SIZE=str(64 * 1024 * 1024),
    GDAL_CACHEMAX=96,   # Mo (entier attendu par GDAL)
)


def cutoff_for_standard(standard):
    return RA_CUTOFF_YEAR if standard in ('RA', 'EUDR+RA') else EUDR_CUTOFF_YEAR


def classify(defor_ha, defor_pct, tolerance_ha, alert_pct):
    """Règle du plugin : perte négligeable -> Conforme ; <= alert_pct % -> À risque ; sinon Non conforme."""
    if defor_ha <= tolerance_ha:
        return STAT_OK
    if defor_pct <= alert_pct:
        return STAT_RISK
    return STAT_FAIL


def risk_level_for(defor_pct):
    if defor_pct <= 0.0:
        return RISK_LOW
    if defor_pct <= 2.0:
        return RISK_MEDIUM
    if defor_pct <= 10.0:
        return RISK_HIGH
    return RISK_VHIGH


# ─── Géométrie ────────────────────────────────────────────────────────────────

def _circle(lon, lat, radius_m, n=32):
    """Cercle (polygone) autour d'un point, en degrés."""
    dlat = radius_m / 110574.0
    dlon = radius_m / (111320.0 * max(math.cos(math.radians(lat)), 1e-6))
    ring = [[lon + dlon * math.cos(2 * math.pi * k / n), lat + dlat * math.sin(2 * math.pi * k / n)] for k in range(n)]
    ring.append(ring[0])
    return {'type': 'Polygon', 'coordinates': [ring]}


def _as_area_geometry(geom, area_ha):
    """Polygone à analyser (un point devient un cercle de la superficie déclarée)."""
    gtype = geom.get('type')
    if gtype == 'Point':
        lon, lat = geom['coordinates'][:2]
        radius = math.sqrt(area_ha * 10000.0 / math.pi) if area_ha and area_ha > 0 else DEFAULT_POINT_RADIUS_M
        return _circle(lon, lat, radius)
    if gtype == 'MultiPoint' and geom.get('coordinates'):
        lon, lat = geom['coordinates'][0][:2]
        return _circle(lon, lat, DEFAULT_POINT_RADIUS_M)
    if gtype in ('Polygon', 'MultiPolygon'):
        return geom
    return None


def _coords(geom):
    if geom['type'] == 'Polygon':
        for ring in geom['coordinates']:
            yield from ring
    else:
        for poly in geom['coordinates']:
            for ring in poly:
                yield from ring


def _bounds(geom):
    xs, ys = [], []
    for c in _coords(geom):
        xs.append(c[0]); ys.append(c[1])
    return min(xs), min(ys), max(xs), max(ys)


def _ring_area_m2(ring):
    """Aire géodésique d'un anneau (algorithme de turf / Chamberlain & Duquette)."""
    n = len(ring)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        lo1, la1 = ring[i - 1][0], ring[i - 1][1]
        lo2, la2 = ring[i][0], ring[i][1]
        total += math.radians(lo2 - lo1) * (2 + math.sin(math.radians(la1)) + math.sin(math.radians(la2)))
    return abs(total * EARTH_RADIUS_M ** 2 / 2.0)


def geodesic_area_ha(geom):
    polys = [geom['coordinates']] if geom['type'] == 'Polygon' else geom['coordinates']
    m2 = 0.0
    for poly in polys:
        if not poly:
            continue
        m2 += _ring_area_m2(poly[0]) - sum(_ring_area_m2(h) for h in poly[1:])
    return max(m2, 0.0) / 10000.0


def _tile_id(lon, lat):
    """Tuile Hansen contenant le point : identifiée par son coin nord-ouest (ex. 10N_010W)."""
    top = int(math.floor(lat / 10.0) * 10 + 10)
    left = int(math.floor(lon / 10.0) * 10)
    return (f'{abs(top):02d}{"N" if top >= 0 else "S"}_{abs(left):03d}{"E" if left >= 0 else "W"}', top, left)


def _row_area_ha(top_lat, row0, nrows):
    """Surface (ha) d'un pixel pour chaque ligne (dépend de la latitude)."""
    rows = np.arange(row0, row0 + nrows, dtype=np.float64)
    lat_n = np.radians(top_lat - rows * PIXEL_DEG)
    lat_s = np.radians(top_lat - (rows + 1) * PIXEL_DEG)
    return (EARTH_RADIUS_M ** 2 * math.radians(PIXEL_DEG) * (np.sin(lat_n) - np.sin(lat_s))) / 10000.0


# ─── Lecture des tuiles (réutilisées d'une requête à l'autre) ─────────────────

# Un jeu de fichiers ouverts PAR FIL D'EXÉCUTION : un dataset GDAL ne supporte pas les lectures
# simultanées (plusieurs lots analysés en parallèle faisaient échouer les lectures).
_local = threading.local()


def _datasets():
    if not hasattr(_local, 'datasets'):
        _local.datasets = {}
    return _local.datasets


def _read(layer, tile, row0, col0, nrows, ncols, attempts=3):
    import rasterio
    from rasterio.windows import Window
    cache = _datasets()
    key = (layer, tile)
    last = None
    for _ in range(attempts):
        ds = cache.get(key)
        if ds is None:
            ds = rasterio.open(f'/vsicurl/{HANSEN_BASE}/Hansen_{HANSEN_VERSION}_{layer}_{tile}.tif')
            cache[key] = ds
        try:
            return ds.read(1, window=Window(col0, row0, ncols, nrows))
        except Exception as exc:  # noqa: BLE001 — coupure réseau : on rouvre le fichier et on réessaie
            last = exc
            try:
                ds.close()
            finally:
                cache.pop(key, None)
    raise last


# ─── Analyse ──────────────────────────────────────────────────────────────────

def _groups(items):
    """Regroupe les parcelles d'une tuile (triées par latitude) en fenêtres de taille raisonnable."""
    items.sort(key=lambda it: it['r0'])
    group, g = [], None
    for it in items:
        if g is None:
            g = [it['r0'], it['c0'], it['r1'], it['c1']]
            group = [it]
            continue
        r0, c0 = min(g[0], it['r0']), min(g[1], it['c0'])
        r1, c1 = max(g[2], it['r1']), max(g[3], it['c1'])
        if (r1 - r0) * (c1 - c0) > MAX_WINDOW_PX:
            yield g, group
            g, group = [it['r0'], it['c0'], it['r1'], it['c1']], [it]
        else:
            g = [r0, c0, r1, c1]
            group.append(it)
    if group:
        yield g, group


def analyze(features, standard='EUDR', tolerance_ha=0.01, alert_pct=1.0, treecover_min=FAO_TREECOVER_MIN):
    """
    features : [{'geometry': GeoJSON (Polygon, MultiPolygon ou Point), 'area_ha': float|None}]
    Retourne une liste alignée sur `features`.
    """
    import rasterio
    from rasterio import features as rfeatures
    from rasterio.transform import from_origin

    cutoff = cutoff_for_standard(standard)
    min_code = cutoff - 2000 + 1               # 2020 -> 21 (pertes 2021 et après)
    results = [None] * len(features)
    by_tile = {}

    for i, f in enumerate(features):
        raw = f.get('geometry') or {}
        try:
            declared = float(f.get('area_ha')) if f.get('area_ha') not in (None, '') else None
        except (TypeError, ValueError):
            declared = None
        geom = _as_area_geometry(raw, declared)
        if geom is None:
            results[i] = {'status': STAT_NODATA, 'error': 'Géométrie absente ou non surfacique.'}
            continue
        try:
            minx, miny, maxx, maxy = _bounds(geom)
        except (ValueError, TypeError, IndexError, KeyError):
            results[i] = {'status': STAT_NODATA, 'error': 'Géométrie illisible.'}
            continue
        if not (-180 <= minx <= 180 and -80 <= miny <= 80):
            results[i] = {'status': STAT_NODATA, 'error': 'Coordonnées hors couverture Hansen (degrés WGS84 attendus).'}
            continue
        area_ha = declared if (declared and raw.get('type') != 'Point') else geodesic_area_ha(geom)
        tile, top, left = _tile_id((minx + maxx) / 2, (miny + maxy) / 2)
        r0 = max(0, int(math.floor((top - maxy) / PIXEL_DEG)) - 1)
        r1 = min(TILE_PX, int(math.ceil((top - miny) / PIXEL_DEG)) + 1)
        c0 = max(0, int(math.floor((minx - left) / PIXEL_DEG)) - 1)
        c1 = min(TILE_PX, int(math.ceil((maxx - left) / PIXEL_DEG)) + 1)
        if r1 <= r0 or c1 <= c0:
            results[i] = {'status': STAT_NODATA, 'error': 'Parcelle hors de la tuile Hansen.'}
            continue
        by_tile.setdefault((tile, top, left), []).append(
            {'i': i, 'geom': geom, 'area_ha': area_ha, 'r0': r0, 'r1': r1, 'c0': c0, 'c1': c1,
             'centroid': ((minx + maxx) / 2, (miny + maxy) / 2)})

    with rasterio.Env(**GDAL_ENV):
        for (tile, top, left), items in by_tile.items():
            for (gr0, gc0, gr1, gc1), group in _groups(items):
                nrows, ncols = gr1 - gr0, gc1 - gc0
                loss = _read('lossyear', tile, gr0, gc0, nrows, ncols)
                cover = _read('treecover2000', tile, gr0, gc0, nrows, ncols)
                pix_ha = _row_area_ha(top, gr0, nrows)[:, None]
                for it in group:
                    results[it['i']] = _analyze_one(it, loss, cover, pix_ha, gr0, gc0, top, left,
                                                    min_code, treecover_min, tolerance_ha, alert_pct,
                                                    rfeatures, from_origin)
    return results, cutoff


def _analyze_one(it, loss, cover, pix_ha, gr0, gc0, top, left, min_code, treecover_min,
                 tolerance_ha, alert_pct, rfeatures, from_origin):
    r0, r1, c0, c1 = it['r0'] - gr0, it['r1'] - gr0, it['c0'] - gc0, it['c1'] - gc0
    sub_loss = loss[r0:r1, c0:c1]
    sub_cover = cover[r0:r1, c0:c1]
    sub_area = np.broadcast_to(pix_ha[r0:r1], sub_loss.shape)
    transform = from_origin(left + it['c0'] * PIXEL_DEG, top - it['r0'] * PIXEL_DEG, PIXEL_DEG, PIXEL_DEG)

    mask = rfeatures.geometry_mask([it['geom']], out_shape=sub_loss.shape, transform=transform, invert=True)
    method = 'pixels'
    if not mask.any():
        # parcelle plus petite qu'un pixel : pixels touchés, sinon pixel du centroïde
        mask = rfeatures.geometry_mask([it['geom']], out_shape=sub_loss.shape, transform=transform,
                                       invert=True, all_touched=True)
        method = 'pixels_touches'
        if not mask.any():
            cx, cy = it['centroid']
            rr = min(max(int((top - cy) / PIXEL_DEG) - it['r0'], 0), sub_loss.shape[0] - 1)
            cc = min(max(int((cx - left) / PIXEL_DEG) - it['c0'], 0), sub_loss.shape[1] - 1)
            mask = np.zeros(sub_loss.shape, dtype=bool)
            mask[rr, cc] = True
            method = 'centroide'

    forest2000 = sub_cover >= treecover_min
    defor_px = mask & forest2000 & (sub_loss >= min_code)
    forest2020_px = mask & forest2000 & ((sub_loss == 0) | (sub_loss >= min_code))

    mask_ha = float(sub_area[mask].sum())
    area_ha = it['area_ha'] or mask_ha
    # Les fractions sont mesurées sur les pixels, puis rapportées à la surface réelle de la parcelle
    frac_defor = float(sub_area[defor_px].sum()) / mask_ha if mask_ha > 0 else 0.0
    frac_forest = float(sub_area[forest2020_px].sum()) / mask_ha if mask_ha > 0 else 0.0
    frac_forest2000 = float(sub_area[mask & forest2000].sum()) / mask_ha if mask_ha > 0 else 0.0
    defor_ha = frac_defor * area_ha
    defor_pct = frac_defor * 100.0

    years = sorted({2000 + int(v) for v in np.unique(sub_loss[defor_px]) if v})
    loss_by_year = {}
    if years:
        for y in years:
            sel = defor_px & (sub_loss == (y - 2000))
            loss_by_year[str(y)] = round(float(sub_area[sel].sum()) / mask_ha * area_ha, 4)

    status = classify(defor_ha, defor_pct, tolerance_ha, alert_pct)
    return {
        'status': status,
        'risk_level': risk_level_for(defor_pct if defor_ha > tolerance_ha else 0.0),
        'area_ha': round(area_ha, 4),
        'defor_ha': round(defor_ha, 4),
        'defor_pct': round(defor_pct, 2),
        'forest2000_pct': round(frac_forest2000 * 100.0, 1),
        'forest2020_pct': round(frac_forest * 100.0, 1),
        'cover2020': 'Foret' if frac_forest >= 0.10 else 'Agriculture',
        'years': ', '.join(str(y) for y in years),
        'loss_by_year': loss_by_year,
        'pixels': int(mask.sum()),
        'method': method,
    }
