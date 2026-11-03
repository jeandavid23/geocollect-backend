"""
Opérations d'une usine — /api/v1/lots/plant/
  stock/                     GET : en route, cacao reçu non usiné, produits finis non expédiés (ancienneté)
  dashboard/                 GET : réceptions, qualité, écarts, rendements, expéditions sur 12 mois
  shipments/                 GET / POST : expéditions sortantes {ship_date, client, items: [{batch, product, weight_kg}]…}
  shipments/shippable/       GET : produits finis disponibles à l'expédition
  shipments/<id>/            GET / PATCH (documents, {"status": "expediee"} avec référence DDS obligatoire)
  shipments/<id>/trace/      GET : chaîne complète jusqu'aux producteurs et parcelles
Compte usine : son usine. Super admin / propriétaire / coopérative : ?plant=<id> (une usine visible), en lecture ;
la coopérative n'y voit que ce qui concerne ses propres lots (voir plant_ops.scope).
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import ActivityLog
from apps.accounts.permissions import IsCooperativeAdminOrPlant, module_required
from . import plant_ops, processing as pr, shipments as sh
from .views_plants import visible_plants

PERMS = [IsCooperativeAdminOrPlant, module_required('lots')]


def _plant(request):
    """L'usine concernée : celle du compte usine, sinon ?plant= parmi les usines visibles (administrateur, coopérative)."""
    user = request.user
    if user.role == 'usine':
        return user.plant
    body = request.data if isinstance(request.data, dict) else {}
    pid = request.query_params.get('plant') or body.get('plant')
    return visible_plants(user).filter(pk=pid).first() if pid and _uuid_ok(pid) else None


def _uuid_ok(value):
    try:
        pr._uuid(value)
        return True
    except pr.ProcessingError:
        return False


def _no_plant():
    return Response({'detail': 'Usine introuvable (indiquez ?plant=).'}, status=status.HTTP_404_NOT_FOUND)


def _manager_only(request):
    """Expéditions : saisies par le responsable d'usine (ou le super admin) ; consultation pour les autres."""
    if plant_ops.is_operator(request.user) or request.user.role == 'cooperative':
        return Response({'detail': "Réservé au responsable d'usine."}, status=status.HTTP_403_FORBIDDEN)
    return None


class PlantStockView(APIView):
    permission_classes = PERMS

    def get(self, request):
        plant = _plant(request)
        return Response(plant_ops.stock(plant, request.user)) if plant else _no_plant()


class PlantDashboardView(APIView):
    permission_classes = PERMS

    def get(self, request):
        plant = _plant(request)
        return Response(plant_ops.dashboard(plant, request.user)) if plant else _no_plant()


def _shipments(request):
    return sh.visible_shipments(request.user).select_related('plant', 'created_by').prefetch_related('items__batch')


class ShipmentListView(APIView):
    permission_classes = PERMS

    def get(self, request):
        qs = _shipments(request)
        if request.query_params.get('plant') and _uuid_ok(request.query_params['plant']):
            qs = qs.filter(plant_id=request.query_params['plant'])
        return Response([sh.serialize(s) for s in qs[:500]])

    def post(self, request):
        denied = _manager_only(request)
        if denied:
            return denied
        plant = _plant(request)
        if plant is None:
            return _no_plant()
        try:
            ship = sh.create(request.user, plant, request.data)
        except pr.ProcessingError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        ActivityLog.objects.create(user=request.user, action='create_shipment', resource='shipment',
                                   resource_id=str(ship.id), details=ship.number)
        return Response(sh.serialize(_shipments(request).get(pk=ship.pk)), status=status.HTTP_201_CREATED)


class ShippableView(APIView):
    permission_classes = PERMS

    def get(self, request):
        denied = _manager_only(request)
        if denied:
            return denied
        plant = _plant(request)
        return Response(sh.shippable(plant)) if plant else _no_plant()


class ShipmentDetailView(APIView):
    permission_classes = PERMS

    def _get(self, request, pk):
        return _shipments(request).filter(pk=pk).first()

    def get(self, request, pk):
        ship = self._get(request, pk)
        if ship is None:
            return Response({'detail': 'Expédition introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(sh.serialize(ship))

    def patch(self, request, pk):
        denied = _manager_only(request)
        if denied:
            return denied
        ship = self._get(request, pk)
        if ship is None:
            return Response({'detail': 'Expédition introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            ship = sh.update(ship, request.data)
        except pr.ProcessingError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        ActivityLog.objects.create(user=request.user, action='update_shipment', resource='shipment',
                                   resource_id=str(ship.id), details=f'{ship.number} — {ship.status}')
        return Response(sh.serialize(self._get(request, pk)))


class ShipmentTraceView(APIView):
    permission_classes = PERMS

    def get(self, request, pk):
        ship = _shipments(request).filter(pk=pk).first()
        if ship is None:
            return Response({'detail': 'Expédition introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        batches = list({i.batch_id: i.batch for i in ship.items.all()}.values())
        return Response(pr.trace_many(batches))
