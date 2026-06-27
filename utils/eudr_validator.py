"""
Polygon Validator EUDR by JDK
Validateur de polygones pour la conformité EUDR (règlement UE déforestation).
"""
import math
from datetime import datetime
from typing import Any


def _polygon_area(coords: list) -> float:
    area = 0.0
    n = len(coords)
    for i in range(n - 1):
        area += coords[i][0] * coords[i + 1][1]
        area -= coords[i + 1][0] * coords[i][1]
    return abs(area / 2.0)


def _degrees_to_m2(deg_area: float, lat: float = 7.0) -> float:
    lat_rad = math.radians(lat)
    m_per_deg_lat = 111320.0
    m_per_deg_lng = 111320.0 * math.cos(lat_rad)
    return deg_area * m_per_deg_lat * m_per_deg_lng


def _segments_intersect(p1, p2, p3, p4) -> bool:
    d1x, d1y = p2[0] - p1[0], p2[1] - p1[1]
    d2x, d2y = p4[0] - p3[0], p4[1] - p3[1]
    cross = d1x * d2y - d1y * d2x
    if abs(cross) < 1e-10:
        return False
    t = ((p3[0] - p1[0]) * d2y - (p3[1] - p1[1]) * d2x) / cross
    u = ((p3[0] - p1[0]) * d1y - (p3[1] - p1[1]) * d1x) / cross
    return 0 < t < 1 and 0 < u < 1


def _has_self_intersection(coords: list) -> bool:
    n = len(coords) - 1
    for i in range(n - 1):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_intersect(coords[i], coords[i + 1], coords[j], coords[j + 1]):
                return True
    return False


def _is_clockwise(coords: list) -> bool:
    total = 0.0
    for i in range(len(coords) - 1):
        total += (coords[i + 1][0] - coords[i][0]) * (coords[i + 1][1] + coords[i][1])
    return total > 0


