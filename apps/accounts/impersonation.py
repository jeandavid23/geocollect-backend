"""
« Voir comme » : le propriétaire (tous les comptes sauf propriétaires) ou un super admin (comptes de SES coopératives)
ouvre l'espace d'un utilisateur EN LECTURE SEULE, pour le support. Jeton d'accès d'une heure, sans renouvellement,
marqué `imp_by` (auteur) et `ro` (lecture seule) ; toute écriture est refusée par l'authentification.
"""
from datetime import timedelta

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from .models import ActivityLog, User
from .permissions import can_manage_user


class ImpersonateView(APIView):
    """POST /api/v1/auth/impersonate/ {"user": "<uuid>"} → {access, user} (lecture seule, 1 h)."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from .serializers import MeSerializer
        actor = request.user
        if getattr(request.auth, 'get', lambda *_: None)('imp_by'):
            return Response({'detail': 'Revenez d’abord à votre compte.'}, status=status.HTTP_400_BAD_REQUEST)
        target = User.objects.filter(pk=request.data.get('user'), is_active=True).select_related('cooperative').first()
        allowed = (target is not None and target.pk != actor.pk and target.role != 'owner'
                   and actor.role in ('owner', 'super_admin') and can_manage_user(actor, target)
                   and (actor.role == 'owner' or target.role in ('cooperative', 'agent')))
        if not allowed:
            return Response({'detail': 'Vous ne pouvez pas consulter ce compte.'}, status=status.HTTP_403_FORBIDDEN)
        token = AccessToken.for_user(target)
        token.set_exp(lifetime=timedelta(hours=1))
        token['imp_by'] = str(actor.pk)
        token['ro'] = True
        ActivityLog.objects.create(user=actor, action='impersonate', resource='user', resource_id=str(target.pk),
                                   details=f'Consultation (lecture seule) de {target.username}', ip_address=request.META.get('REMOTE_ADDR'))
        data = MeSerializer(target).data
        # le compte consulté n'a pas à changer son mot de passe ni à configurer la double authentification pour être consulté
        data.update(must_change_password=False, mfa_required=False)
        return Response({'access': str(token), 'user': data, 'read_only': True,
                         'impersonator': actor.full_name or actor.username})
