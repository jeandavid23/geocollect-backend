"""
Analyse de déforestation EUDR via Google Earth Engine.

Sources de données :
  - Hansen Global Forest Change (UMD) : bande `lossyear` -> perte de couvert
    forestier annuelle 2001-2023. Seuil de conformité EUDR = 31/12/2020.
  - Global Forest Watch / RADD alerts (radar-wur) : alertes de déforestation
    récentes (couverture Afrique). Utilisées en complément lorsque disponibles.

Le module s'initialise avec un compte de service GEE fourni via la variable
d'environnement GEE_SERVICE_ACCOUNT_JSON (contenu JSON de la clé). Tant que la
clé n'est pas configurée, analyze_features() renvoie une erreur explicite.
"""
import json
import math

from decouple import config

GEE_HANSEN_ASSET = 'UMD/hansen/global_forest_change_2023_v1_11'
RADD_ASSET = 'projects/radar-wur/raddalert/v1'
CANOPY_THRESHOLD = 30          # % de couvert = forêt en 2000
EUDR_CUTOFF_YEAR = 2020        # perte APRÈS cette année = non conforme EUDR
DEFAULT_POINT_RADIUS_M = 100   # rayon de bufferisation d'un point sans superficie

_INITIALIZED = False
_INIT_ERROR = None


def _ensure_init():
    """Initialise GEE une seule fois. Retourne (ok, message_erreur)."""
    global _INITIALIZED, _INIT_ERROR
    if _INITIALIZED:
        return True, None
    key_json = config('GEE_SERVICE_ACCOUNT_JSON', default='')
    if not key_json:
        return False, (
            "Google Earth Engine n'est pas configuré. "
            "Ajoutez la variable d'environnement GEE_SERVICE_ACCOUNT_JSON "
            "(contenu de la clé JSON du compte de service) sur le serveur."
        )
    try:
        import ee
        info = json.loads(key_json)
        creds = ee.ServiceAccountCredentials(info['client_email'], key_data=key_json)
        ee.Initialize(creds)
        _INITIALIZED = True
        return True, None
    except Exception as exc:  # noqa: BLE001
        _INIT_ERROR = str(exc)
        return False, f"Échec d'initialisation Earth Engine : {exc}"


def _risk_level(loss_recent_pct, alert_area_ha):
    """Détermine le niveau de risque EUDR à partir des signaux disponibles."""
    if loss_recent_pct is None:
        return 'unknown', 'Indéterminé'
    # Toute perte forestière après le seuil EUDR (2020) = risque élevé.
    if loss_recent_pct >= 1.0 or (alert_area_ha or 0) > 0.05:
        return 'high', 'Élevé'
    if loss_recent_pct > 0:
        return 'medium', 'Moyen'
    return 'low', 'Faible'


