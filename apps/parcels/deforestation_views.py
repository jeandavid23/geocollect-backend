import logging

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import deforestation as dc
from apps.accounts.permissions import module_required
from apps.accounts.tenancy import has_module

log = logging.getLogger(__name__)

# Par appel : le navigateur découpe les gros fichiers (10 000+ parcelles) en lots triés par latitude
MAX_FEATURES = 1000


class DeforestationAnalyzeView(APIView):
    """
    POST /api/v1/parcels/deforestation/analyze/

    Corps :
      {
        "standard": "EUDR" | "RA" | "EUDR+RA",   # défaut EUDR (perte après 2020)
        "tolerance_ha": 0.01,                     # perte négligée (bord de pixel)
        "alert_pct": 1.0,                         # perte <= 1 % -> « À risque », au-delà « Non conforme »
        "treecover_min": 10,                      # couvert 2000 minimal pour être « forêt » (FAO)
        "features": [ {"geometry": {...}, "area_ha": 2.5}, ... ]   # 1000 max
      }
    Réponse : { "count", "standard", "cutoff_year", "source", "results": [...] } (même ordre que features)
    """
    permission_classes = [permissions.IsAuthenticated, module_required('deforestation')]

    def post(self, request):
        features = request.data.get('features') or []
        if not isinstance(features, list) or not features:
            return Response({'detail': 'Aucune géométrie fournie (champ "features" vide).'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(features) > MAX_FEATURES:
            return Response({'detail': f'{len(features)} géométries : maximum {MAX_FEATURES} par envoi.'},
                            status=status.HTTP_400_BAD_REQUEST)

        standard = request.data.get('standard') or 'EUDR'
        if standard not in ('EUDR', 'RA', 'EUDR+RA'):
            standard = 'EUDR'
        try:
            tolerance_ha = max(0.0, float(request.data.get('tolerance_ha', 0.01)))
            alert_pct = max(0.0, float(request.data.get('alert_pct', 1.0)))
            treecover_min = min(100, max(0, int(request.data.get('treecover_min', dc.FAO_TREECOVER_MIN))))
            apply_rdue = request.data.get('apply_rdue') in (True, 'true', '1', 1)
            if apply_rdue and not has_module(request.user, 'rdue'):
                return Response({'detail': "Le module « Matrice RDUE » n'est pas activé pour votre organisation."},
                                status=status.HTTP_403_FORBIDDEN)
            forest_min_frac = min(1.0, max(0.0, float(request.data.get('forest_min_frac', 0.10))))
        except (TypeError, ValueError):
            return Response({'detail': 'Paramètres invalides.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            results, cutoff = dc.analyze(features, standard, tolerance_ha, alert_pct, treecover_min,
                                         apply_rdue, forest_min_frac)
        except Exception as exc:  # noqa: BLE001 — lecture distante des tuiles Hansen
            log.exception('Analyse Hansen impossible')
            # 503 : erreur passagère (réseau, stockage Google) — le navigateur retente le lot
            return Response({'detail': f'Lecture des données Hansen impossible pour le moment : {exc}'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response({
            'count': len(results),
            'standard': standard,
            'cutoff_year': cutoff,
            'source': f'Hansen Global Forest Change {dc.HANSEN_VERSION} (UMD / Google)',
            'rdue': apply_rdue,
            'results': results,
        })


class LandZoneListView(APIView):
    """GET /api/v1/parcels/landuse/ : zones foncières RDUE (carte) — FeatureCollection."""
    permission_classes = [permissions.IsAuthenticated, module_required('rdue')]

    def get(self, request):
        from .models import LandZone
        feats = [{'type': 'Feature', 'geometry': g, 'properties': {'category': c, 'name': n, 'region': r}}
                 for c, n, r, g in LandZone.objects.values_list('category', 'name', 'region', 'geometry')]
        return Response({'type': 'FeatureCollection', 'features': feats})
