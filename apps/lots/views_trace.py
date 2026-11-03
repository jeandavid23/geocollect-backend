"""
Traçabilité des volumes — /api/v1/lots/
  deliveries/                GET (filtres : campaign, producer, product, status, lot, free=1, q) / POST : pesées
  deliveries/<id>/           GET (aucune suppression : une pesée erronée s'annule avec un motif)
  deliveries/<id>/cancel/    POST {reason}
  volumes/                   GET ?campaign=&product= : volumes par producteur comparés à la capacité des parcelles
  transport/                 GET / POST : fiches d'accompagnement (lot validé obligatoire)
  transport/<id>/            GET / PATCH (réception : poids à l'arrivée ; ou {"status": "annulee", "notes": motif})
Coopérative : la sienne ; super admin et propriétaire : ?cooperative= (comme les lots).
"""
import uuid

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import ActivityLog
from apps.accounts.permissions import (IsCooperativeAdminOrPlant, IsCooperativeOrAdmin, module_required,
                                      resolve_cooperative, scope_to_cooperative)
from utils.pagination import StandardPagination
from . import plant_ops, traceability as tr
from .models import Delivery, TransportSlip
from .serializers_trace import QUALITY_FIELDS, DeliverySerializer, TransportSlipSerializer

PERMS = [IsCooperativeOrAdmin, module_required('lots')]
SLIP_PERMS = [IsCooperativeAdminOrPlant, module_required('lots')]     # l'usine reçoit les fiches qui lui sont adressées
RECEPTION_FIELDS = {'received_weight_kg', 'received_by', 'reception_notes', 'decision', 'decision_reason', *QUALITY_FIELDS}


def pr_uuid(value):
    """Identifiant valide, ou None (un filtre mal formé ne doit pas provoquer d'erreur 500)."""
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _missing_coop():
    return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)


def slips_for(user):
    """Fiches visibles : celles adressées à son usine pour un compte usine, sinon celles de ses coopératives."""
    qs = TransportSlip.objects.select_related('lot', 'cooperative', 'created_by', 'plant')
    if user.role == 'usine':
        return qs.filter(plant_id=user.plant_id) if user.plant_id else qs.none()
    return scope_to_cooperative(qs, user)


def _log(request, action, obj, details=''):
    ActivityLog.objects.create(user=request.user, action=action, resource=obj._meta.model_name,
                               resource_id=str(obj.pk), details=details or obj.number)


class DeliveryListView(APIView):
    permission_classes = PERMS

    def get(self, request):
        qs = scope_to_cooperative(Delivery.objects.select_related('producer', 'lot', 'cooperative', 'created_by'),
                                  request.user)
        p = request.query_params
        for field in ('campaign', 'product', 'status', 'cooperative'):
            if p.get(field):
                qs = qs.filter(**{f'{field}_id' if field == 'cooperative' else field: p[field]})
        if p.get('producer'):
            qs = qs.filter(producer_id=p['producer'])
        if p.get('lot'):
            qs = qs.filter(lot_id=p['lot'])
        if p.get('free') == '1':
            qs = qs.filter(lot__isnull=True, status='valide')
        if p.get('q'):
            q = p['q'].strip()
            qs = qs.filter(Q(number__icontains=q) | Q(producer__last_name__icontains=q)
                           | Q(producer__first_name__icontains=q) | Q(producer__field_id_base__icontains=q))
        pager = StandardPagination()
        page = pager.paginate_queryset(qs, request, view=self)
        return pager.get_paginated_response(DeliverySerializer(page, many=True).data)

    def post(self, request):
        coop = resolve_cooperative(request, request.data)
        if coop is None:
            return _missing_coop()
        s = DeliverySerializer(data=request.data, context={'request': request, 'cooperative': coop})
        s.is_valid(raise_exception=True)
        d = s.save()
        _log(request, 'create_delivery', d, f'{d.number} — {d.net_kg} kg')
        return Response(DeliverySerializer(d).data, status=status.HTTP_201_CREATED)


