from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import User, ActivityLog


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """JWT login — retourne les infos utilisateur avec les tokens."""

    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user
        data['user'] = UserSerializer(user).data
        return data


class UserSerializer(serializers.ModelSerializer):
    cooperative_id = serializers.SerializerMethodField()
    cooperative_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'full_name', 'phone',
            'role', 'is_active', 'cooperative_id', 'cooperative_name',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_cooperative_id(self, obj):
        return str(obj.cooperative_id) if obj.cooperative_id else None

    def get_cooperative_name(self, obj):
        return obj.cooperative.name if obj.cooperative else None


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6)

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
    new_password = serializers.CharField(required=True, min_length=6)

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
