from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .deforestation import analyze_features, EUDR_CUTOFF_YEAR

MAX_FEATURES = 500


class DeforestationAnalyzeView(APIView):
    """
    POST /api/v1/parcels/deforestation/analyze/

    Corps attendu :
      {
        "cutoff_year": 2020,          # optionnel (défaut 2020, seuil EUDR)
        "features": [
          {"name": "Parcelle 1", "area_ha": 2.5,
           "geometry": {"type": "Polygon", "coordinates": [[[lng,lat],...]]}},
          ...
        ]
      }

    Réponse :
      { "count": N, "cutoff_year": 2020, "results": [ {..., risk, risk_label}, ... ] }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        features = request.data.get('features') or []
        if not isinstance(features, list) or not features:
            return Response(
                {'detail': 'Aucune géométrie fournie (champ "features" vide).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(features) > MAX_FEATURES:
            return Response(
                {'detail': f'Trop de géométries ({len(features)}). Maximum {MAX_FEATURES} par analyse.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            cutoff_year = int(request.data.get('cutoff_year') or EUDR_CUTOFF_YEAR)
        except (TypeError, ValueError):
            cutoff_year = EUDR_CUTOFF_YEAR

        results, error = analyze_features(features, cutoff_year=cutoff_year)
        if error:
            # 503 : le service (GEE) n'est pas disponible / non configuré.
            return Response({'detail': error}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        summary = {'high': 0, 'medium': 0, 'low': 0, 'unknown': 0}
        for r in results:
            summary[r.get('risk', 'unknown')] = summary.get(r.get('risk', 'unknown'), 0) + 1

        return Response({
            'count': len(results),
            'cutoff_year': cutoff_year,
            'summary': summary,
            'results': results,
        })
