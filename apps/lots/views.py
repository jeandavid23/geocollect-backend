"""
Fiches de lot — /api/v1/lots/
  GET/POST            liste / création (coopérative ; super admin et propriétaire avec ?cooperative=)
  GET/PATCH/DELETE    <id>/  détail avec contrôles EUDR et parcelles d'origine ; suppression des brouillons seulement
Un lot ne peut passer « Validé » que si tous les contrôles bloquants sont levés.
"""
from collections import defaultdict

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import ActivityLog
from apps.accounts.permissions import IsCooperativeOrAdmin, module_required, resolve_cooperative, scope_to_cooperative
from apps.parcels.models import Parcel
from .models import Lot
from .serializers import LotSerializer

MAX_YIELD = {'cacao': 1500, 'cafe': 2500, 'autre': 10 ** 9}   # kg/ha : au-delà, rendement anormal (risque de mélange)


def checks(lot):
    """Contrôles de conformité du lot et parcelles d'origine."""
    lines = list(lot.lines.select_related('producer'))
    pids = [l.producer_id for l in lines]
    parcels = list(Parcel.objects.filter(producer_id__in=pids, cooperative=lot.cooperative)
                   .values('id', 'producer_id', 'field_id', 'village', 'area_hectares', 'eudr_status', 'eudr_score', 'geometry'))
    by_prod = defaultdict(list)
    for p in parcels:
        by_prod[p['producer_id']].append(p)
    weight = defaultdict(float)
    for l in lines:
        weight[l.producer_id] += float(l.weight_kg)

    issues, producers = [], []
    for pid in dict.fromkeys(pids):
        line = next(l for l in lines if l.producer_id == pid)
        ps = by_prod.get(pid, [])
        area = sum(p['area_hectares'] or 0 for p in ps)
        bad = [p['field_id'] for p in ps if p['eudr_status'] == 'non_compliant']
        pending = [p['field_id'] for p in ps if p['eudr_status'] not in ('compliant', 'non_compliant')]
        yld = weight[pid] / area if area else None
        name = line.producer.full_name
        if not ps:
            issues.append({'level': 'bloquant', 'producer': name, 'message': 'Aucune parcelle cartographiée : origine non géolocalisée.'})
        if bad:
            issues.append({'level': 'bloquant', 'producer': name, 'message': f'Parcelle(s) non conforme(s) EUDR : {", ".join(bad)}.'})
        if pending:
            issues.append({'level': 'attention', 'producer': name, 'message': f'Parcelle(s) en attente de validation : {", ".join(pending)}.'})
        if yld is not None and yld > MAX_YIELD.get(lot.product, 10 ** 9):
            issues.append({'level': 'attention', 'producer': name,
                           'message': f'Rendement anormal : {yld:,.0f} kg/ha déclarés sur {area:.2f} ha (risque de mélange avec un autre cacao).'.replace(',', ' ')})
        producers.append({'producer': str(pid), 'name': name, 'code': line.producer.field_id_base, 'weight_kg': round(weight[pid], 1),
                          'parcels': len(ps), 'area_ha': round(area, 2), 'yield_kg_ha': round(yld) if yld else None,
                          'status': 'bloquant' if (not ps or bad) else 'attention' if pending else 'ok'})
    net = sum(weight.values())
    if lot.gross_weight_kg and abs(float(lot.gross_weight_kg) - net) > max(1.0, 0.02 * net):
        issues.append({'level': 'attention', 'producer': '', 'message': f'Poids du lot ({float(lot.gross_weight_kg):,.1f} kg) différent de la somme des livraisons ({net:,.1f} kg).'.replace(',', ' ')})
    line_bags = sum(l.bags for l in lines)
    if lot.bags and line_bags and lot.bags != line_bags:
        issues.append({'level': 'attention', 'producer': '', 'message': f'{lot.bags} sacs déclarés pour le lot, {line_bags} dans les livraisons.'})
    return {
        'blocking': sum(1 for i in issues if i['level'] == 'bloquant'), 'warnings': sum(1 for i in issues if i['level'] == 'attention'),
        'issues': issues, 'producers': producers, 'net_weight_kg': round(net, 1),
        'parcels': [{**p, 'id': str(p['id']), 'producer_id': str(p['producer_id'])} for p in parcels],
        'total_area_ha': round(sum(p['area_hectares'] or 0 for p in parcels), 2),
    }


class LotListView(APIView):
    permission_classes = [IsCooperativeOrAdmin, module_required('lots')]

    def get(self, request):
        qs = scope_to_cooperative(Lot.objects.select_related('cooperative', 'created_by').prefetch_related('lines__producer'), request.user)
        if request.query_params.get('cooperative'):
            qs = qs.filter(cooperative_id=request.query_params['cooperative'])
        return Response(LotSerializer(qs[:500], many=True).data)

    def post(self, request):
        coop = resolve_cooperative(request, request.data)
        if coop is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
        s = LotSerializer(data=request.data, context={'request': request, 'cooperative': coop})
        s.is_valid(raise_exception=True)
        lot = s.save()
        ActivityLog.objects.create(user=request.user, action='create_lot', resource='lot', resource_id=str(lot.id), details=lot.code)
        return Response({**LotSerializer(lot).data, 'checks': checks(lot)}, status=status.HTTP_201_CREATED)


class LotDetailView(APIView):
    permission_classes = [IsCooperativeOrAdmin, module_required('lots')]

    def _get(self, request, pk):
        return scope_to_cooperative(Lot.objects.select_related('cooperative', 'created_by'), request.user).filter(pk=pk).first()

    def get(self, request, pk):
        lot = self._get(request, pk)
        if lot is None:
            return Response({'detail': 'Lot introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        return Response({**LotSerializer(lot).data, 'checks': checks(lot)})

    def patch(self, request, pk):
        lot = self._get(request, pk)
        if lot is None:
            return Response({'detail': 'Lot introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        if lot.status == 'expedie' and set(request.data) - {'status', 'notes'}:
            return Response({'detail': 'Lot expédié : seules les notes peuvent encore être modifiées.'}, status=status.HTTP_400_BAD_REQUEST)
        s = LotSerializer(lot, data=request.data, partial=True, context={'request': request, 'cooperative': lot.cooperative})
        s.is_valid(raise_exception=True)
        new_status = s.validated_data.get('status')
        lot = s.save()
        c = checks(lot)
        if new_status in ('valide', 'expedie') and c['blocking']:
            lot.status = 'brouillon'
            lot.save(update_fields=['status'])
            return Response({'detail': f'{c["blocking"]} contrôle(s) bloquant(s) : le lot reste en brouillon.', **LotSerializer(lot).data, 'checks': c},
                            status=status.HTTP_400_BAD_REQUEST)
        if new_status:
            ActivityLog.objects.create(user=request.user, action=f'lot_{new_status}', resource='lot', resource_id=str(lot.id), details=lot.code)
        return Response({**LotSerializer(lot).data, 'checks': c})

    def delete(self, request, pk):
        lot = self._get(request, pk)
        if lot is None:
            return Response({'detail': 'Lot introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        if lot.status != 'brouillon':
            return Response({'detail': 'Seul un lot en brouillon peut être supprimé.'}, status=status.HTTP_400_BAD_REQUEST)
        lot.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
