"""
Étape 1 (contrôles minimaux, communs à tous les outils) et grille d'analyse.

Les corrections avancées (auto-intersections, chevauchements entre parcelles) restent faites par les outils existants
(Polygon Validator, Self-intersection, contrôle spatial PostGIS de GeoCollect). Ici : coordonnées WGS84 arrondies
à 6 décimales, anneaux fermés et suffisants, surface GÉODÉSIQUE (pyproj si présent, sinon formule sphérique),
règle point / polygone (4 ha), et rasterisation identique partout (numpy pur, centre des pixels).
"""
import math

import numpy as np

GRID_RES = 1.0 / 12000.0      # ≈ 9,3 m : grille du JRC GFC2020 (la plus fine des sources)
EARTH_R = 6371007.2


def _round(c, d=6):
    return [round(float(c[0]), d), round(float(c[1]), d)]


def round_geometry(geom, d=6):
    t = geom.get("type")
    if t == "Point":
        return {"type": "Point", "coordinates": _round(geom["coordinates"], d)}
    if t == "Polygon":
        return {"type": "Polygon", "coordinates": [[_round(p, d) for p in ring] for ring in geom["coordinates"]]}
    if t == "MultiPolygon":
        return {"type": "MultiPolygon", "coordinates": [[[_round(p, d) for p in ring] for ring in poly]
                                                        for poly in geom["coordinates"]]}
    return geom


def polygons(geom):
    t = (geom or {}).get("type")
    if t == "Polygon":
        return [geom["coordinates"]]
    if t == "MultiPolygon":
        return list(geom["coordinates"])
    return []


def _ring_area_m2(ring):
    """Aire géodésique d'un anneau (formule sphérique de Chamberlain & Duquette, m²)."""
    if len(ring) < 4:
        return 0.0
    total = 0.0
    for i in range(len(ring) - 1):
        lon1, lat1 = map(math.radians, ring[i][:2])          # altitude éventuelle ignorée
        lon2, lat2 = map(math.radians, ring[i + 1][:2])
        total += (lon2 - lon1) * (2 + math.sin(lat1) + math.sin(lat2))
    return abs(total * EARTH_R * EARTH_R / 2.0)


def geodesic_area_ha(geom):
    """Surface géodésique (ha). pyproj (ellipsoïde WGS84) s'il est installé, sinon formule sphérique (écart < 0,5 %)."""
    try:
        from pyproj import Geod
        g = Geod(ellps="WGS84")
        total = 0.0
        for poly in polygons(geom):
            for k, ring in enumerate(poly):
                lons, lats = zip(*ring)
                a, _ = g.polygon_area_perimeter(lons, lats)
                total += abs(a) if k == 0 else -abs(a)
        return total / 10000.0
    except ImportError:
        total = 0.0
        for poly in polygons(geom):
            for k, ring in enumerate(poly):
                a = _ring_area_m2(ring)
                total += a if k == 0 else -a
        return total / 10000.0


def circle(lon, lat, radius_m, n=32):
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(0.01, math.cos(math.radians(lat))))
    ring = [[lon + dlon * math.cos(2 * math.pi * k / n), lat + dlat * math.sin(2 * math.pi * k / n)] for k in range(n)]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


