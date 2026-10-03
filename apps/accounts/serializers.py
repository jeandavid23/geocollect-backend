from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import User, ActivityLog, Notification


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """JWT login — retourne les infos utilisateur avec les tokens."""

    def validate(self, attrs):
        from rest_framework.exceptions import AuthenticationFailed
        from .tenancy import tenant_status
        data = super().validate(attrs)
        user = self.user
        status = tenant_status(user)
        if not status['active']:
            # Organisation suspendue, abonnement expiré ou coopérative désactivée : connexion refusée
            raise AuthenticationFailed({'detail': status['reason'], 'code': 'tenant_inactive'})
        data['user'] = MeSerializer(user).data
        return data


class UserSerializer(serializers.ModelSerializer):
    cooperative_id = serializers.SerializerMethodField()
    cooperative_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'full_name', 'phone',
            'national_id', 'photo_data',
            'role', 'is_active', 'cooperative_id', 'cooperative_name',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'role', 'is_active']

    def validate_photo_data(self, value):
        # photo de profil en base64 : 2 Mo maximum (évite de saturer la base)
        if value and len(value) > 2_800_000:
            raise serializers.ValidationError('Photo trop volumineuse (2 Mo maximum).')
        if value and not value.startswith('data:image/'):
            raise serializers.ValidationError('Format de photo invalide.')
        return value

    def get_cooperative_id(self, obj):
        return str(obj.cooperative_id) if obj.cooperative_id else None

    def get_cooperative_name(self, obj):
        return obj.cooperative.name if obj.cooperative else None


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_password(self, value):
        from django.contrib.auth.password_validation import validate_password
        validate_password(value)
        return value

    class Meta:
        model = User
        fields = ['username', 'email', 'full_name', 'phone', 'role', 'password', 'cooperative']

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True, min_length=8)

    def validate_new_password(self, value):
        from django.contrib.auth.password_validation import validate_password
        validate_password(value, self.context['request'].user)
        return value

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('Mot de passe actuel incorrect.')
        return value


class ActivityLogSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)

    class Meta:
        model = ActivityLog
        fields = ['id', 'user_name', 'action', 'resource', 'resource_id', 'details', 'ip_address', 'timestamp']


class NotificationSerializer(serializers.ModelSerializer):
    cooperative_name = serializers.CharField(source='cooperative.name', read_only=True, default=None)

    class Meta:
        model = Notification
        fields = ['id', 'type', 'title', 'message', 'is_read', 'cooperative_name', 'created_at']
        read_only_fields = fields


class MeSerializer(UserSerializer):
    """Profil de l'utilisateur connecté + droits de son organisation (modules, licence)."""
    modules = serializers.SerializerMethodField()
    license = serializers.SerializerMethodField()

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + ['modules', 'license']

    def get_modules(self, obj):
        from .tenancy import tenant_status
        return tenant_status(obj)['modules']   # None = tous les modules

    def get_license(self, obj):
        """Licence et consommation du super admin (son propre abonnement) ; None pour les autres rôles."""
        if obj.role != 'super_admin':
            return None
        return license_summary(obj)


def license_summary(admin):
    """Licence + consommation d'un super admin (utilisé par son tableau de bord et par le propriétaire)."""
    from apps.producers.models import Agent
    try:
        lic = admin.admin_license
    except Exception:  # noqa: BLE001
        lic = None
    coops = admin.managed_cooperatives.all()
    return {
        'organization': lic.organization if lic else '',
        'max_cooperatives': lic.max_cooperatives if lic else None,
        'max_agents_per_coop': lic.max_agents_per_coop if lic else None,
        'modules': lic.modules if lic else None,
        'expires_at': lic.expires_at.isoformat() if lic and lic.expires_at else None,
        'is_expired': bool(lic and lic.is_expired),
        'cooperatives_used': coops.count(),
        'agents_used': Agent.objects.filter(cooperative__managed_by=admin).count(),
    }
