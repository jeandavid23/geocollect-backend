"""
Usinage et transformation — /api/v1/lots/processing/
  GET / POST             lots d'usinage visibles / création {operation, plant, process_date, inputs, outputs, notes}
                         inputs : [{"lot": id, "weight_kg"}] ou [{"batch": id d'un usinage, "weight_kg"}]
                         outputs : [{"product", "weight_kg", "reference"}]
  GET / PATCH <id>/      détail ; clôture {"status": "cloture"} ou notes
  GET <id>/trace/        remontée complète jusqu'aux producteurs, pesées et parcelles
  GET available/         lots expédiés et produits d'usinage encore disponibles
Compte « usine » : seulement ce qui est adressé à son usine (voir processing.usable_lots).
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import ActivityLog
from apps.accounts.permissions import IsCooperativeAdminOrPlant, module_required
from . import plant_ops, processing as pr
from .models import ProcessingOutput

PERMS = [IsCooperativeAdminOrPlant, module_required('lots')]


def serialize(b):
    return {
        'id': str(b.id), 'code': b.code, 'operation': b.operation, 'plant': b.plant, 'process_date': b.process_date,
        'notes': b.notes, 'status': b.status, 'created_at': b.created_at,
        'created_by_name': b.created_by.full_name if b.created_by else '',
        'cooperatives': [c.name for c in b.cooperatives.all()],
        'inputs': [{'lot': str(i.lot_id) if i.lot_id else None, 'lot_code': i.lot.code if i.lot_id else None,
                    'batch': str(i.source_batch_id) if i.source_batch_id else None,
                    'batch_code': i.source_batch.code if i.source_batch_id else None,
                    'weight_kg': float(i.weight_kg)} for i in b.inputs.all()],
        'outputs': [{'product': o.product, 'product_label': ProcessingOutput.PRODUCTS.get(o.product, o.product),
                     'weight_kg': float(o.weight_kg), 'reference': o.reference,
                     'byproduct': o.product in ProcessingOutput.BYPRODUCTS} for o in b.outputs.all()],
        **pr.balance(b),
    }


def _query(user):
    return pr.visible_batches(user).select_related('created_by', 'plant_ref').prefetch_related(
        'cooperatives', 'inputs__lot', 'inputs__source_batch', 'outputs')


class ProcessingListView(APIView):
    permission_classes = PERMS

    def get(self, request):
        return Response([serialize(b) for b in _query(request.user)[:500]])

    def post(self, request):
        if plant_ops.is_operator(request.user):
            return Response({'detail': "L'opérateur de pesée réceptionne les camions ; l'usinage est fait par le "
                                       "responsable d'usine."}, status=status.HTTP_403_FORBIDDEN)
        d = request.data
        try:
            batch = pr.create_batch(request.user, d.get('operation', 'usinage'), (d.get('plant') or '').strip(),
                                    d.get('process_date'), d.get('inputs') or [], d.get('outputs') or [], d.get('notes', ''))
        except pr.ProcessingError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        ActivityLog.objects.create(user=request.user, action='create_processing', resource='processing',
                                   resource_id=str(batch.id), details=batch.code)
        batch = _query(request.user).get(pk=batch.pk)
        plant_ops.notify_loss(batch, pr.balance(batch))
        return Response(serialize(batch), status=status.HTTP_201_CREATED)


class ProcessingAvailableView(APIView):
    permission_classes = PERMS

    def get(self, request):
        return Response(pr.available(request.user))


class ProcessingDetailView(APIView):
    permission_classes = PERMS

    def get(self, request, pk):
        b = _query(request.user).filter(pk=pk).first()
        if b is None:
            return Response({'detail': "Lot d'usinage introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(serialize(b))

    def patch(self, request, pk):
        if plant_ops.is_operator(request.user):
            return Response({'detail': "Réservé au responsable d'usine."}, status=status.HTTP_403_FORBIDDEN)
        b = _query(request.user).filter(pk=pk).first()
        if b is None:
            return Response({'detail': "Lot d'usinage introuvable."}, status=status.HTTP_404_NOT_FOUND)
        if 'notes' in request.data:
            b.notes = request.data['notes']
        if request.data.get('status') == 'cloture':
            b.status = 'cloture'
        b.save(update_fields=['notes', 'status'])
        return Response(serialize(b))


class ProcessingTraceView(APIView):
    permission_classes = PERMS

    def get(self, request, pk):
        b = pr.visible_batches(request.user).filter(pk=pk).first()
        if b is None:
            return Response({'detail': "Lot d'usinage introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(pr.trace(b))
