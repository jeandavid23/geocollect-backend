from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

from .tenancy import tenant_status


class TenantJWTAuthentication(JWTAuthentication):
    """
    Authentification par jeton + contrôle du client : si l'organisation est suspendue, son abonnement expiré
    ou la coopérative désactivée, toutes les requêtes sont refusées (y compris pour un jeton déjà émis).
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        status = tenant_status(user)
        if not status['active']:
            raise AuthenticationFailed({'detail': status['reason'], 'code': 'tenant_inactive'})
        path = request.path
        if token.get('imp_by'):
            # « Voir comme » : consultation en lecture seule, sans les étapes de sécurité du compte consulté
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                raise PermissionDenied({'detail': 'Mode consultation : lecture seule. Revenez à votre compte pour modifier.',
                                        'code': 'read_only'})
            return user, token
        if not any(path.startswith(p) for p in GATE_OPEN):
            if user.must_change_password:
                raise PermissionDenied({'detail': 'Mot de passe provisoire : choisissez votre mot de passe pour continuer.',
                                        'code': 'password_change_required'})
            if user.role in ('owner', 'super_admin') and not user.totp_enabled and getattr(settings, 'MFA_ENFORCED', True):
                raise PermissionDenied({'detail': 'Activez la double authentification pour continuer.', 'code': 'mfa_setup_required'})
        return user, token


# accessibles même avant le changement de mot de passe / l'activation de la double authentification
GATE_OPEN = ('/api/v1/auth/me/', '/api/v1/auth/change-password/', '/api/v1/auth/logout/', '/api/v1/auth/mfa/',
             '/api/v1/monitoring/')
