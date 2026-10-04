import json
import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAgentOrAbove, module_required
from apps.accounts.usage import log_tool_run
from . import selfintersection
from .validator_views import MAX_FEATURES, read_json_body

log = logging.getLogger(__name__)


class SelfIntersectionView(APIView):
    """
    POST /api/v1/parcels/selfintersection/run/

    Corps (JSON, gzip accepté) :
      { "options": {"min_area_ha": 0.25, "fill_holes": true, "fill_holes_max_ha": 0, "round_corners": true,
                    "round_radius_m": 2, "multipart_to_single": true, "remove_dup_code": true, "id_field": "Field_ID"},
        "features": [{"id": "...", "geometry": {...}, "properties": {...}}, ...] }
    Réponse : { "summary": {...}, "results": [...] } — un résultat par polygone produit, statut
              « kept » (conservé), « deleted » (supprimé) ou « dup_code » (doublon de code).
    """
    permission_classes = [IsAgentOrAbove, module_required('selfintersection')]

    def post(self, request):
        try:
            body = read_json_body(request)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return Response({'detail': f'Corps de requête illisible : {exc}'}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(body, dict):
            return Response({'detail': 'Objet JSON attendu.'}, status=status.HTTP_400_BAD_REQUEST)

        features = body.get('features') or []
        if not isinstance(features, list):
            return Response({'detail': 'Champ "features" invalide.'}, status=status.HTTP_400_BAD_REQUEST)
        if not features:
            return Response({'detail': 'Aucun polygone à traiter.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(features) > MAX_FEATURES:
            return Response({'detail': f'{len(features)} polygones : maximum {MAX_FEATURES} par traitement.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            out = selfintersection.run(features, selfintersection.Options(body.get('options')))
        except Exception as exc:  # noqa: BLE001
            log.exception('Self-intersection')
            return Response({'detail': f'Erreur pendant le traitement : {exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        log_tool_run(request, 'selfintersection', len(features))
        return Response(out)


class GmrUsageView(APIView):
    """POST /api/v1/parcels/gmr/log/ {"count": n} — la jointure Polygon & GMR se fait dans le navigateur ;
    on enregistre seulement son utilisation."""
    permission_classes = [IsAgentOrAbove, module_required('gmr')]

    def post(self, request):
        try:
            n = max(0, int(request.data.get('count') or 0))
        except (TypeError, ValueError):
            n = 0
        log_tool_run(request, 'gmr', n)
        return Response(status=status.HTTP_204_NO_CONTENT)
