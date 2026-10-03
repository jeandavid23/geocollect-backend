from rest_framework import generics, filters
from rest_framework.exceptions import PermissionDenied, ValidationError
from django_filters.rest_framework import DjangoFilterBackend
from .models import Cooperative
from .serializers import CooperativeSerializer, CooperativeListSerializer, CooperativeCreateSerializer
from apps.accounts.permissions import IsSuperAdmin, IsCooperativeOrAdmin, scope_to_cooperative, is_owner
from apps.accounts.tenancy import invalidate_tenant_cache


def check_cooperative_quota(admin):
    """Refuse la création si le super admin a atteint son nombre maximum de coopératives."""
    try:
        lic = admin.admin_license
    except Exception:  # noqa: BLE001 — super admin sans licence : illimité
        return
    if lic.max_cooperatives is not None and admin.managed_cooperatives.count() >= lic.max_cooperatives:
        raise ValidationError({'detail': f'Limite atteinte : votre abonnement autorise {lic.max_cooperatives} '
                                         'coopérative(s). Contactez GeoLab Service pour l’augmenter.'})


class CooperativeListCreateView(generics.ListCreateAPIView):
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['is_active', 'region', 'country', 'managed_by']
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
        # propriétaire → toutes ; super admin → les siennes ; coopérative → la sienne
        qs = Cooperative.objects.select_related('managed_by').order_by('name')
        return scope_to_cooperative(qs, self.request.user, field='id')

    def perform_create(self, serializer):
        user = self.request.user
        if is_owner(user):
            # le propriétaire choisit le super admin gestionnaire (ou aucun : gérée par lui-même)
            admin = serializer.validated_data.get('managed_by')
        else:
            admin = user
        if admin is not None:
            check_cooperative_quota(admin)
        serializer.save(managed_by=admin)
        invalidate_tenant_cache()


class CooperativeDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CooperativeSerializer

    def get_queryset(self):
        return scope_to_cooperative(Cooperative.objects.select_related('managed_by'), self.request.user, field='id')

    def get_permissions(self):
        if self.request.method in ('PUT', 'PATCH', 'DELETE'):
            return [IsSuperAdmin()]
        return [IsCooperativeOrAdmin()]

    def perform_update(self, serializer):
        serializer.save()
        invalidate_tenant_cache()   # activer / désactiver une coopérative bloque ou rouvre ses comptes

    def perform_destroy(self, instance):
        if instance.producers.exists() or instance.parcels.exists():
            raise PermissionDenied('Cette coopérative contient des producteurs ou des parcelles : '
                                   'désactivez-la plutôt que de la supprimer.')
        instance.delete()
