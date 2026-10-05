"""
Connexion avec Google (site web et application mobile).

  GET  /api/v1/auth/google/config/   identifiant client web (public) — vide si la connexion Google n'est pas configurée
  POST /api/v1/auth/google/          {"credential": "<jeton d'identité Google>"} → mêmes jetons que /auth/login/

Le jeton d'identité est vérifié auprès de Google (signature, audience = un de nos identifiants client, e-mail vérifié).
Le compte GeoCollect est retrouvé par son adresse e-mail. AUCUN compte n'est créé : une adresse inconnue est refusée
(les comptes sont créés par le propriétaire, les super admins et les coopératives, ce qui préserve le cloisonnement).
"""
import logging

from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import ActivityLog, User
from .serializers import MeSerializer
from .tenancy import tenant_status
from .throttles import LoginThrottle

log = logging.getLogger(__name__)


def client_ids():
    return [c.strip() for c in (getattr(settings, 'GOOGLE_CLIENT_IDS', '') or '').split(',') if c.strip()]


def verify_google_token(credential):
    """Renvoie les informations du jeton (email, name…) ou lève ValueError."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    ids = client_ids()
    if not ids:
        raise ValueError('Connexion Google non configurée.')
    info = id_token.verify_oauth2_token(credential, google_requests.Request(), audience=None, clock_skew_in_seconds=10)
    if info.get('aud') not in ids and info.get('azp') not in ids:
        raise ValueError('Jeton Google émis pour une autre application.')
    if info.get('iss') not in ('accounts.google.com', 'https://accounts.google.com'):
        raise ValueError('Émetteur du jeton inconnu.')
    if not info.get('email') or not info.get('email_verified'):
        raise ValueError('Adresse e-mail Google non vérifiée.')
    return info


class GoogleConfigView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        ids = client_ids()
        return Response({'enabled': bool(ids), 'client_id': ids[0] if ids else ''})


class GoogleLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [LoginThrottle]

    def post(self, request):
        from apps.monitoring.metrics import LOGINS, safe_inc
        from .notify import _device, client_ip, notify_login

        credential = (request.data or {}).get('credential') if isinstance(request.data, dict) else None
        if not credential:
            return Response({'detail': 'Jeton Google manquant.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            info = verify_google_token(credential)
        except ValueError as exc:
            safe_inc(LOGINS, result='echec')
            return Response({'detail': f'Connexion Google refusée : {exc}'}, status=status.HTTP_401_UNAUTHORIZED)
        except Exception:  # noqa: BLE001
            log.exception('Vérification du jeton Google')
            return Response({'detail': 'Google est momentanément injoignable. Réessayez.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        email = info['email'].strip().lower()
        users = list(User.objects.select_related('cooperative').filter(email__iexact=email, is_active=True)[:2])
        if not users:
            safe_inc(LOGINS, result='echec')
            return Response({'detail': f"Aucun compte GeoCollect actif n'est lié à {email}. Demandez à votre administrateur "
                                       "d'enregistrer cette adresse e-mail sur votre compte."}, status=status.HTTP_401_UNAUTHORIZED)
        if len(users) > 1:
            return Response({'detail': f"Plusieurs comptes utilisent {email} : connectez-vous avec votre identifiant et votre "
                                       "mot de passe, puis demandez à votre administrateur de corriger les adresses."},
                            status=status.HTTP_409_CONFLICT)
        user = users[0]
        st = tenant_status(user)
        if not st['active']:
            return Response({'detail': st['reason'], 'code': 'tenant_inactive'}, status=status.HTTP_401_UNAUTHORIZED)

        refresh = RefreshToken.for_user(user)
        safe_inc(LOGINS, result='succes')
        prev = ActivityLog.objects.filter(user=user, action='login').order_by('-timestamp').values_list('ip_address', 'details').first()
        ActivityLog.objects.create(user=user, action='login', resource='auth', details=f'Google · {_device(request)}'[:255],
                                   ip_address=client_ip(request) or None)
        from django.contrib.auth.models import update_last_login
        update_last_login(None, user)
        notify_login(user, request, previous=(prev[0], prev[1].replace('Google · ', '')) if prev and prev[1] else None)
        return Response({'access': str(refresh.access_token), 'refresh': str(refresh), 'user': MeSerializer(user).data})
