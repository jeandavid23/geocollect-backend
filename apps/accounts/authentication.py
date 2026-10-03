from rest_framework.exceptions import AuthenticationFailed
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
        return user, token
