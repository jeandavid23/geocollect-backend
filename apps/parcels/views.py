import csv
import json
from django.http import HttpResponse
from rest_framework import generics, filters, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend

from .models import Parcel
from .serializers import ParcelSerializer, ParcelCreateSerializer, ParcelListSerializer, ParcelGeoJSONSerializer
from apps.accounts.permissions import IsAgentOrAbove, IsCooperativeOrAdmin


class ParcelListCreateView(generics.ListCreateAPIView):
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['cooperative', 'producer', 'agent', 'eudr_status', 'status', 'section', 'village', 'culture']
    search_fields = ['field_id', 'village', 'section', 'producer__last_name']
    ordering_fields = ['created_at', 'area_hectares', 'eudr_score']

    def get_serializer_class(self):
        return ParcelCreateSerializer if self.request.method == 'POST' else ParcelListSerializer

    def get_permissions(self):
        return [IsAgentOrAbove()]

    def get_queryset(self):
        qs = Parcel.objects.select_related('producer', 'cooperative', 'agent__user')
        user = self.request.user
        if user.role == 'cooperative':
            qs = qs.filter(cooperative=user.cooperative)
        elif user.role == 'agent':
            agent = getattr(user, 'agent_profile', None)
            if agent:
                qs = qs.filter(agent=agent)
        return qs


class ParcelDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Parcel.objects.select_related('producer', 'cooperative', 'agent__user')
    permission_classes = [IsAgentOrAbove]

    def get_serializer_class(self):
        return ParcelCreateSerializer if self.request.method in ('PUT', 'PATCH') else ParcelSerializer


class ParcelValidateView(APIView):
    """Re-run EUDR validation on an existing parcel."""
    permission_classes = [IsCooperativeOrAdmin]

    def post(self, request, pk):
        parcel = generics.get_object_or_404(Parcel, pk=pk)
        result = parcel.run_eudr_validation()
        return Response({
            'eudr_score': parcel.eudr_score,
            'eudr_status': parcel.eudr_status,
            'validation_result': result,
        })


class ParcelGeoJSONView(APIView):
    """Returns all parcels as a GeoJSON FeatureCollection."""
    permission_classes = [IsAgentOrAbove]

    def get(self, request):
        qs = Parcel.objects.select_related('producer')
        user = request.user
        if user.role == 'cooperative':
            qs = qs.filter(cooperative=user.cooperative)
        elif user.role == 'agent':
            agent = getattr(user, 'agent_profile', None)
            if agent:
                qs = qs.filter(agent=agent)

        features = [ParcelGeoJSONSerializer(p).data for p in qs]
        return Response({'type': 'FeatureCollection', 'features': features})


class ParcelExportCSVView(APIView):
    """Export parcels as CSV."""
    permission_classes = [IsCooperativeOrAdmin]

    def get(self, request):
        qs = Parcel.objects.select_related('producer', 'agent__user')
        user = request.user
        if user.role == 'cooperative':
            qs = qs.filter(cooperative=user.cooperative)

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="parcelles_eudr.csv"'
        response.write('﻿')  # BOM for Excel

        writer = csv.writer(response)
        writer.writerow([
            'FIELD ID', 'Producteur', 'Village', 'Section', 'Culture',
            'Superficie (ha)', 'Périmètre (m)', 'Score EUDR (%)',
            'Statut EUDR', 'Statut', 'Agent', 'Date création',
        ])
        for p in qs:
            writer.writerow([
                p.field_id,
                p.producer.full_name,
                p.village,
                p.section,
                p.culture,
                p.area_hectares,
                p.perimeter_meters,
                p.eudr_score or '',
                p.get_eudr_status_display(),
                p.get_status_display(),
                p.agent.user.full_name if p.agent else '',
                p.created_at.strftime('%Y-%m-%d %H:%M'),
            ])
        return response


class SyncParcelsView(APIView):
    """Batch sync parcels created offline."""
    permission_classes = [IsAgentOrAbove]

    def post(self, request):
        parcels_data = request.data.get('parcels', [])
        created = []
        errors = []

        for item in parcels_data:
            serializer = ParcelCreateSerializer(data=item)
            if serializer.is_valid():
                parcel = serializer.save()
                created.append(str(parcel.id))
            else:
                errors.append({'data': item, 'errors': serializer.errors})

        return Response({
            'created': len(created),
            'errors': len(errors),
            'created_ids': created,
            'error_details': errors,
        }, status=status.HTTP_207_MULTI_STATUS)
