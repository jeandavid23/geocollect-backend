from rest_framework import serializers
from .models import Cooperative, CooperativeDocument


class CooperativeCreateSerializer(serializers.ModelSerializer):
    """Crée une coopérative ET son compte de connexion (rôle cooperative)."""
    login_username = serializers.CharField(write_only=True, required=False, allow_blank=True)
    login_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    account_username = serializers.SerializerMethodField()
    account_password = serializers.SerializerMethodField()

    class Meta:
        model = Cooperative
        fields = [
            'id', 'name', 'rccm', 'agrement', 'pca', 'adg', 'director',
            'sig_manager', 'phone', 'email', 'address', 'region', 'country',
            'login_username', 'login_password',
            'account_username', 'account_password',
        ]
        read_only_fields = ['id']
        extra_kwargs = {
            f: {'required': False, 'allow_blank': True}
            for f in ['rccm', 'agrement', 'pca', 'adg', 'director',
                      'sig_manager', 'phone', 'email', 'address', 'region', 'country']
        }

    def get_account_username(self, obj):
        return getattr(obj, '_account_username', None)

    def get_account_password(self, obj):
        return getattr(obj, '_account_password', None)

    def create(self, validated_data):
        from apps.accounts.models import User
        from apps.accounts.credentials import generate_username, generate_password

        username = (validated_data.pop('login_username', '') or '').strip()
        password = (validated_data.pop('login_password', '') or '').strip()

        coop = Cooperative.objects.create(**validated_data)

        if not username:
            username = generate_username(coop.name)
        if not password:
            password = generate_password()

        user = User(
            username=username,
            full_name=coop.name,
            email=coop.email or None,
            phone=coop.phone or '',
            role=User.Role.COOPERATIVE,
            cooperative=coop,
        )
        user.set_password(password)
        user.save()

        coop._account_username = username
        coop._account_password = password
        return coop


class CooperativeSerializer(serializers.ModelSerializer):
    producer_count = serializers.ReadOnlyField()
    parcel_count = serializers.ReadOnlyField()
    total_hectares = serializers.ReadOnlyField()
    agent_count = serializers.ReadOnlyField()

    class Meta:
        model = Cooperative
        fields = [
            'id', 'name', 'rccm', 'agrement', 'logo',
            'pca', 'adg', 'director', 'sig_manager',
            'phone', 'email', 'address', 'region', 'country',
            'is_active', 'producer_count', 'parcel_count',
            'total_hectares', 'agent_count', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class CooperativeListSerializer(serializers.ModelSerializer):
    producer_count = serializers.ReadOnlyField()
    parcel_count = serializers.ReadOnlyField()
    total_hectares = serializers.ReadOnlyField()
    agent_count = serializers.ReadOnlyField()

    class Meta:
        model = Cooperative
        fields = ['id', 'name', 'region', 'country', 'is_active',
                  'producer_count', 'parcel_count', 'total_hectares', 'agent_count']


class CooperativeDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CooperativeDocument
        fields = ['id', 'name', 'file', 'doc_type', 'uploaded_at']
        read_only_fields = ['id', 'uploaded_at']
