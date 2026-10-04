import json
import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAgentOrAbove, module_required
from apps.accounts.usage import log_tool_run
from apps.accounts.notify import notify_tool
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
        s = out['summary']
        notify_tool(request, 'Self-intersection terminé',
                    f"{s['initial_count']} polygone(s) · {s['final_count']} conservé(s) · {s['deleted_count']} supprimé(s) · "
                    f"{s['dup_code_removed']} doublon(s) de code · {s['geometries_fixed']} réparé(s)")
        return Response(out)


class GmrUsageView(APIView):
    """POST /api/v1/parcels/gmr/log/ {"count": n} — la jointure Polygon & GMR se fait dans le navigateur ;
    on enregistre seulement son utilisation."""
    permission_classes = [IsAgentOrAbove, module_required('gmr')]

    def post(self, request):
        n, matched, no_match = (_int(request.data.get(k)) for k in ('count', 'matched', 'no_match'))
        log_tool_run(request, 'gmr', n)
        notify_tool(request, 'Polygon & GMR terminé',
                    f'{n} polygone(s) croisé(s) avec le registre · {matched} avec registre · {no_match} sans registre',
                    'warning' if no_match else 'success')
        return Response(status=status.HTTP_204_NO_CONTENT)


def _int(v):
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


class DeforestationDoneView(APIView):
    """
    POST /api/v1/parcels/deforestation/done/ {"total", "conforme", "a_risque", "non_conforme", "indetermine", "standard"}
    L'analyse se fait par lots ; le navigateur signale la fin : suivi d'utilisation + notification.
    """
    permission_classes = [IsAgentOrAbove, module_required('deforestation')]

    def post(self, request):
        d = request.data if isinstance(request.data, dict) else {}
        total, ok, risk, bad, undet = (_int(d.get(k)) for k in ('total', 'conforme', 'a_risque', 'non_conforme', 'indetermine'))
        std = str(d.get('standard') or 'EUDR')[:10]
        log_tool_run(request, 'deforestation', total)
        notify_tool(request, f'Analyse déforestation terminée ({std})',
                    f'{total} parcelle(s) · {ok} conforme(s) · {risk} à risque · {bad} non conforme(s)'
                    + (f' · {undet} indéterminée(s)' if undet else ''),
                    'error' if bad else 'warning' if risk or undet else 'success')
        return Response(status=status.HTTP_204_NO_CONTENT)
