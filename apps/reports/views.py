"""
/api/v1/reports/
  GET    ?cooperative=&kind=     liste (sans le contenu) des rapports des coopératives accessibles
  POST                           enregistre un rapport (corps JSON, gzip accepté) ; garde les 30 derniers par type
  GET    <id>/                   contenu complet (JSON)
  DELETE <id>/                   suppression (coopérative, super admin gestionnaire, propriétaire)
"""
import gzip
import json

from django.http import HttpResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAgentOrAbove, resolve_cooperative, scope_to_cooperative
from apps.parcels.validator_views import read_json_body
from .models import AnalysisReport

KEEP_PER_KIND = 30
MAX_COMPRESSED = 25 * 1024 * 1024


def _meta(r):
    return {
        'id': str(r.id), 'cooperative': str(r.cooperative_id), 'cooperative_name': r.cooperative.name,
        'kind': r.kind, 'kind_label': r.get_kind_display(), 'title': r.title, 'source': r.source,
        'summary': r.summary, 'feature_count': r.feature_count, 'size_bytes': r.size_bytes,
        'created_by_name': (r.created_by.full_name or r.created_by.username) if r.created_by else '',
        'created_at': r.created_at.isoformat(),
    }


class ReportListCreateView(APIView):
    permission_classes = [IsAgentOrAbove]

    def get(self, request):
        qs = scope_to_cooperative(AnalysisReport.objects.select_related('cooperative', 'created_by').defer('content'),
                                  request.user)
        if request.query_params.get('cooperative'):
            qs = qs.filter(cooperative_id=request.query_params['cooperative'])
        if request.query_params.get('kind'):
            qs = qs.filter(kind=request.query_params['kind'])
        try:
            return Response([_meta(r) for r in qs[:300]])
        except Exception:  # noqa: BLE001 — identifiant mal formé
            return Response([])

    def post(self, request):
        try:
            body = read_json_body(request)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return Response({'detail': f'Corps illisible : {exc}'}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(body, dict) or body.get('kind') not in AnalysisReport.KINDS:
            return Response({'detail': 'Type de rapport invalide.'}, status=status.HTTP_400_BAD_REQUEST)
        coop = resolve_cooperative(request, body)
        if coop is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
        content = {k: body.get(k) for k in ('summary', 'tables', 'features', 'meta')}
        blob = gzip.compress(json.dumps(content, ensure_ascii=False, separators=(',', ':')).encode(), 6)
        if len(blob) > MAX_COMPRESSED:
            return Response({'detail': 'Rapport trop volumineux pour être enregistré (téléchargez-le directement).'},
                            status=status.HTTP_400_BAD_REQUEST)
        summary = body.get('summary') if isinstance(body.get('summary'), list) else []
        r = AnalysisReport.objects.create(
            cooperative=coop, kind=body['kind'], title=str(body.get('title') or AnalysisReport.KINDS[body['kind']])[:200],
            source=str(body.get('source') or '')[:255], summary=summary[:40],
            feature_count=len(body.get('features') or []), content=blob, size_bytes=len(blob), created_by=request.user,
        )
        # rétention : les plus anciens au-delà de 30 rapports du même type sont supprimés
        old = AnalysisReport.objects.filter(cooperative=coop, kind=r.kind).values_list('id', flat=True)[KEEP_PER_KIND:]
        AnalysisReport.objects.filter(id__in=list(old)).delete()
        return Response(_meta(r), status=status.HTTP_201_CREATED)


class ReportDetailView(APIView):
    permission_classes = [IsAgentOrAbove]

    def _get(self, request, pk):
        try:
            return scope_to_cooperative(AnalysisReport.objects.select_related('cooperative', 'created_by'),
                                        request.user).get(pk=pk)
        except Exception:  # noqa: BLE001
            return None

    def get(self, request, pk):
        r = self._get(request, pk)
        if r is None:
            return Response({'detail': 'Rapport introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        # contenu déjà compressé : renvoyé tel quel, décompressé par le navigateur
        meta = json.dumps(_meta(r), ensure_ascii=False)
        resp = HttpResponse(bytes(r.content), content_type='application/json')
        resp['Content-Encoding'] = 'gzip'
        resp['X-Report-Meta'] = meta.encode('ascii', 'backslashreplace').decode()
        return resp

    def delete(self, request, pk):
        r = self._get(request, pk)
        if r is None:
            return Response({'detail': 'Rapport introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        if request.user.role == 'agent' and r.created_by_id != request.user.id:
            return Response({'detail': 'Un agent ne peut supprimer que ses propres rapports.'}, status=status.HTTP_403_FORBIDDEN)
        r.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