class DeliveryDetailView(APIView):
    permission_classes = PERMS

    def get(self, request, pk):
        d = scope_to_cooperative(Delivery.objects.select_related('producer', 'lot', 'cooperative'), request.user) \
            .filter(pk=pk).first()
        if d is None:
            return Response({'detail': 'Pesée introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(DeliverySerializer(d).data)


class DeliveryCancelView(APIView):
    permission_classes = PERMS

    def post(self, request, pk):
        d = scope_to_cooperative(Delivery.objects.select_related('lot'), request.user).filter(pk=pk).first()
        if d is None:
            return Response({'detail': 'Pesée introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response({'detail': "Indiquez le motif de l'annulation."}, status=status.HTTP_400_BAD_REQUEST)
        if d.status == 'annulee':
            return Response({'detail': 'Pesée déjà annulée.'}, status=status.HTTP_400_BAD_REQUEST)
        if d.lot_id:
            return Response({'detail': f'Pesée affectée au lot {d.lot.code} : retirez-la du lot avant de l\'annuler.'},
                            status=status.HTTP_400_BAD_REQUEST)
        d.status, d.cancel_reason = 'annulee', reason[:255]
        d.cancelled_by, d.cancelled_at = request.user, timezone.now()
        d.save(update_fields=['status', 'cancel_reason', 'cancelled_by', 'cancelled_at'])
        _log(request, 'cancel_delivery', d, f'{d.number} — {reason}')
        return Response(DeliverySerializer(d).data)


class VolumesView(APIView):
    permission_classes = PERMS

    def get(self, request):
        coop = resolve_cooperative(request)
        if coop is None:
            return _missing_coop()
        campaign = request.query_params.get('campaign')
        if not campaign:
            return Response({'detail': 'Campagne requise (ex. 2026-2027).'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(tr.volumes(coop, campaign, request.query_params.get('product') or 'cacao'))


class TransportListView(APIView):
    permission_classes = SLIP_PERMS

    def get(self, request):
        qs = slips_for(request.user)
        if request.query_params.get('lot'):
            qs = qs.filter(lot_id=request.query_params['lot'])
        if request.query_params.get('cooperative'):
            qs = qs.filter(cooperative_id=request.query_params['cooperative'])
        if request.query_params.get('plant'):
            plant = pr_uuid(request.query_params['plant'])
            qs = qs.filter(plant_id=plant) if plant else qs.none()
        return Response(TransportSlipSerializer(qs[:500], many=True).data)

    def post(self, request):
        if request.user.role == 'usine':
            return Response({'detail': "Seule la coopérative émet une fiche d'accompagnement."},
                            status=status.HTTP_403_FORBIDDEN)
        coop = resolve_cooperative(request, request.data)
        if coop is None:
            return _missing_coop()
        s = TransportSlipSerializer(data=request.data, context={'request': request, 'cooperative': coop})
        s.is_valid(raise_exception=True)
        slip = s.save()
        _log(request, 'create_transport_slip', slip, f'{slip.number} — lot {slip.lot.code}')
        plant_ops.notify_truck_sent(slip)
        return Response(TransportSlipSerializer(slip).data, status=status.HTTP_201_CREATED)


class TransportDetailView(APIView):
    permission_classes = SLIP_PERMS

    def _get(self, request, pk):
        return slips_for(request.user).filter(pk=pk).first()

    def get(self, request, pk):
        slip = self._get(request, pk)
        if slip is None:
            return Response({'detail': "Fiche d'accompagnement introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(TransportSlipSerializer(slip).data)

    def patch(self, request, pk):
        """Réception (poids à l'arrivée) ou annulation motivée ; les données du départ ne changent plus."""
        slip = self._get(request, pk)
        if slip is None:
            return Response({'detail': "Fiche d'accompagnement introuvable."}, status=status.HTTP_404_NOT_FOUND)
        if slip.status != 'emise':
            return Response({'detail': 'Fiche déjà clôturée (livrée ou annulée).'}, status=status.HTTP_400_BAD_REQUEST)
        if request.data.get('status') == 'annulee':
            if request.user.role == 'usine':
                return Response({'detail': "Seule la coopérative peut annuler une fiche d'accompagnement."},
                                status=status.HTTP_403_FORBIDDEN)
            reason = (request.data.get('notes') or '').strip()
            if not reason:
                return Response({'detail': "Indiquez le motif de l'annulation."}, status=status.HTTP_400_BAD_REQUEST)
            slip.status, slip.reception_notes = 'annulee', reason
            slip.save(update_fields=['status', 'reception_notes'])
            _log(request, 'cancel_transport_slip', slip, f'{slip.number} — {reason}')
            return Response(TransportSlipSerializer(slip).data)
        return self._receive(request, slip)

    def _receive(self, request, slip):
        """Réception : poids, contrôle qualité et décision. Un lot refusé retourne à la coopérative."""
        data = {k: (None if v == '' and k in QUALITY_FIELDS else v)
                for k, v in request.data.items() if k in RECEPTION_FIELDS}
        if not (data.get('received_by') or '').strip():
            data['received_by'] = request.user.full_name or request.user.username
        decision = data.get('decision') or 'accepte'
        if decision not in TransportSlip.DECISIONS:
            return Response({'detail': 'Décision inconnue (accepte, reserve ou refuse).'}, status=status.HTTP_400_BAD_REQUEST)
        data['decision'] = decision
        if decision != 'accepte' and not (data.get('decision_reason') or '').strip():
            return Response({'detail': 'Indiquez le motif des réserves ou du refus.'}, status=status.HTTP_400_BAD_REQUEST)
        if decision != 'refuse' and data.get('received_weight_kg') in (None, ''):
            return Response({'detail': "Indiquez le poids pesé à l'arrivée."}, status=status.HTTP_400_BAD_REQUEST)
        if decision == 'refuse' and slip.lot.processing_inputs.exists():
            return Response({'detail': 'Ce lot est déjà entré en usinage : il ne peut plus être refusé.'},
                            status=status.HTTP_400_BAD_REQUEST)
        s = TransportSlipSerializer(slip, data=data, partial=True,
                                    context={'request': request, 'cooperative': slip.cooperative})
        s.is_valid(raise_exception=True)
        with transaction.atomic():
            slip = s.save(status='refusee' if decision == 'refuse' else 'livree', received_at=timezone.now())
            if decision == 'refuse' and not slip.lot.transport_slips.filter(status__in=('emise', 'livree')).exists():
                slip.lot.status = 'valide'                  # retour à la coopérative : elle peut le réexpédier
                slip.lot.save(update_fields=['status'])
        _log(request, 'receive_transport_slip', slip,
             f'{slip.number} — {TransportSlip.DECISIONS[decision]}'
             + (f', {slip.received_weight_kg} kg reçus' if slip.received_weight_kg is not None else ''))
        plant_ops.notify_reception(slip)
        return Response(TransportSlipSerializer(slip).data)
