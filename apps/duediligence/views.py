"""
API de la diligence raisonnée (module de licence « due_diligence »).

  GET  /api/v1/dd/catalog/                         jeux de données, seuils par défaut, paramètres réglementaires
  GET  /api/v1/dd/settings/?cooperative=           seuils de la coopérative ;  PUT : enregistrer
  GET  /api/v1/dd/analyses/?cooperative=           historique (sans les résultats)
  POST /api/v1/dd/analyses/                        lancer {cooperative, source, lot, thresholds, report_meta}
  GET  /api/v1/dd/analyses/<id>/                   analyse complète (résultats par parcelle + décisions)
  GET  /api/v1/dd/analyses/<id>/status/            avancement (suivi léger)
  POST /api/v1/dd/analyses/<id>/decisions/         décision humaine (ajout seulement)
  GET  /api/v1/dd/analyses/<id>/whisp-export/      GeoJSON pour whisp.earthmap.org
  POST /api/v1/dd/analyses/<id>/whisp-import/      CSV de résultats WHISP → concordance
"""
from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import ActivityLog
from apps.accounts.permissions import IsCooperativeOrAdmin, module_required, resolve_cooperative, scope_to_cooperative
from . import services
from .models import DDAnalysis, DDDecision, DDSettings

PERMS = [IsCooperativeOrAdmin, module_required('due_diligence')]
MAX_ATTACHMENT = 3 * 1024 * 1024


def _row(a, with_results=False):
    d = {'id': str(a.id), 'cooperative': str(a.cooperative_id), 'cooperative_name': a.cooperative.name,
         'lot': str(a.lot_id) if a.lot_id else None, 'lot_code': a.lot.code if a.lot_id else None,
         'created_at': a.created_at, 'created_by': (a.created_by.full_name or a.created_by.username) if a.created_by else None,
         'status': a.status, 'progress': a.progress, 'total': a.total, 'scope': a.scope, 'thresholds': a.thresholds,
         'report_meta': a.report_meta, 'summary': a.summary, 'whisp_status': a.whisp_status,
         'whisp_version': a.whisp_version, 'whisp_message': a.whisp_message, 'error': a.error, 'keep_until': a.keep_until}
    if with_results:
        d['manifest'] = a.manifest
        d['results'] = a.get_results()
        d['decisions'] = [_decision(x) for x in a.decisions.all()]
    return d


def _decision(x):
    return {'id': str(x.id), 'parcel_key': x.parcel_key, 'decision': x.decision, 'decision_label': x.get_decision_display(),
            'comment': x.comment, 'author_name': x.author_name, 'created_at': x.created_at,
            'attachment_name': x.attachment_name, 'attachment_type': x.attachment_type,
            'attachment_data': x.attachment_data or None}


class CatalogView(APIView):
    permission_classes = PERMS

    def get(self, request):
        from eudr_evidence import __version__, catalog, config, dd
        return Response({'engine_version': __version__, 'datasets': catalog.dataset_table(),
                         'sources': catalog.SOURCES, 'defaults': config.DEFAULTS, 'regulation': config.REGULATION,
                         'whisp_configured': services.whisp_configured(),
                         # textes d'explication, communs à tous les outils (moteur eudr_evidence)
                         'explain': {'method': dd.METHOD_STEPS, 'risk': dd.RISK_MEANING, 'reasons': dd.REASON_EXPLAIN,
                                     'actions': dd.REASON_ACTIONS, 'thresholds': dd.THRESHOLD_LABELS,
                                     'limits': dd.TXT['limits']}})


class SettingsView(APIView):
    permission_classes = PERMS

    def get(self, request):
        from eudr_evidence.config import DEFAULTS, thresholds
        coop = resolve_cooperative(request)
        if coop is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
        s = DDSettings.objects.filter(cooperative=coop).first()
        return Response({'defaults': DEFAULTS, 'thresholds': thresholds(s.thresholds if s else None)})

    def put(self, request):
        from eudr_evidence.config import DEFAULTS, thresholds
        coop = resolve_cooperative(request, request.data)
        if coop is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
        clean = {k: v for k, v in (request.data.get('thresholds') or {}).items() if k in DEFAULTS}
        try:
            eff = thresholds(clean)
        except (TypeError, ValueError):
            return Response({'detail': 'Seuils invalides.'}, status=status.HTTP_400_BAD_REQUEST)
        DDSettings.objects.update_or_create(cooperative=coop, defaults={'thresholds': clean})
        return Response({'defaults': DEFAULTS, 'thresholds': eff})