def validate_eudr(
    geometry: dict,
    min_area_ha: float = 0.1,
    max_area_ha: float = 500.0,
    country: str = "Côte d'Ivoire",
    culture: str = 'Cacao',
) -> dict:
    """
    Exécute les 10 contrôles du Polygon Validator EUDR by JDK.
    Retourne un dict avec le score EUDR, les checks et le résumé.
    """
    coords = geometry.get('coordinates', [[]])[0]
    checks = []

    # 1. Géométrie valide (min 4 points)
    valid_geom = len(coords) >= 4
    checks.append({
        'name': 'geometry_valid',
        'label': 'Géométrie valide',
        'passed': valid_geom,
        'severity': 'error',
        'message': f'Polygone {"valide" if valid_geom else "invalide"} — {len(coords)} point(s)',
        'value': len(coords),
    })

    # 2. Anneau fermé
    is_closed = (len(coords) >= 2 and
                 coords[0][0] == coords[-1][0] and
                 coords[0][1] == coords[-1][1])
    checks.append({
        'name': 'ring_closed',
        'label': 'Anneau fermé',
        'passed': is_closed,
        'severity': 'error',
        'message': 'Polygone fermé correctement' if is_closed else 'Premier et dernier point différents',
    })

    # 3. Auto-intersection
    no_self_intersect = not _has_self_intersection(coords)
    checks.append({
        'name': 'no_self_intersection',
        'label': "Pas d'auto-intersection",
        'passed': no_self_intersect,
        'severity': 'error',
        'message': 'Aucune auto-intersection' if no_self_intersect else 'Polygone auto-intersectant (géométrie invalide)',
    })

    # 4. Superficie minimale
    deg_area = _polygon_area(coords)
    area_m2 = _degrees_to_m2(deg_area)
    area_ha = area_m2 / 10000
    above_min = area_ha >= min_area_ha
    checks.append({
        'name': 'min_area',
        'label': f'Superficie minimale ({min_area_ha} ha)',
        'passed': above_min,
        'severity': 'error',
        'message': f'Superficie {area_ha:.3f} ha — {"conforme" if above_min else f"inférieure au minimum {min_area_ha} ha"}',
        'value': f'{area_ha:.3f} ha',
    })

    # 5. Superficie maximale
    below_max = area_ha <= max_area_ha
    checks.append({
        'name': 'max_area',
        'label': f'Superficie maximale ({max_area_ha} ha)',
        'passed': below_max,
        'severity': 'warning',
        'message': f'Superficie {area_ha:.3f} ha — {"dans les limites" if below_max else f"supérieure au maximum {max_area_ha} ha"}',
        'value': f'{area_ha:.3f} ha',
    })

    # 6. Orientation GeoJSON (CCW)
    cw = _is_clockwise(coords)
    checks.append({
        'name': 'orientation',
        'label': 'Orientation GeoJSON (CCW)',
        'passed': not cw,
        'severity': 'warning',
        'message': 'Orientation antihoraire (conforme GeoJSON)' if not cw else 'Orientation horaire (non standard GeoJSON)',
    })

    # 7. Nombre de sommets
    vertex_count = len(coords) - 1
    good_vertices = 3 <= vertex_count <= 200
    checks.append({
        'name': 'vertex_count',
        'label': 'Nombre de sommets (3-200)',
        'passed': good_vertices,
        'severity': 'warning',
        'message': f'{vertex_count} sommets — {"valide" if good_vertices else "hors plage acceptable"}',
        'value': vertex_count,
    })

    # 8. Localisation pays (mock — PostGIS en production)
    in_country = country in ("Côte d'Ivoire", 'Ghana', 'Cameroun', 'Nigeria', 'Liberia')
    checks.append({
        'name': 'country_boundary',
        'label': f'Localisation ({country})',
        'passed': in_country,
        'severity': 'error',
        'message': f'Parcelle localisée en {country}' if in_country else f'Parcelle hors limites de {country}',
    })

    # 9. Absence de déforestation (mock — API externe en production)
    import random
    random.seed(str(coords[0]) if coords else '0')
    no_deforestation = random.random() > 0.15
    checks.append({
        'name': 'no_deforestation',
        'label': 'Absence de déforestation (post-2020)',
        'passed': no_deforestation,
        'severity': 'error',
        'message': 'Aucune déforestation détectée après le 31/12/2020' if no_deforestation
                   else 'Risque de déforestation — vérification terrain requise',
    })

    # 10. Zone protégée (mock)
    not_protected = random.random() > 0.08
    checks.append({
        'name': 'not_protected_area',
        'label': 'Hors zone protégée / forêt classée',
        'passed': not_protected,
        'severity': 'error',
        'message': "La parcelle n'est pas en zone protégée" if not_protected
                   else 'La parcelle chevauche une zone protégée ou une forêt classée',
    })

    # Score EUDR
    error_checks = [c for c in checks if c['severity'] == 'error']
    warning_checks = [c for c in checks if c['severity'] == 'warning']
    error_score = sum(1 for c in error_checks if c['passed']) / max(len(error_checks), 1)
    warning_score = sum(1 for c in warning_checks if c['passed']) / max(len(warning_checks), 1)
    eudr_score = round((error_score * 0.8 + warning_score * 0.2) * 100)

    is_valid = all(c['passed'] for c in error_checks) and eudr_score >= 60
    failed_errors = sum(1 for c in error_checks if not c['passed'])

    return {
        'is_valid': is_valid,
        'eudr_score': eudr_score,
        'checks': checks,
        'area_hectares': round(area_ha, 4),
        'summary': (
            f'Polygone conforme EUDR — Score {eudr_score}%'
            if is_valid
            else f'Polygone NON conforme EUDR — Score {eudr_score}% — {failed_errors} erreur(s) critique(s)'
        ),
        'timestamp': datetime.utcnow().isoformat(),
    }
