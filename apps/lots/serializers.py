from django.db import transaction
from django.db.models import Max
from rest_framework import serializers

from apps.producers.models import Producer
from . import traceability as tr
from .models import Lot, LotLine


class LotLineSerializer(serializers.ModelSerializer):
    producer_name = serializers.CharField(source='producer.full_name', read_only=True)
    producer_code = serializers.CharField(source='producer.field_id_base', read_only=True)
    village = serializers.CharField(source='producer.village', read_only=True)

    class Meta:
        model = LotLine
        fields = ['id', 'producer', 'producer_name', 'producer_code', 'village', 'weight_kg', 'bags', 'delivery_date', 'receipt']


class LotSerializer(serializers.ModelSerializer):
    """
    Un lot se compose soit de pesées (`deliveries` : identifiants des fiches de pesée, lignes calculées par
    producteur), soit de lignes saisies à la main (`lines`, ancien fonctionnement conservé).
    """
    lines = LotLineSerializer(many=True, required=False)
    deliveries = serializers.ListField(child=serializers.UUIDField(), write_only=True, required=False)
    delivery_count = serializers.SerializerMethodField()
    cooperative_name = serializers.CharField(source='cooperative.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True, default='')
    net_weight_kg = serializers.SerializerMethodField()

    class Meta:
        model = Lot
        fields = ['id', 'cooperative', 'cooperative_name', 'code', 'campaign', 'product', 'lot_date', 'bags', 'gross_weight_kg',
                  'net_weight_kg', 'quality', 'warehouse', 'buyer', 'destination', 'transport', 'notes', 'status',
                  'created_by_name', 'created_at', 'updated_at', 'lines', 'deliveries', 'delivery_count']
        read_only_fields = ['id', 'cooperative', 'code', 'created_at', 'updated_at']

    def get_delivery_count(self, obj):
        annotated = getattr(obj, 'valid_deliveries', None)          # liste : compté par la requête principale
        return annotated if annotated is not None else obj.deliveries.filter(status='valide').count()

    def get_net_weight_kg(self, obj):
        return float(sum((l.weight_kg for l in obj.lines.all()), 0))

    def validate(self, data):
        if self.instance is None and not data.get('lines') and not data.get('deliveries'):
            raise serializers.ValidationError('Ajoutez au moins une pesée ou une livraison de producteur.')
        if data.get('deliveries') is not None:
            if not data['deliveries']:
                raise serializers.ValidationError({'deliveries': 'Un lot doit contenir au moins une pesée.'})
            product = data.get('product') or (self.instance.product if self.instance else 'cacao')
            found, error = tr.check_deliveries(product, self.context['cooperative'], data['deliveries'], self.instance)
            if error:
                raise serializers.ValidationError({'deliveries': error})
            data['deliveries'] = found
        return data

    def validate_lines(self, lines):
        if not lines:
            return lines
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
        lines = data.pop('lines', None) or []
        deliveries = data.pop('deliveries', None)
        coop = self.context['cooperative']
        lot = Lot.objects.create(cooperative=coop, code=self.next_code(coop, data.get('campaign')),
                                 created_by=self.context['request'].user, **data)
        if deliveries:
            tr.assign_deliveries(lot, deliveries)
        else:
            LotLine.objects.bulk_create([LotLine(lot=lot, **l) for l in lines])
        return lot

    @transaction.atomic
    def update(self, lot, data):
        lines = data.pop('lines', None)
        deliveries = data.pop('deliveries', None)
        for k, v in data.items():
            setattr(lot, k, v)
        lot.save()
        if deliveries is not None:
            tr.assign_deliveries(lot, deliveries)
        elif lines is not None and not lot.deliveries.exists():      # lot de pesées : lignes calculées, jamais saisies
            lot.lines.all().delete()
            LotLine.objects.bulk_create([LotLine(lot=lot, **l) for l in lines])
        return lot
