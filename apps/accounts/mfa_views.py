from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import mfa
from .models import ActivityLog, User
from .permissions import IsOwner
from .session import open_session
from .throttles import MfaThrottle


class MfaVerifyView(APIView):
    """POST /api/v1/auth/mfa/verify/ {mfa_token, code} : 2e étape de connexion → jetons de session."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [MfaThrottle]

    def post(self, request):
        user = mfa.user_from_login_token(request.data.get('mfa_token') or '')
        if user is None:
            return Response({'detail': 'Session expirée : reconnectez-vous.', 'code': 'mfa_expired'}, status=status.HTTP_401_UNAUTHORIZED)
        if not mfa.check_code(user, request.data.get('code')):
            ActivityLog.objects.create(user=user, action='mfa_failed', resource='auth', ip_address=request.META.get('REMOTE_ADDR'))
            return Response({'detail': 'Code incorrect ou expiré.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(open_session(user, request, method='2FA · '))


class MfaSetupView(APIView):
    """
    GET  /api/v1/auth/mfa/setup/          nouveau secret + lien otpauth (QR code) — pas encore actif
    POST /api/v1/auth/mfa/setup/ {code}   active la double authentification → codes de secours (affichés une fois)
    DELETE /api/v1/auth/mfa/setup/ {password, code}  désactive (impossible pour un rôle où elle est obligatoire)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        u = request.user
        if u.totp_enabled:
            return Response({'enabled': True, 'recovery_left': len(u.totp_recovery or [])})
        u.totp_secret = mfa.new_secret()
        u.save(update_fields=['totp_secret'])
        return Response({'enabled': False, 'secret': u.totp_secret, 'uri': mfa.otpauth_uri(u, u.totp_secret)})

    def post(self, request):
        u = request.user
        if u.totp_enabled:
            return Response({'detail': 'Double authentification déjà active.'}, status=status.HTTP_400_BAD_REQUEST)
        if not mfa.verify_totp(u.totp_secret, request.data.get('code')):
            return Response({'detail': "Code incorrect : vérifiez l'heure du téléphone et réessayez."}, status=status.HTTP_400_BAD_REQUEST)
        codes, hashes = mfa.new_recovery_codes()
        u.totp_enabled, u.totp_recovery = True, hashes
        u.save(update_fields=['totp_enabled', 'totp_recovery'])
        ActivityLog.objects.create(user=u, action='mfa_enabled', resource='auth', ip_address=request.META.get('REMOTE_ADDR'))
        return Response({'enabled': True, 'recovery_codes': codes})

    def delete(self, request):
        u = request.user
        if u.role in mfa.MFA_ROLES:
            return Response({'detail': 'La double authentification est obligatoire pour votre rôle.'}, status=status.HTTP_400_BAD_REQUEST)
        if not u.check_password(request.data.get('password') or '') or not mfa.check_code(u, request.data.get('code')):
            return Response({'detail': 'Mot de passe ou code incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
        u.totp_enabled, u.totp_secret, u.totp_recovery = False, '', []
        u.save(update_fields=['totp_enabled', 'totp_secret', 'totp_recovery'])
        return Response({'enabled': False})


class MfaResetView(APIView):
    """POST /api/v1/auth/users/<id>/reset-mfa/ : le propriétaire réinitialise la double authentification (téléphone perdu)."""
    permission_classes = [IsOwner]

    def post(self, request, pk):
        u = User.objects.filter(pk=pk).first()
        if u is None or u.pk == request.user.pk:
            return Response({'detail': 'Action non autorisée.'}, status=status.HTTP_403_FORBIDDEN)
        u.totp_enabled, u.totp_secret, u.totp_recovery = False, '', []
        u.save(update_fields=['totp_enabled', 'totp_secret', 'totp_recovery'])
        ActivityLog.objects.create(user=request.user, action='mfa_reset', resource='user', resource_id=str(u.pk),
                                   details=u.username, ip_address=request.META.get('REMOTE_ADDR'))
        return Response({'detail': 'Double authentification réinitialisée : elle sera reconfigurée à la prochaine connexion.'})
