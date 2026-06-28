from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User, ActivityLog, Notification
from .serializers import (
    CustomTokenObtainPairSerializer, UserSerializer,
    UserCreateSerializer, ChangePasswordSerializer, ActivityLogSerializer,
    NotificationSerializer,
)
from .permissions import IsSuperAdmin


class LoginView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

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
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data['new_password'])
        request.user.save()
        return Response({'detail': 'Mot de passe modifié.'})


class UserListCreateView(generics.ListCreateAPIView):
    queryset = User.objects.all()
    permission_classes = [IsSuperAdmin]

    def get_serializer_class(self):
        return UserCreateSerializer if self.request.method == 'POST' else UserSerializer


class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsSuperAdmin]


class ToggleUserActiveView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, pk):
        user = generics.get_object_or_404(User, pk=pk)
        user.is_active = not user.is_active
        user.save()
        return Response({'is_active': user.is_active})


class ResetPasswordView(APIView):
    """Régénère un mot de passe. Super admin → tout le monde ;
    Coopérative → ses propres agents uniquement. Renvoie le nouveau mot de passe."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from .credentials import generate_password
        target = generics.get_object_or_404(User, pk=pk)
        requester = request.user

        allowed = (
            requester.role == 'super_admin'
            or (requester.role == 'cooperative'
                and target.role == 'agent'
                and target.cooperative_id == requester.cooperative_id)
        )
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
