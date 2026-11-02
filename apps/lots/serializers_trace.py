"""Pesées et fiches d'accompagnement (sérialiseurs)."""
from rest_framework import serializers

from . import traceability as tr
from .models import Delivery, TransportSlip


class DeliverySerializer(serializers.ModelSerializer):
    producer_name = serializers.CharField(source='producer.full_name', read_only=True)
    producer_code = serializers.CharField(source='producer.field_id_base', read_only=True)
    village = serializers.CharField(source='producer.village', read_only=True)
    lot_code = serializers.CharField(source='lot.code', read_only=True, default=None)
    cooperative_name = serializers.CharField(source='cooperative.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True, default='')
    tare_kg = serializers.DecimalField(max_digits=10, decimal_places=1, required=False, allow_null=True)

    class Meta:
        model = Delivery
        fields = ['id', 'number', 'cooperative', 'cooperative_name', 'producer', 'producer_name', 'producer_code', 'village',
                  'lot', 'lot_code', 'campaign', 'product', 'delivery_date', 'collection_point', 'bags', 'gross_kg',
                  'tare_kg', 'deduction_kg', 'net_kg', 'moisture_pct', 'quality', 'unit_price', 'amount', 'weigher',
                  'notes', 'status', 'cancel_reason', 'cancelled_at', 'created_by_name', 'created_at']
        read_only_fields = ['id', 'number', 'cooperative', 'lot', 'net_kg', 'amount', 'status', 'cancel_reason',
                            'cancelled_at', 'created_at']

    def validate_producer(self, producer):
        if producer.cooperative_id != self.context['cooperative'].id:
            raise serializers.ValidationError('Producteur inconnu dans cette coopérative.')
        return producer

    def validate(self, data):
        if data['gross_kg'] <= 0:
            raise serializers.ValidationError({'gross_kg': 'Le poids brut doit être positif.'})
        tare, net, _ = tr.weights(data['gross_kg'], data.get('bags', 0), data.get('tare_kg'), data.get('deduction_kg'))
        if tare < 0 or (data.get('deduction_kg') or 0) < 0:
            raise serializers.ValidationError('La tare et la réfaction ne peuvent pas être négatives.')
        if net <= 0:
            raise serializers.ValidationError('Poids net nul ou négatif : vérifiez le poids brut, la tare et la réfaction.')
        return data

    def create(self, data):
        tare, net, amount = tr.weights(data['gross_kg'], data.get('bags', 0), data.pop('tare_kg', None),
                                       data.get('deduction_kg'), data.get('unit_price'))
        return tr.create_delivery(self.context['cooperative'], tare_kg=tare, net_kg=net, amount=amount,
                                  created_by=self.context['request'].user, **data)


class TransportSlipSerializer(serializers.ModelSerializer):
    lot_code = serializers.CharField(source='lot.code', read_only=True)
    lot_product = serializers.CharField(source='lot.product', read_only=True)
    lot_campaign = serializers.CharField(source='lot.campaign', read_only=True)
    cooperative_name = serializers.CharField(source='cooperative.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True, default='')
    weight_gap_kg = serializers.SerializerMethodField()

    class Meta:
        model = TransportSlip
        fields = ['id', 'number', 'cooperative', 'cooperative_name', 'lot', 'lot_code', 'lot_product', 'lot_campaign',
                  'issued_at', 'origin', 'destination', 'consignee', 'carrier', 'vehicle_plate', 'trailer_plate',
                  'driver_name', 'driver_phone', 'driver_licence', 'bags', 'gross_weight_kg', 'seals', 'notes', 'status',
                  'received_at', 'received_weight_kg', 'received_by', 'reception_notes', 'weight_gap_kg',
                  'created_by_name']
        read_only_fields = ['id', 'number', 'cooperative', 'issued_at', 'status', 'received_at']

    def get_weight_gap_kg(self, obj):
        if obj.received_weight_kg is None:
            return None
        return float(obj.received_weight_kg - obj.gross_weight_kg)

    def validate_lot(self, lot):
        if lot.cooperative_id != self.context['cooperative'].id:
            raise serializers.ValidationError('Lot inconnu dans cette coopérative.')
        if lot.status not in ('valide', 'expedie'):
            raise serializers.ValidationError("Seul un lot validé (contrôles EUDR levés) peut recevoir une fiche "
                                              "d'accompagnement.")
        return lot

    def create(self, data):
        lot = data['lot']
        slip = tr.create_slip(self.context['cooperative'], str(lot.lot_date)[:4],
                              created_by=self.context['request'].user, **data)
        if lot.status == 'valide':
            lot.status = 'expedie'
            lot.save(update_fields=['status'])
        return slip
