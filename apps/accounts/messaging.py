"""
Messages entre les rôles (cloche de notifications) : /api/v1/auth/notifications/targets/ et send/.

Chaque rôle ne peut écrire qu'à sa hiérarchie, jamais à un autre client :
  propriétaire  → tout le monde, tous les super admins, ou un client (super admin + ses coopératives et agents)
  super admin   → toutes ses coopératives (comptes + agents), une de ses coopératives, ou le propriétaire
  coopérative   → ses agents, ou son super admin
  agent         → sa coopérative
"""
from django.db.models import Q
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ActivityLog, User
from .notify import notify_users, owners
from .permissions import managed_cooperatives
from .throttles import MessageThrottle

MAX_RECIPIENTS = 5000


def targets_for(user):
    """[(clé, libellé)] des destinataires possibles."""
    role = user.role
    out = []
    if role == 'owner':
        out += [('all', 'Tous les utilisateurs de la plateforme'), ('super_admins', 'Tous les super admins (clients)')]
        for a in User.objects.filter(role='super_admin', is_active=True).select_related('admin_license').order_by('full_name'):
            org = getattr(getattr(a, 'admin_license', None), 'organization', '') or a.full_name
            out.append((f'client:{a.id}', f'Client {org} (super admin, coopératives et agents)'))
    elif role == 'super_admin':
        out.append(('my_cooperatives', 'Toutes mes coopératives (comptes et agents)'))
        for c in managed_cooperatives(user).order_by('name'):
            out.append((f'coop:{c.id}', f'{c.name} (compte et agents)'))
        out.append(('owner', 'Le propriétaire de la plateforme (GeoLab Service)'))
    elif role == 'cooperative':
        out += [('agents', 'Tous mes agents mappeurs'), ('manager', 'Mon super admin')]
    elif role == 'agent':
        out.append(('cooperative', 'Ma coopérative'))
    return out


def recipients_for(user, target):
    """Destinataires d'une clé, ou None si la clé n'est pas autorisée pour ce rôle."""
    allowed = {k for k, _ in targets_for(user)}
    if target not in allowed:
        return None
    active = User.objects.filter(is_active=True)
    role = user.role
    if role == 'owner':
        if target == 'all':
            return active.exclude(pk=user.pk)
        if target == 'super_admins':
            return active.filter(role='super_admin')
        admin_id = target.split(':', 1)[1]
        return active.filter(Q(pk=admin_id) | Q(cooperative__managed_by_id=admin_id))
    if role == 'super_admin':
        if target == 'owner':
            return active.filter(role='owner')
        if target == 'my_cooperatives':
            return active.filter(cooperative__in=managed_cooperatives(user))
        coop_id = target.split(':', 1)[1]
        return active.filter(cooperative__in=managed_cooperatives(user).filter(pk=coop_id))
    if role == 'cooperative':
        if target == 'agents':
            return active.filter(cooperative_id=user.cooperative_id, role='agent')
        coop = user.cooperative
        return active.filter(pk=coop.managed_by_id) if coop and coop.managed_by_id else active.filter(role='owner')
    if role == 'agent':
        return active.filter(cooperative_id=user.cooperative_id, role='cooperative')
    return None


class MessageSerializer(serializers.Serializer):
    target = serializers.CharField(max_length=80)
    title = serializers.CharField(max_length=120)
    message = serializers.CharField(max_length=1000, allow_blank=True, required=False)
    level = serializers.ChoiceField(choices=['info', 'warning', 'success'], default='info')


class MessageTargetsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response([{'key': k, 'label': v} for k, v in targets_for(request.user)])


class SendMessageView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [MessageThrottle]           # limite l'envoi en rafale

    def post(self, request):
        s = MessageSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        qs = recipients_for(request.user, d['target'])
        if qs is None:
            return Response({'detail': 'Destinataire non autorisé.'}, status=status.HTTP_403_FORBIDDEN)
        ids = list(qs.exclude(pk=request.user.pk).values_list('id', flat=True)[:MAX_RECIPIENTS])
        if not ids:
            return Response({'detail': 'Aucun destinataire actif pour ce choix.'}, status=status.HTTP_400_BAD_REQUEST)
        sender = request.user.full_name or request.user.username
        n = notify_users(ids, ntype=d['level'], title=f'{d["title"]} — message de {sender}', message=d.get('message', ''),
                         cooperative=request.user.cooperative if request.user.role in ('agent', 'cooperative') else None)
        ActivityLog.objects.create(user=request.user, action='send_message', resource='notification',
                                   details=f'{d["target"]} · {n} destinataire(s)', ip_address=request.META.get('REMOTE_ADDR'))
        return Response({'sent': n})
