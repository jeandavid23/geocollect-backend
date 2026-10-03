from django.db import transaction
from django.db.models import Count, Sum
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAgentOrAbove, IsCooperativeOrAdmin, resolve_cooperative, scope_to_cooperative, module_required
from apps.accounts.tenancy import has_module
from .models import LegacyParcel

GEOMETRY_TYPES = {'Polygon', 'MultiPolygon'}
MAX_FEATURES = 5000  # par envoi : le navigateur découpe les gros fichiers en plusieurs lots


def _scoped(request):
    """Polygones des coopératives accessibles (et module « anciens polygones » activé) ; ?cooperative=<uuid> pour filtrer."""
    user = request.user
    if not has_module(user, 'legacy'):
        return LegacyParcel.objects.none()
    qs = scope_to_cooperative(LegacyParcel.objects.all(), user)
    coop_id = request.query_params.get('cooperative')
    if coop_id and user.role not in ('cooperative', 'agent'):
        try:
            qs = qs.filter(cooperative_id=coop_id)
        except Exception:  # noqa: BLE001
            return LegacyParcel.objects.none()
    return qs


def _serialize(p):
    return {
        'id': str(p.id),
        'cooperative': str(p.cooperative_id),
        'name': p.name,
        'geometry': p.geometry,
        'properties': p.properties,
        'area_hectares': p.area_hectares,
        'source_file': p.source_file,
        'created_at': p.created_at.isoformat(),
    }


class LegacyParcelListView(APIView):
    """
    GET    /api/v1/parcels/legacy/                     → tous les anciens polygones visibles
    DELETE /api/v1/parcels/legacy/?source_file=<nom>   → supprime un import (coopérative / admin)
    """

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsCooperativeOrAdmin(), module_required('legacy')()]
        return [IsAgentOrAbove()]

    def get(self, request):
        qs = _scoped(request).order_by('source_file', 'name', 'id')
        if 'page' not in request.query_params:
            return Response([_serialize(p) for p in qs])
        # Pagination (?page=&page_size=, 2000 max) : plus de 10 000 polygones sans réponse géante
        try:
            page = max(1, int(request.query_params.get('page', 1)))
            size = min(2000, max(1, int(request.query_params.get('page_size', 1000))))
        except ValueError:
            return Response({'detail': 'Pagination invalide.'}, status=status.HTTP_400_BAD_REQUEST)
        total = qs.count()
        start = (page - 1) * size
        return Response({
            'count': total,
            'next': page + 1 if start + size < total else None,
            'results': [_serialize(p) for p in qs[start:start + size]],
        })

    def delete(self, request):
        source = request.query_params.get('source_file')
        if not source:
            return Response({'detail': 'Paramètre source_file requis.'}, status=status.HTTP_400_BAD_REQUEST)
        deleted, _ = _scoped(request).filter(source_file=source).delete()
        return Response({'deleted': deleted})


class LegacyParcelImportView(APIView):
    """
    POST /api/v1/parcels/legacy/import/
    Corps : {"cooperative": "<uuid, super admin>", "source_file": "anciens.kml",
             "replace": true, "features": [{"name", "geometry", "properties", "area_hectares"}]}
    Les géométries doivent déjà être en WGS84 (conversion faite dans le navigateur).
    """
    permission_classes = [IsCooperativeOrAdmin, module_required('legacy')]

    def post(self, request):
        cooperative = resolve_cooperative(request, request.data)
        if cooperative is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)

        features = request.data.get('features') or []
        source = str(request.data.get('source_file') or '')[:255]
        if not isinstance(features, list) or not features:
            return Response({'detail': 'Aucun polygone à importer.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(features) > MAX_FEATURES:
            return Response({'detail': f'Maximum {MAX_FEATURES} polygones par import.'},
                            status=status.HTTP_400_BAD_REQUEST)

        objs, skipped = [], 0
        for f in features:
            geom = (f or {}).get('geometry') or {}
            if geom.get('type') not in GEOMETRY_TYPES or not geom.get('coordinates'):
                skipped += 1
                continue
            area = f.get('area_hectares')
            props = f.get('properties')
            objs.append(LegacyParcel(
                cooperative=cooperative,
                name=str(f.get('name') or '')[:255],
                geometry=geom,
                properties=props if isinstance(props, dict) else {},
                area_hectares=float(area) if isinstance(area, (int, float)) else None,
                source_file=source,
                uploaded_by=request.user,
            ))

        with transaction.atomic():
            if request.data.get('replace') and source:
                LegacyParcel.objects.filter(cooperative=cooperative, source_file=source).delete()
            LegacyParcel.objects.bulk_create(objs, batch_size=1000)

        # une seule notification par fichier : au premier lot (replace) uniquement
        if not request.data.get('notify', True):
            return Response({'created': len(objs), 'skipped': skipped}, status=status.HTTP_201_CREATED)
        try:
            from apps.accounts.notify import notify_cooperative
            notify_cooperative(
                cooperative, ntype='info',
                title=f'Anciens polygones importés — {len(objs)}',
                message=f'Fichier {source or "—"}' + (f' · {skipped} entité(s) ignorée(s) (non polygonales)' if skipped else ''),
            )
        except Exception:
            pass

        return Response({'created': len(objs), 'skipped': skipped}, status=status.HTTP_201_CREATED)


class LegacyParcelSourcesView(APIView):
    """GET /api/v1/parcels/legacy/sources/ → imports réalisés (fichier, nombre, surface)."""
    permission_classes = [IsAgentOrAbove]

    def get(self, request):
        rows = (_scoped(request).values('cooperative', 'source_file')
                .annotate(count=Count('id'), area=Sum('area_hectares')).order_by('source_file'))
        return Response([
            {'cooperative': str(r['cooperative']), 'source_file': r['source_file'],
             'count': r['count'], 'area_hectares': round(r['area'] or 0, 2)}
            for r in rows
        ])