def analyze_features(features, cutoff_year=EUDR_CUTOFF_YEAR):
    """
    features : liste de dict {'name': str, 'geometry': GeoJSON geometry, 'area_ha': float|None}
    Retourne : (results, error)
      results = liste alignée sur `features`, chaque item :
        { name, geometry_type, forest_pct, loss_recent_pct, loss_total_pct,
          loss_recent_ha, alert_area_ha, risk, risk_label, area_ha }
    """
    ok, err = _ensure_init()
    if not ok:
        return None, err

    import ee

    try:
        gfc = ee.Image(GEE_HANSEN_ASSET)
        treecover = gfc.select('treecover2000')
        lossyear = gfc.select('lossyear')          # 0 = pas de perte, 1..23 = 2001..2023
        forest = treecover.gte(CANOPY_THRESHOLD)
        pixel = ee.Image.pixelArea()
        thr = cutoff_year - 2000                    # 2020 -> 20

        # Couche d'alertes RADD (GFW). Optionnelle : si indisponible on continue.
        try:
            radd = ee.ImageCollection(RADD_ASSET).filterMetadata(
                'layer', 'contains', 'alert'
            ).mosaic()
            # bande 'Alert' : 2 = non confirmé, 3 = confirmé ; 'Date' : AAJJJ (yr*1000+doy)
            radd_conf = radd.select('Alert').gte(2)
            radd_date = radd.select('Date')
            cutoff_code = (cutoff_year % 100) * 1000  # ex: 2020 -> 20000
            radd_recent = radd_conf.And(radd_date.gte(cutoff_code))
            has_radd = True
        except Exception:  # noqa: BLE001
            has_radd = False

        bands = [
            pixel.rename('area'),
            forest.multiply(pixel).rename('forest'),
            lossyear.gte(thr).And(forest).multiply(pixel).rename('loss_recent'),
            lossyear.gte(1).And(forest).multiply(pixel).rename('loss_total'),
        ]
        if has_radd:
            bands.append(radd_recent.unmask(0).multiply(pixel).rename('alert'))
        stack = ee.Image.cat(bands)

        # Construction de la FeatureCollection (une seule requête getInfo).
        ee_feats = []
        for i, f in enumerate(features):
            geom_json = f.get('geometry')
            if not geom_json:
                continue
            geom = ee.Geometry(geom_json)
            gtype = (geom_json.get('type') or '').lower()
            if 'point' in gtype:
                area_ha = f.get('area_ha')
                if area_ha and area_ha > 0:
                    radius = math.sqrt((area_ha * 10000.0) / math.pi)
                else:
                    radius = DEFAULT_POINT_RADIUS_M
                geom = geom.buffer(radius)
            ee_feats.append(ee.Feature(geom, {'idx': i}))

        fc = ee.FeatureCollection(ee_feats)

        def per_feat(feat):
            stats = stack.reduceRegion(
                reducer=ee.Reducer.sum(),
                geometry=feat.geometry(),
                scale=30,
                maxPixels=1e10,
                bestEffort=True,
            )
            return feat.set(stats)

        computed = fc.map(per_feat).getInfo()
    except Exception as exc:  # noqa: BLE001
        return None, f"Erreur d'analyse Earth Engine : {exc}"

    # Réassemblage des résultats dans l'ordre d'entrée.
    by_idx = {}
    for feat in computed.get('features', []):
        props = feat.get('properties', {})
        by_idx[props.get('idx')] = props

    results = []
    for i, f in enumerate(features):
        props = by_idx.get(i)
        if props is None:
            results.append({
                'name': f.get('name', f'#{i + 1}'),
                'geometry_type': (f.get('geometry') or {}).get('type', ''),
                'error': 'géométrie manquante ou invalide',
                'risk': 'unknown', 'risk_label': 'Indéterminé',
            })
            continue
        area = props.get('area') or 0.0
        forest_a = props.get('forest') or 0.0
        loss_recent_a = props.get('loss_recent') or 0.0
        loss_total_a = props.get('loss_total') or 0.0
        alert_a = props.get('alert')  # peut être None si RADD indisponible

        def pct(part):
            return round((part / area) * 100, 2) if area > 0 else 0.0

        loss_recent_pct = pct(loss_recent_a)
        alert_ha = round(alert_a / 10000.0, 4) if alert_a is not None else None
        risk, risk_label = _risk_level(loss_recent_pct, alert_ha)

        results.append({
            'name': f.get('name', f'#{i + 1}'),
            'geometry_type': (f.get('geometry') or {}).get('type', ''),
            'area_ha': round(area / 10000.0, 4),
            'forest_pct': pct(forest_a),
            'loss_recent_pct': loss_recent_pct,        # perte après seuil EUDR (2020)
            'loss_total_pct': pct(loss_total_a),       # perte totale 2001->2023
            'loss_recent_ha': round(loss_recent_a / 10000.0, 4),
            'alert_area_ha': alert_ha,                 # alertes GFW/RADD récentes
            'risk': risk,
            'risk_label': risk_label,
        })

    return results, None