class AnalysisListView(APIView):
    permission_classes = PERMS

    def get(self, request):
        services.mark_stale()
        coop = resolve_cooperative(request)
        qs = DDAnalysis.objects.select_related('cooperative', 'lot', 'created_by').defer('results_gz')
        qs = qs.filter(cooperative=coop) if coop else scope_to_cooperative(qs, request.user)
        return Response([_row(a) for a in qs[:100]])

    def post(self, request):
        from apps.lots.models import Lot
        from eudr_evidence.config import thresholds
        coop = resolve_cooperative(request, request.data)
        if coop is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
        if DDAnalysis.objects.filter(cooperative=coop, status__in=('pending', 'running')).exists():
            services.mark_stale()
            if DDAnalysis.objects.filter(cooperative=coop, status__in=('pending', 'running')).exists():
                return Response({'detail': 'Une analyse est déjà en cours pour cette coopérative.'}, status=status.HTTP_409_CONFLICT)
        lot = None
        if request.data.get('lot'):
            lot = Lot.objects.filter(pk=request.data['lot'], cooperative=coop).first()
            if lot is None:
                return Response({'detail': 'Lot introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        source = request.data.get('source') if request.data.get('source') in ('parcels', 'legacy', 'both') else 'both'
        saved = DDSettings.objects.filter(cooperative=coop).values_list('thresholds', flat=True).first() or {}
        try:
            t = thresholds({**saved, **(request.data.get('thresholds') or {})})
        except (TypeError, ValueError):
            return Response({'detail': 'Seuils invalides.'}, status=status.HTTP_400_BAD_REQUEST)
        meta = request.data.get('report_meta') if isinstance(request.data.get('report_meta'), dict) else {}
        a = DDAnalysis.objects.create(cooperative=coop, lot=lot, created_by=request.user, thresholds=t, report_meta=meta,
                                      scope={'source': source, 'label': f'Lot {lot.code}' if lot else coop.name})
        ActivityLog.objects.create(user=request.user, action='run_tool', resource='due_diligence', resource_id=str(a.pk),
                                   details=f'Analyse DD — {a.scope["label"]}', ip_address=request.META.get('REMOTE_ADDR'))
        transaction.on_commit(lambda: services.start(a.pk))
        return Response(_row(a), status=status.HTTP_201_CREATED)


def _get(request, pk):
    qs = scope_to_cooperative(DDAnalysis.objects.select_related('cooperative', 'lot', 'created_by'), request.user)
    return qs.filter(pk=pk).first()


class AnalysisDetailView(APIView):
    permission_classes = PERMS

    def get(self, request, pk):
        a = _get(request, pk)
        if a is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(_row(a, with_results=True))


class AnalysisStatusView(APIView):
    permission_classes = PERMS

    def get(self, request, pk):
        services.mark_stale()
        a = _get(request, pk)
        if a is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response({'status': a.status, 'progress': a.progress, 'total': a.total, 'error': a.error,
                         'whisp_status': a.whisp_status})


class DecisionView(APIView):
    permission_classes = PERMS

    def post(self, request, pk):
        a = _get(request, pk)
        if a is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        d = request.data
        if d.get('decision') not in DDDecision.DECISIONS or not d.get('parcel_key'):
            return Response({'detail': 'Décision ou parcelle manquante.'}, status=status.HTTP_400_BAD_REQUEST)
        data = d.get('attachment_data') or ''
        if len(data) > MAX_ATTACHMENT * 4 / 3:
            return Response({'detail': 'Pièce jointe trop lourde (3 Mo maximum).'}, status=status.HTTP_400_BAD_REQUEST)
        x = DDDecision.objects.create(analysis=a, parcel_key=str(d['parcel_key'])[:80], decision=d['decision'],
                                      comment=str(d.get('comment') or '')[:5000], author=request.user,
                                      author_name=request.user.full_name or request.user.username,
                                      attachment_name=str(d.get('attachment_name') or '')[:255],
                                      attachment_type=str(d.get('attachment_type') or '')[:100], attachment_data=data)
        return Response(_decision(x), status=status.HTTP_201_CREATED)


class WhispExportView(APIView):
    permission_classes = PERMS

    def get(self, request, pk):
        from django.http import HttpResponse
        from eudr_evidence import whisp_io
        a = _get(request, pk)
        if a is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        feats = services.load_features(a)
        resp = HttpResponse(whisp_io.export_geojson(feats), content_type='application/geo+json')
        resp['Content-Disposition'] = f'attachment; filename="whisp_{a.cooperative.name[:30]}_{a.created_at:%Y%m%d}.geojson"'
        return resp


class WhispImportView(APIView):
    permission_classes = PERMS

    def post(self, request, pk):
        from eudr_evidence import whisp_io
        from eudr_evidence.classify import summarize
        a = _get(request, pk)
        if a is None or a.status != 'done':
            return Response({'detail': 'Analyse introuvable ou pas encore terminée.'}, status=status.HTTP_400_BAD_REQUEST)
        text = request.data.get('csv') or ''
        if not text.strip():
            return Response({'detail': 'Fichier CSV vide.'}, status=status.HTTP_400_BAD_REQUEST)
        rows = whisp_io.import_csv(text)
        results = whisp_io.apply(a.get_results(), rows)
        matched = sum(1 for r in results if r.get('whisp_risk'))
        a.set_results(results)
        summary = summarize(results)
        summary.update({k: v for k, v in (a.summary or {}).items() if k not in summary})
        a.summary = summary
        a.whisp_status = 'importe'
        a.whisp_version = str(request.data.get('version') or 'whisp.earthmap.org')[:40]
        a.manifest = {**(a.manifest or {}), 'whisp': {'executed': True, 'version': a.whisp_version,
                                                      'mode': 'import CSV whisp.earthmap.org', 'matched': matched}}
        a.save()
        return Response({'matched': matched, 'rows': len(rows), 'summary': summary})
