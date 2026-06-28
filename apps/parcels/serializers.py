from rest_framework import serializers
from .models import Parcel


class ParcelSerializer(serializers.ModelSerializer):
    producer_name = serializers.CharField(source='producer.full_name', read_only=True)
    agent_name = serializers.CharField(source='agent.user.full_name', read_only=True)
    cooperative_name = serializers.CharField(source='cooperative.name', read_only=True)

    class Meta:
        model = Parcel
        fields = [
            'id', 'field_id', 'name',
            'producer', 'producer_name',
            'cooperative', 'cooperative_name',
            'agent', 'agent_name',
            'geometry', 'area_hectares', 'perimeter_meters', 'vertex_count',
            'culture', 'village', 'section', 'region', 'country',
            'status', 'eudr_score', 'eudr_status', 'validation_result',
            'mapping_started_at', 'mapping_ended_at',
            'is_synced', 'synced_at', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'field_id', 'eudr_score', 'eudr_status',
                            'validation_result', 'status', 'created_at', 'updated_at']


class ParcelCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Parcel
        fields = [
            'producer', 'cooperative', 'agent', 'name',
            'geometry', 'area_hectares', 'perimeter_meters', 'vertex_count',
            'culture', 'village', 'section', 'region', 'country',
            'mapping_started_at', 'mapping_ended_at', 'is_synced',
        ]
        extra_kwargs = {
            'cooperative': {'required': False},
            'agent': {'required': False},
            'region': {'required': False, 'allow_blank': True},
            'country': {'required': False, 'allow_blank': True},
            'village': {'required': False, 'allow_blank': True},
            'section': {'required': False, 'allow_blank': True},
        }

    def validate(self, attrs):
        # Auto-remplit agent + coopérative depuis le compte connecté (agent mappeur)
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user and getattr(user, 'role', None) == 'agent':
            agent = getattr(user, 'agent_profile', None)
            if agent:
                attrs['agent'] = agent
                attrs['cooperative'] = agent.cooperative
        producer = attrs.get('producer')
        if not attrs.get('cooperative') and producer:
            attrs['cooperative'] = producer.cooperative
        if not attrs.get('agent') and producer and producer.assigned_agent:
            attrs['agent'] = producer.assigned_agent
        if producer:
            attrs.setdefault('village', producer.village)
            attrs.setdefault('section', producer.section)
            attrs.setdefault('region', producer.region)
            attrs.setdefault('country', producer.country)
        return attrs

    def create(self, validated_data):
        from utils.field_id import generate_parcel_field_id, get_next_parcel_index
        producer = validated_data['producer']
        index = get_next_parcel_index(producer.id)
        field_id = generate_parcel_field_id(producer.field_id_base, index)
        while Parcel.objects.filter(field_id=field_id).exists():
            index += 1
            field_id = generate_parcel_field_id(producer.field_id_base, index)
        validated_data['field_id'] = field_id
        parcel = Parcel(**validated_data)
        parcel.save()
        parcel.run_eudr_validation()
        # Notifie la coopérative + super admins
        try:
            from apps.accounts.notify import notify_cooperative
            notify_cooperative(
                parcel.cooperative, ntype='success',
                title=f'Nouvelle parcelle mappée — {parcel.field_id}',
                message=f'{parcel.area_hectares} ha · {parcel.culture} · {parcel.village} · EUDR {parcel.eudr_score}%',
            )
        except Exception:
            pass
        return parcel

    def to_representation(self, instance):
        # Renvoie la parcelle complète (field_id, eudr_score, geometry…) après création
        return ParcelSerializer(instance, context=self.context).data


class ParcelListSerializer(serializers.ModelSerializer):
    producer_name = serializers.CharField(source='producer.full_name', read_only=True)

    class Meta:
        model = Parcel
        fields = [
            'id', 'field_id', 'producer_name', 'village', 'section',
            'culture', 'area_hectares', 'eudr_score', 'eudr_status',
            'status', 'is_synced', 'created_at',
        ]


class ParcelGeoJSONSerializer(serializers.ModelSerializer):
    """Returns a GeoJSON FeatureCollection for map display."""

    def to_representation(self, instance):
        return {
            'type': 'Feature',
            'properties': {
                'id': str(instance.id),
                'field_id': instance.field_id,
                'producer_name': instance.producer.full_name,
                'village': instance.village,
                'section': instance.section,
                'culture': instance.culture,
                'area_hectares': instance.area_hectares,
                'eudr_score': instance.eudr_score,
                'eudr_status': instance.eudr_status,
            },
            'geometry': instance.geometry,
        }

    class Meta:
        model = Parcel
        fields = []
