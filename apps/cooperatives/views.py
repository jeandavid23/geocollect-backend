from rest_framework import generics, permissions, filters
from django_filters.rest_framework import DjangoFilterBackend
from .models import Cooperative
from .serializers import CooperativeSerializer, CooperativeListSerializer, CooperativeCreateSerializer
from apps.accounts.permissions import IsSuperAdmin, IsCooperativeOrAdmin, BelongsToCooperative


class CooperativeListCreateView(generics.ListCreateAPIView):
    queryset = Cooperative.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['is_active', 'region', 'country']
    search_fields = ['name', 'rccm', 'region']

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return CooperativeListSerializer
        return CooperativeCreateSerializer

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsSuperAdmin()]
        return [IsCooperativeOrAdmin()]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.role == 'cooperative':
            qs = qs.filter(id=user.cooperative_id)
        return qs


class CooperativeDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Cooperative.objects.all()
    serializer_class = CooperativeSerializer

    def get_permissions(self):
        if self.request.method in ('PUT', 'PATCH', 'DELETE'):
            return [IsSuperAdmin()]
        return [IsCooperativeOrAdmin()]
