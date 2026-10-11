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
            'mapping_started_at', 'mapping_ended_at', 'is_synced', 'client_id',
        ]
        extra_kwargs = {
            # unicité vérifiée dans create() (renvoi idempotent), pas comme une erreur de validation
            'client_id': {'required': False, 'validators': []},
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
        role = getattr(user, 'role', None)
        if role == 'agent':
            agent = getattr(user, 'agent_profile', None)
            if agent:
                attrs['agent'] = agent
                attrs['cooperative'] = agent.cooperative
        elif role == 'cooperative':
            attrs['cooperative'] = user.cooperative
        # Cloisonnement : producteur et agent doivent appartenir à la coopérative de l'utilisateur
        if role in ('agent', 'cooperative'):
            producer = attrs.get('producer') or getattr(self.instance, 'producer', None)
            if producer is not None and producer.cooperative_id != user.cooperative_id:
                raise serializers.ValidationError({'producer': 'Producteur inconnu.'})
            agent = attrs.get('agent')
            if agent is not None and agent.cooperative_id != user.cooperative_id:
                raise serializers.ValidationError({'agent': 'Agent inconnu.'})
        elif role == 'super_admin':
            from apps.accounts.permissions import can_access_cooperative
            producer = attrs.get('producer') or getattr(self.instance, 'producer', None)
            if producer is not None and not can_access_cooperative(user, producer.cooperative_id):
                raise serializers.ValidationError({'producer': 'Producteur inconnu.'})
            agent = attrs.get('agent')
            if agent is not None and not can_access_cooperative(user, agent.cooperative_id):
                raise serializers.ValidationError({'agent': 'Agent inconnu.'})
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
        # Renvoi d'une parcelle déjà reçue (réseau coupé avant la réponse) : on renvoie la parcelle existante
        cid = validated_data.get('client_id')
        if cid:
            existing = Parcel.objects.filter(client_id=cid).first()
            if existing is not None:
                if existing.cooperative_id != validated_data.get('cooperative', existing.cooperative).id:
                    raise serializers.ValidationError({'client_id': 'Identifiant déjà utilisé.'})
                self.instance_was_existing = True
                return existing
        from django.db import IntegrityError, transaction
        producer = validated_data['producer']
        coop = validated_data.get('cooperative') or producer.cooperative
        in_coop = Parcel.objects.filter(cooperative=coop)
        parcel = None
        for _ in range(5):
            # Producteur du registre sans aucun polygone : la parcelle porte EXACTEMENT son code du registre,
            # sans ajout. Les parcelles suivantes d'un producteur déjà cartographié reçoivent un suffixe.
            first = not Parcel.objects.filter(producer=producer).exists() and not producer.legacy_parcels.exists()
            if first:
                field_id = producer.field_id_base
                if in_coop.filter(field_id=field_id).exists():
                    raise serializers.ValidationError({'field_id': (
                        f'Le code {field_id} est déjà porté par une autre parcelle de la coopérative : '
                        'corrigez le code du producteur dans le registre avant de mapper.')})
            else:
                index = get_next_parcel_index(producer.id)
                field_id = generate_parcel_field_id(producer.field_id_base, index)
                while in_coop.filter(field_id=field_id).exists():
                    index += 1
                    field_id = generate_parcel_field_id(producer.field_id_base, index)
            validated_data['field_id'] = field_id
            try:
                with transaction.atomic():
                    parcel = Parcel(**validated_data)
                    parcel.save()
                break
            except IntegrityError:
                # deux agents ont mappé le même producteur au même instant : le second reçoit un suffixe
                continue
        if parcel is None:
            raise serializers.ValidationError({'field_id': 'Code de parcelle indisponible, réessayez.'})
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
