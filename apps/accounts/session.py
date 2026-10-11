"""Ouverture de session commune (mot de passe, Google, double authentification) : journal, alertes, jetons."""
from rest_framework_simplejwt.tokens import RefreshToken


def open_session(user, request, method=''):
    from django.contrib.auth.models import update_last_login
    from apps.monitoring.metrics import LOGINS, safe_inc
    from .models import ActivityLog
    from .notify import _device, client_ip, notify_login
    from .serializers import MeSerializer
    prev = ActivityLog.objects.filter(user=user, action='login').order_by('-timestamp').values_list('ip_address', 'details').first()
    ActivityLog.objects.create(user=user, action='login', resource='auth', details=f'{method}{_device(request)}'[:255],
                               ip_address=client_ip(request) or None)
    update_last_login(None, user)
    safe_inc(LOGINS, result='succes')
    try:
        notify_login(user, request, previous=(prev[0], prev[1].split(' · ')[-1]) if prev and prev[1] else None)
    except Exception:  # noqa: BLE001 — une alerte en échec ne bloque pas la connexion
        pass
    refresh = RefreshToken.for_user(user)
    return {'access': str(refresh.access_token), 'refresh': str(refresh), 'user': MeSerializer(user).data}


def mfa_challenge(user):
    """Réponse de 1re étape quand la double authentification est active : pas encore de jeton de session."""
    from .mfa import login_token
    return {'mfa_required': True, 'mfa_token': login_token(user), 'user': {'full_name': user.full_name, 'username': user.username}}
