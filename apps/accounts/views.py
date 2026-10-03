from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User, ActivityLog, Notification
from .serializers import (
    CustomTokenObtainPairSerializer, UserSerializer, MeSerializer,
    UserCreateSerializer, ChangePasswordSerializer, ActivityLogSerializer,
    NotificationSerializer,
)
from .permissions import IsSuperAdmin, IsOwner, scope_users, can_manage_user
from .throttles import LoginThrottle, PasswordThrottle


class LoginView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = [LoginThrottle]   # 10 tentatives / minute par identifiant visé

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            user_data = response.data.get('user', {})
            ActivityLog.objects.create(
                user_id=user_data.get('id'),
                action='login',
                resource='auth',
                ip_address=request.META.get('REMOTE_ADDR'),
            )
        return response


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            token = RefreshToken(request.data.get('refresh'))
            token.blacklist()
            ActivityLog.objects.create(
                user=request.user, action='logout', resource='auth',
                ip_address=request.META.get('REMOTE_ADDR'),
            )
            return Response({'detail': 'Déconnexion réussie.'})
        except Exception:
            return Response({'detail': 'Token invalide.'}, status=status.HTTP_400_BAD_REQUEST)


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = MeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [PasswordThrottle]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data['new_password'])
        request.user.save()
        return Response({'detail': 'Mot de passe modifié.'})


class UserListCreateView(generics.ListCreateAPIView):
    """
    GET  : comptes visibles (propriétaire → tous ; super admin → lui-même et ses coopératives/agents).
    POST : réservé au propriétaire (les super admins créent leurs comptes via coopératives et agents ;
           les super admins se créent via /platform/admins/).
    """
    def get_permissions(self):
        return [IsOwner()] if self.request.method == 'POST' else [IsSuperAdmin()]

    def get_queryset(self):
        return scope_users(User.objects.select_related('cooperative').all(), self.request.user)

    def get_serializer_class(self):
        return UserCreateSerializer if self.request.method == 'POST' else UserSerializer


class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsSuperAdmin]

    def get_queryset(self):
        return scope_users(User.objects.select_related('cooperative').all(), self.request.user)

    def _check(self, target):
        from rest_framework.exceptions import PermissionDenied
        if target.pk == self.request.user.pk:
            raise PermissionDenied('Utilisez « Mon compte » pour modifier votre propre profil.')
        if not can_manage_user(self.request.user, target):
            raise PermissionDenied("Vous n'avez pas le droit de gérer ce compte.")

    def perform_update(self, serializer):
        self._check(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._check(instance)
        if instance.role == 'owner':
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Le compte propriétaire ne peut pas être supprimé.')
        instance.delete()


class ToggleUserActiveView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, pk):
        from .tenancy import invalidate_tenant_cache
        user = generics.get_object_or_404(scope_users(User.objects.all(), request.user), pk=pk)
        if user.pk == request.user.pk or user.role == 'owner' or not can_manage_user(request.user, user):
            return Response({'detail': 'Action non autorisée.'}, status=status.HTTP_403_FORBIDDEN)
        user.is_active = not user.is_active
        user.save(update_fields=['is_active'])
        invalidate_tenant_cache()
        return Response({'is_active': user.is_active})


class ResetPasswordView(APIView):
    """Régénère un mot de passe. Super admin → tout le monde ;
    Coopérative → ses propres agents uniquement. Renvoie le nouveau mot de passe."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from .credentials import generate_password
        target = generics.get_object_or_404(User, pk=pk)
        requester = request.user

        # Propriétaire → tout le monde ; super admin → comptes de ses coopératives ;
        # coopérative → ses agents. Jamais le compte propriétaire (sauf par lui-même via « Mon compte »).
        allowed = target.pk != requester.pk and target.role != 'owner' and can_manage_user(requester, target)
        if not allowed:
            return Response({'detail': 'Action non autorisée.'}, status=status.HTTP_403_FORBIDDEN)

        new_password = generate_password()
        target.set_password(new_password)
        target.save()
        ActivityLog.objects.create(
            user=requester, action='reset_password', resource='user',
            resource_id=str(target.id), ip_address=request.META.get('REMOTE_ADDR'),
        )
        # Renvoie aussi par email si une adresse existe
        try:
            from .emails import send_credentials_email
            send_credentials_email(
                to_email=target.email, full_name=target.full_name,
                role_label=target.get_role_display(),
                username=target.username, password=new_password,
            )
        except Exception:
            pass
        return Response({
            'username': target.username,
            'new_password': new_password,
            'full_name': target.full_name,
        })


class ActivityLogListView(generics.ListAPIView):
    serializer_class = ActivityLogSerializer
    permission_classes = [IsSuperAdmin]

    def get_queryset(self):
        qs = ActivityLog.objects.select_related('user').order_by('-timestamp')
        if self.request.user.role != 'owner':
            # super admin : journaux de ses propres comptes uniquement
            qs = qs.filter(user__in=scope_users(User.objects.all(), self.request.user))
        user_id = self.request.query_params.get('user')
        if user_id:
            qs = qs.filter(user_id=user_id)
        return qs


class NotificationListView(generics.ListAPIView):
    """Notifications de l'utilisateur connecté (le super admin reçoit tout via notify_cooperative)."""
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)[:100]


class NotificationMarkReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        return Response({'detail': 'Notifications marquées comme lues.'})
