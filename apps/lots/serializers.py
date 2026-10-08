from django.db import transaction
from django.db.models import Max
from rest_framework import serializers

from apps.producers.models import Producer
from .models import Lot, LotLine


class LotLineSerializer(serializers.ModelSerializer):
    producer_name = serializers.CharField(source='producer.full_name', read_only=True)
    producer_code = serializers.CharField(source='producer.field_id_base', read_only=True)
    village = serializers.CharField(source='producer.village', read_only=True)

    class Meta:
        model = LotLine
        fields = ['id', 'producer', 'producer_name', 'producer_code', 'village', 'weight_kg', 'bags', 'delivery_date', 'receipt']


class LotSerializer(serializers.ModelSerializer):
    lines = LotLineSerializer(many=True)
    cooperative_name = serializers.CharField(source='cooperative.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True, default='')
    net_weight_kg = serializers.SerializerMethodField()

    class Meta:
        model = Lot
        fields = ['id', 'cooperative', 'cooperative_name', 'code', 'campaign', 'product', 'lot_date', 'bags', 'gross_weight_kg',
                  'net_weight_kg', 'quality', 'warehouse', 'buyer', 'destination', 'transport', 'notes', 'status',
                  'created_by_name', 'created_at', 'updated_at', 'lines']
        read_only_fields = ['id', 'cooperative', 'code', 'created_at', 'updated_at']

    def get_net_weight_kg(self, obj):
        return float(sum((l.weight_kg for l in obj.lines.all()), 0))

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError('Ajoutez au moins une livraison de producteur.')
        coop = self.context['cooperative']
        ids = {l['producer'].id for l in lines}
        ok = set(Producer.objects.filter(id__in=ids, cooperative=coop).values_list('id', flat=True))
        if ok != ids:
            raise serializers.ValidationError('Producteur inconnu dans cette coopérative.')
        for l in lines:
            if l['weight_kg'] <= 0:
                raise serializers.ValidationError('Chaque livraison doit avoir un poids positif.')
        return lines

    @staticmethod
    def next_code(coop, campaign):
        prefix = ''.join(ch for ch in (coop.name or 'COOP').upper() if ch.isalnum())[:6] or 'COOP'
        year = (campaign or '')[:4]
        base = f'LOT-{prefix}-{year}-'
        last = Lot.objects.filter(code__startswith=base).aggregate(m=Max('code'))['m']
        n = int(last.rsplit('-', 1)[1]) + 1 if last else 1
        return f'{base}{n:04d}'

    @transaction.atomic
    def create(self, data):
        lines = data.pop('lines')
        coop = self.context['cooperative']
        lot = Lot.objects.create(cooperative=coop, code=self.next_code(coop, data.get('campaign')),
                                 created_by=self.context['request'].user, **data)
        LotLine.objects.bulk_create([LotLine(lot=lot, **l) for l in lines])
        return lot

    @transaction.atomic
    def update(self, lot, data):
        lines = data.pop('lines', None)
        for k, v in data.items():
            setattr(lot, k, v)
        lot.save()
        if lines is not None:
            lot.lines.all().delete()
            LotLine.objects.bulk_create([LotLine(lot=lot, **l) for l in lines])
        return lot
