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
            'account_username', 'account_password', 'managed_by',
        ]
        read_only_fields = ['id']
        extra_kwargs = {
            f: {'required': False, 'allow_blank': True}
            for f in ['rccm', 'agrement', 'pca', 'adg', 'director',
                      'sig_manager', 'phone', 'email', 'address', 'region', 'country']
        }
        extra_kwargs['managed_by'] = {'required': False, 'allow_null': True}

    def validate_managed_by(self, value):
        if value is not None and value.role != 'super_admin':
            raise serializers.ValidationError('Le gestionnaire doit être un super admin.')
        return value

    def get_account_username(self, obj):
        return getattr(obj, '_account_username', None)

    def get_account_password(self, obj):
        return getattr(obj, '_account_password', None)

    def create(self, validated_data):
        from apps.accounts.models import User
        from apps.accounts.credentials import generate_username, generate_password

        username = (validated_data.pop('login_username', '') or '').strip()
        password = (validated_data.pop('login_password', '') or '').strip()
        if password:
            # mot de passe choisi à la main : mêmes règles que partout (8 caractères, pas trop courant)
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                validate_password(password)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({'login_password': list(exc.messages)})

        # rccm est unique : génère une valeur unique si non fournie (évite la collision sur '')
        if not (validated_data.get('rccm') or '').strip():
            import uuid as _uuid
            validated_data['rccm'] = f'AUTO-{_uuid.uuid4().hex[:10].upper()}'

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

        # Email de confirmation avec les accès
        from apps.accounts.emails import send_credentials_email
        send_credentials_email(
            to_email=coop.email, full_name=coop.name, role_label='Coopérative',
            username=username, password=password,
        )

        coop._account_username = username
        coop._account_password = password
        return coop


class CooperativeSerializer(serializers.ModelSerializer):
    producer_count = serializers.ReadOnlyField()
    parcel_count = serializers.ReadOnlyField()
    total_hectares = serializers.ReadOnlyField()
    agent_count = serializers.ReadOnlyField()
    managed_by_name = serializers.SerializerMethodField()

    def get_managed_by_name(self, obj):
        m = obj.managed_by
        if m is None:
            return None
        try:
            org = m.admin_license.organization
        except Exception:  # noqa: BLE001
            org = ''
        return org or m.full_name

    class Meta:
        model = Cooperative
        fields = [
            'id', 'name', 'rccm', 'agrement', 'logo',
            'pca', 'adg', 'director', 'sig_manager',
            'phone', 'email', 'address', 'region', 'country',
            'is_active', 'producer_count', 'parcel_count',
            'total_hectares', 'agent_count', 'created_at', 'managed_by', 'managed_by_name',
        ]
        # le transfert d'une coopérative entre super admins passe par /platform/ (propriétaire)
        read_only_fields = ['id', 'created_at', 'managed_by']


class CooperativeListSerializer(serializers.ModelSerializer):
    producer_count = serializers.ReadOnlyField()
    parcel_count = serializers.ReadOnlyField()
    total_hectares = serializers.ReadOnlyField()
    agent_count = serializers.ReadOnlyField()
    managed_by_name = serializers.SerializerMethodField()

    def get_managed_by_name(self, obj):
        m = obj.managed_by
        if m is None:
            return None
        try:
            org = m.admin_license.organization
        except Exception:  # noqa: BLE001
            org = ''
        return org or m.full_name

    class Meta:
        model = Cooperative
        fields = ['id', 'name', 'region', 'country', 'is_active',
                  'producer_count', 'parcel_count', 'total_hectares', 'agent_count',
                  'managed_by', 'managed_by_name']


class CooperativeDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CooperativeDocument
        fields = ['id', 'name', 'file', 'doc_type', 'uploaded_at']
        read_only_fields = ['id', 'uploaded_at']
