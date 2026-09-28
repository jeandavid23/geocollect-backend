from django.db import transaction
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsCooperativeOrAdmin, resolve_cooperative
from .models import RegistrySheet
from .serializers import RegistrySheetSerializer


def _scoped(request):
    qs = RegistrySheet.objects.select_related('updated_by')
    if request.user.role == 'cooperative':
        return qs.filter(cooperative_id=request.user.cooperative_id)
    coop_id = request.query_params.get('cooperative')
    return qs.filter(cooperative_id=coop_id) if coop_id else qs.none()


class RegistrySheetListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/v1/registry/sheets/[?cooperative=<uuid> pour le super admin] → feuilles avec leurs cellules
    POST /api/v1/registry/sheets/  {name, data, col_widths, position}   → nouvelle feuille
    """
    serializer_class = RegistrySheetSerializer
    permission_classes = [IsCooperativeOrAdmin]
    pagination_class = None

    def get_queryset(self):
        return _scoped(self.request)

    def perform_create(self, serializer):
        cooperative = resolve_cooperative(self.request, self.request.data)
        if cooperative is None:
            raise ValidationError({'cooperative': 'Coopérative requise.'})
        if RegistrySheet.objects.filter(cooperative=cooperative, name=serializer.validated_data['name']).exists():
            raise ValidationError({'name': 'Une feuille porte déjà ce nom.'})
        serializer.save(cooperative=cooperative, updated_by=self.request.user)


class RegistrySheetDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET / PATCH / DELETE /api/v1/registry/sheets/<uuid>/"""
    serializer_class = RegistrySheetSerializer
    permission_classes = [IsCooperativeOrAdmin]

    def get_queryset(self):
        qs = RegistrySheet.objects.select_related('updated_by')
        if self.request.user.role == 'cooperative':
            qs = qs.filter(cooperative_id=self.request.user.cooperative_id)
        return qs

    def perform_update(self, serializer):
        name = serializer.validated_data.get('name')
        sheet = serializer.instance
        if name and name != sheet.name and RegistrySheet.objects.filter(
                cooperative=sheet.cooperative, name=name).exists():
            raise ValidationError({'name': 'Une feuille porte déjà ce nom.'})
        serializer.save(updated_by=self.request.user)


class RegistryImportView(APIView):
    """
    POST /api/v1/registry/import/
    Corps : {"cooperative": "<uuid, super admin>", "source_file": "registre.xlsx",
             "mode": "replace" | "merge", "sheets": [{name, data, col_widths}]}
    - replace : le registre est entièrement remplacé par le classeur importé ;
    - merge   : les feuilles de même nom sont remplacées, les autres sont conservées.
    """
    permission_classes = [IsCooperativeOrAdmin]

    def post(self, request):
        cooperative = resolve_cooperative(request, request.data)
        if cooperative is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
        sheets = request.data.get('sheets') or []
        mode = request.data.get('mode', 'merge')
        source = str(request.data.get('source_file') or '')[:255]
        if not isinstance(sheets, list) or not sheets:
            return Response({'detail': 'Aucune feuille à importer.'}, status=status.HTTP_400_BAD_REQUEST)

        validated = []
        for i, sheet in enumerate(sheets):
            s = RegistrySheetSerializer(data={**(sheet or {}), 'source_file': source})
            if not s.is_valid():
                return Response({'detail': f'Feuille {i + 1} invalide.', 'errors': s.errors},
                                status=status.HTTP_400_BAD_REQUEST)
            validated.append(s.validated_data)

        names = [v['name'] for v in validated]
        if len(set(names)) != len(names):
            return Response({'detail': 'Deux feuilles portent le même nom.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            existing = RegistrySheet.objects.filter(cooperative=cooperative)
            if mode == 'replace':
                existing.delete()
                start = 0
            else:
                existing.filter(name__in=names).delete()
                last = existing.order_by('-position').first()
                start = (last.position + 1) if last else 0
            for i, v in enumerate(validated):
                RegistrySheet.objects.create(
                    cooperative=cooperative, name=v['name'], position=start + i,
                    data=v.get('data', []), col_widths=v.get('col_widths', []),
                    source_file=source, updated_by=request.user,
                )

        sheets_qs = RegistrySheet.objects.select_related('updated_by').filter(cooperative=cooperative)
        return Response(RegistrySheetSerializer(sheets_qs, many=True).data, status=status.HTTP_201_CREATED)
