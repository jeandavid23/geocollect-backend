import gzip
import io
import json
import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAgentOrAbove, resolve_cooperative, module_required
from .models import LegacyParcel, Parcel
from apps.accounts.usage import log_tool_run
from . import validator

log = logging.getLogger(__name__)

MAX_FEATURES = 50000
MAX_JSON_BYTES = 120 * 1024 * 1024      # corps décompressé


def read_json_body(request):
    """
    Corps JSON, éventuellement compressé en gzip par le navigateur (Content-Encoding: gzip) :
    10 000 polygones pèsent ~10 Mo en JSON, ~3 Mo compressés.
    On lit le corps brut (sans passer par request.data, qui ne sait pas décompresser).
    """
    raw = request.body
    if (request.META.get('HTTP_CONTENT_ENCODING') or '').lower() == 'gzip':
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            raw = gz.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError('Données trop volumineuses.')
    return json.loads(raw or b'{}')


class PolygonValidatorView(APIView):
    """
    POST /api/v1/parcels/validator/run/

    Corps (JSON, gzip accepté) :
      { "options": {"threshold": 18, "min_area_ha": 0.01, ...},
        "features": [{"id": "...", "geometry": {...}}, ...] }          # fichier importé
    ou
      { "options": {...}, "source": "parcels" | "legacy", "cooperative": "<uuid, super admin>" }
                                                                       # données déjà en base
    Réponse : { "summary": {...}, "results": [...] } (même ordre que les entités analysées ;
              pour une source en base, chaque résultat porte aussi « pk »).
    """
    permission_classes = [IsAgentOrAbove, module_required('validator')]

    def post(self, request):
        try:
            body = read_json_body(request)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return Response({'detail': f'Corps de requête illisible : {exc}'}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(body, dict):
            return Response({'detail': 'Objet JSON attendu.'}, status=status.HTTP_400_BAD_REQUEST)

        opts = validator.Options(body.get('options'))
        source = body.get('source')
        pks = None
        if source in ('parcels', 'legacy'):
            coop = resolve_cooperative(request, body)
            if coop is None:
                return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
            if source == 'parcels':
                rows = Parcel.objects.filter(cooperative=coop).values_list('pk', 'field_id', 'geometry')
            else:
                rows = LegacyParcel.objects.filter(cooperative=coop).values_list('pk', 'name', 'geometry')
            rows = list(rows.iterator(chunk_size=2000))
            pks = [str(r[0]) for r in rows]
            features = [{'id': r[1] or str(r[0]), 'geometry': r[2]} for r in rows]
        else:
            features = body.get('features') or []
            if not isinstance(features, list):
                return Response({'detail': 'Champ "features" invalide.'}, status=status.HTTP_400_BAD_REQUEST)

        if not features:
            return Response({'detail': 'Aucun polygone à valider.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(features) > MAX_FEATURES:
            return Response({'detail': f'{len(features)} polygones : maximum {MAX_FEATURES} par validation.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            out = validator.run(features, opts)
        except Exception as exc:  # noqa: BLE001
            log.exception('Polygon Validator')
            return Response({'detail': f'Erreur pendant la validation : {exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if pks is not None:
            for r, pk in zip(out['results'], pks):
                r['pk'] = pk
            # la source en base n'a pas été envoyée par le navigateur : on renvoie les géométries
            for r, f in zip(out['results'], features):
                r.setdefault('geometry', f['geometry'])
        out['summary']['source'] = source or 'fichier'
        log_tool_run(request, 'validator', len(features))
        return Response(out)
