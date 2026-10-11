from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsCooperativeOrAdmin, is_owner, resolve_cooperative
from . import spatial


class OverlapView(APIView):
    """
    GET /api/v1/parcels/spatial/overlaps/?cooperative=&other=true&min_ha=0.01
    Chevauchements des parcelles (mappées + anciens polygones) de la coopérative, entre elles et avec
    les parcelles des autres coopératives (anonymisées, sauf pour le propriétaire de la plateforme).
    """
    permission_classes = [IsCooperativeOrAdmin]

    def get(self, request):
        coop = resolve_cooperative(request)
        if coop is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            min_ha = max(0.0, float(request.query_params.get('min_ha', 0.01)))
        except ValueError:
            min_ha = 0.01
        other = request.query_params.get('other', 'true') != 'false'
        summary, rows = spatial.overlaps(coop, include_other=other, min_ha=min_ha, reveal_other=is_owner(request.user))
        return Response({'cooperative': coop.name, 'min_ha': min_ha, 'summary': summary, 'results': rows})


class ZoneCheckView(APIView):
    """GET /api/v1/parcels/spatial/zones/?cooperative= : parcelles situées dans une forêt classée, un parc, une enclave…"""
    permission_classes = [IsCooperativeOrAdmin]

    def get(self, request):
        coop = resolve_cooperative(request)
        if coop is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
        summary, rows = spatial.zones(coop)
        return Response({'cooperative': coop.name, 'summary': summary, 'results': rows})


class SpatialStatusView(APIView):
    """GET /api/v1/parcels/spatial/status/ : moteur de calcul utilisé (PostGIS ou Python)."""
    permission_classes = [IsCooperativeOrAdmin]

    def get(self, request):
        from .models import LandZone
        return Response({'postgis': spatial.postgis_ready(), 'land_zones': LandZone.objects.count()})