def validate(geom, declared_area_ha=None, rules=None):
    """
    Contrôle d'une géométrie avant analyse. Renvoie (géométrie d'analyse ou None, rapport).
    Rapport : {valid, reasons[], area_ha, kind, needs_polygon}
    """
    from .config import REGULATION
    rules = rules or {}
    reasons = []
    if not isinstance(geom, dict) or geom.get("type") not in ("Point", "Polygon", "MultiPolygon"):
        return None, {"valid": False, "reasons": ["Géométrie absente ou type non pris en charge."]}
    geom = round_geometry(geom, REGULATION["coordinate_decimals"])
    if geom["type"] == "Point":
        lon, lat = geom["coordinates"][:2]
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return None, {"valid": False, "reasons": ["Coordonnées hors WGS84 (degrés attendus)."]}
        area = float(declared_area_ha) if declared_area_ha else None
        needs_polygon = bool(area and area > REGULATION["point_max_area_ha"])
        if needs_polygon:
            reasons.append(f"Parcelle de {area:.2f} ha déclarée par un point : un polygone est obligatoire au-delà de "
                           f"{REGULATION['point_max_area_ha']:.0f} ha (art. 9).")
        radius = math.sqrt(area * 10000.0 / math.pi) if area else rules.get("point_default_radius_m", 50.0)
        return circle(lon, lat, radius), {"valid": True, "reasons": reasons, "area_ha": round(area or math.pi * radius ** 2 / 10000, 4),
                                          "kind": "point", "needs_polygon": needs_polygon}
    for poly in polygons(geom):
        for ring in poly:
            if len(ring) < 4:
                return None, {"valid": False, "reasons": ["Anneau de moins de 4 sommets."]}
            if ring[0] != ring[-1]:
                ring.append(list(ring[0]))
            for lon, lat in (c[:2] for c in ring):
                if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
                    return None, {"valid": False, "reasons": ["Coordonnées hors WGS84 (degrés attendus)."]}
    try:
        from shapely.geometry import shape
        from shapely.validation import explain_validity
        s = shape(geom)
        if not s.is_valid:
            return None, {"valid": False, "reasons": [f"Géométrie invalide : {explain_validity(s)} (corriger avec Self-intersection)."]}
    except ImportError:
        pass
    area = geodesic_area_ha(geom)
    if area <= 0:
        return None, {"valid": False, "reasons": ["Surface nulle."]}
    return geom, {"valid": True, "reasons": reasons, "area_ha": round(area, 4), "kind": "polygon", "needs_polygon": False}


def bbox(geom):
    xs, ys = [], []
    for poly in polygons(geom):
        for ring in poly:
            for c in ring:                                    # [x, y] ou [x, y, z]
                xs.append(c[0]); ys.append(c[1])
    return min(xs), min(ys), max(xs), max(ys)


def grid_for(geom, res=GRID_RES):
    """Grille commune alignée sur l'origine (0, 0) : centres des pixels (lon, lat) couvrant l'emprise."""
    minx, miny, maxx, maxy = bbox(geom)
    c0, c1 = math.floor(minx / res), math.ceil(maxx / res)
    r0, r1 = math.floor(miny / res), math.ceil(maxy / res)
    lons = (np.arange(c0, c1) + 0.5) * res
    lats = (np.arange(r1 - 1, r0 - 1, -1) + 0.5) * res      # du nord au sud
    return np.meshgrid(lons, lats)


def rasterize(geom, lon, lat):
    """Masque des pixels dont le CENTRE est dans la géométrie (pair-impair : trous gérés) ; repli sur le pixel du centroïde."""
    mask = np.zeros(lon.shape, dtype=bool)
    for poly in polygons(geom):
        inside = np.zeros(lon.shape, dtype=bool)
        for ring in poly:
            r = np.asarray(ring, dtype=np.float64)
            x1, y1 = r[:-1, 0], r[:-1, 1]
            x2, y2 = r[1:, 0], r[1:, 1]
            for a, b, c, d in zip(x1, y1, x2, y2):
                cond = (b > lat) != (d > lat)
                with np.errstate(divide="ignore", invalid="ignore"):
                    xin = (c - a) * (lat - b) / (d - b) + a
                inside ^= cond & (lon < xin)
        mask |= inside
    if not mask.any() and mask.size:
        minx, miny, maxx, maxy = bbox(geom)
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        i = int(np.argmin(np.abs(lat[:, 0] - cy)))
        j = int(np.argmin(np.abs(lon[0, :] - cx)))
        mask[i, j] = True
    return mask
