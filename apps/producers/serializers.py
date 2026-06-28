import uuid
from rest_framework import serializers
from .models import Agent, Producer


def _uuid_hex():
    return uuid.uuid4().hex[:6].upper()


class AgentCreateSerializer(serializers.ModelSerializer):
    """Crée un agent ET son compte de connexion (rôle agent)."""
    full_name = serializers.CharField(write_only=True)
    email = serializers.EmailField(write_only=True, required=False, allow_blank=True)
    phone = serializers.CharField(write_only=True, required=False, allow_blank=True)
    login_username = serializers.CharField(write_only=True, required=False, allow_blank=True)
    login_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    account_username = serializers.SerializerMethodField()
    account_password = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = [
            'id', 'cooperative', 'code', 'zone',
            'full_name', 'email', 'phone',
            'login_username', 'login_password',
            'account_username', 'account_password',
        ]
        read_only_fields = ['id']
        extra_kwargs = {
            'cooperative': {'required': False},  # auto-rempli pour le rôle cooperative
            'zone': {'required': False, 'allow_blank': True},
        }

    def get_account_username(self, obj):
        return getattr(obj, '_account_username', None)

    def get_account_password(self, obj):
        return getattr(obj, '_account_password', None)

    def validate(self, attrs):
        request = self.context.get('request')
        if request and getattr(request.user, 'role', None) == 'cooperative':
            attrs['cooperative'] = request.user.cooperative
        if not attrs.get('cooperative'):
            raise serializers.ValidationError({'cooperative': 'Coopérative requise.'})
        return attrs

    def create(self, validated_data):
        from apps.accounts.models import User
        from apps.accounts.credentials import generate_username, generate_password

        full_name = validated_data.pop('full_name')
        email = (validated_data.pop('email', '') or '').strip() or None
        phone = (validated_data.pop('phone', '') or '').strip()
        username = (validated_data.pop('login_username', '') or '').strip()
        password = (validated_data.pop('login_password', '') or '').strip()
        cooperative = validated_data['cooperative']

        if not username:
            username = generate_username(full_name)
        if not password:
            password = generate_password()

        user = User(
            username=username, full_name=full_name, email=email, phone=phone,
            role=User.Role.AGENT, cooperative=cooperative,
        )
        user.set_password(password)
        user.save()

        # code agent est unique : garantit l'unicité (évite la collision entre coops)
        base_code = (validated_data.get('code') or '').strip() or f'AG-{_uuid_hex()}'
        code = base_code
        i = 1
        while Agent.objects.filter(code=code).exists():
            i += 1
            code = f'{base_code}-{i}'
        validated_data['code'] = code

        agent = Agent.objects.create(user=user, **validated_data)

        # Email de confirmation avec les accès
        from apps.accounts.emails import send_credentials_email
        send_credentials_email(
            to_email=email, full_name=full_name, role_label='Agent Mappeur',
            username=username, password=password,
            cooperative_name=cooperative.name, code=agent.code,
        )

        # Notifie la coopérative + super admins
        try:
            from apps.accounts.notify import notify_cooperative
            notify_cooperative(
                cooperative, ntype='info',
                title=f'Nouvel agent mappeur — {full_name}',
                message=f'Code {agent.code} · zone {agent.zone or "—"}',
            )
        except Exception:
            pass

        agent._account_username = username
        agent._account_password = password
        return agent


class AgentSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='user.full_name', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)
    phone = serializers.CharField(source='user.phone', read_only=True)
    parcel_count = serializers.ReadOnlyField()
    total_hectares = serializers.ReadOnlyField()
    cooperative_name = serializers.CharField(source='cooperative.name', read_only=True)

    class Meta:
        model = Agent
        fields = [
            'id', 'user', 'full_name', 'email', 'phone',
            'cooperative', 'cooperative_name', 'code', 'zone',
            'is_active', 'parcel_count', 'total_hectares', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class ProducerSerializer(serializers.ModelSerializer):
    full_name = serializers.ReadOnlyField()
    parcel_count = serializers.ReadOnlyField()
    total_hectares = serializers.ReadOnlyField()
    cooperative_name = serializers.CharField(source='cooperative.name', read_only=True)
    assigned_agent_name = serializers.CharField(source='assigned_agent.user.full_name', read_only=True)

    class Meta:
        model = Producer
        fields = [
            'id', 'cooperative', 'cooperative_name',
            'assigned_agent', 'assigned_agent_name',
            'field_id_base', 'first_name', 'last_name', 'full_name',
            'phone', 'national_id', 'gender', 'birth_year',
            'village', 'section', 'region', 'country',
            'is_active', 'parcel_count', 'total_hectares', 'created_at',
        ]
        read_only_fields = ['id', 'field_id_base', 'created_at']


class ProducerCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Producer
        fields = [
            'id', 'cooperative', 'assigned_agent', 'field_id_base',
            'first_name', 'last_name', 'phone', 'national_id', 'gender', 'birth_year',
            'village', 'section', 'region', 'country',
        ]
        read_only_fields = ['id', 'field_id_base']
        extra_kwargs = {
            'cooperative': {'required': False},
            'assigned_agent': {'required': False},
            'region': {'required': False, 'allow_blank': True},
            'country': {'required': False, 'allow_blank': True},
        }

    def validate(self, attrs):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user:
            if user.role == 'cooperative':
                attrs['cooperative'] = user.cooperative
            elif user.role == 'agent':
                agent = getattr(user, 'agent_profile', None)
                if agent:
                    attrs['cooperative'] = agent.cooperative
                    attrs.setdefault('assigned_agent', agent)
        if not attrs.get('cooperative'):
            raise serializers.ValidationError({'cooperative': 'Coopérative requise.'})
        return attrs

    def create(self, validated_data):
        from utils.field_id import generate_field_id_base, get_next_producer_index
        section = validated_data.get('section', '')
        cooperative = validated_data.get('cooperative')
        index = get_next_producer_index(cooperative.id, section)
        # field_id_base est unique : incrémente jusqu'à trouver une valeur libre
        field_id_base = generate_field_id_base(section, index)
        while Producer.objects.filter(field_id_base=field_id_base).exists():
            index += 1
            field_id_base = generate_field_id_base(section, index)
        validated_data['field_id_base'] = field_id_base
        producer = super().create(validated_data)
        # Notifie la coopérative + super admins
        try:
            from apps.accounts.notify import notify_cooperative
            notify_cooperative(
                producer.cooperative, ntype='info',
                title=f'Nouveau producteur — {producer.full_name}',
                message=f'{producer.field_id_base} · {producer.village} · {producer.section}',
            )
        except Exception:
            pass
        return producer
