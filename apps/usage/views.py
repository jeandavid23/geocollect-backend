"""
Statistiques d'utilisation — /api/v1/usage/
  track/      POST {events: [{path, session, at}]} : pages vues de l'application web (le compte connecté)
  overview/   GET ?days=7|30|90[&cooperative=<id>][&admin=<id>]
              propriétaire : tous les comptes (ou un client, ou une coopérative) ;
              super admin : lui-même et les comptes de ses coopératives et usines ;
              coopérative : ses comptes (coopérative et agents).
"""
import uuid

from django.db.models import Q
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import scope_users
from .analytics import overview
from .tracking import record

PERIODS = (7, 30, 90)


class TrackThrottle(UserRateThrottle):
    scope = 'usage'


class TrackView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [TrackThrottle]

    def post(self, request):
        if getattr(request.auth, 'get', lambda *_: None)('imp_by'):       # « Voir comme » : pas compté
            return Response({'recorded': 0})
        events = request.data.get('events') if isinstance(request.data, dict) else None
        if not isinstance(events, list):
            return Response({'detail': 'Liste « events » attendue.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'recorded': record(request.user, events, request.META.get('HTTP_USER_AGENT'))})


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def accounts_for(user):
    if user.role in ('owner', 'super_admin'):
        return scope_users(User.objects.all(), user)
    if user.role == 'cooperative' and user.cooperative_id:
        return User.objects.filter(cooperative_id=user.cooperative_id)
    return None


class OverviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        accounts = accounts_for(request.user)
        if accounts is None:
            return Response({'detail': 'Réservé aux administrateurs et aux coopératives.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            days = int(request.query_params.get('days') or 30)
        except ValueError:
            days = 0
        if days not in PERIODS:
            return Response({'detail': 'Période : 7, 30 ou 90 jours.'}, status=status.HTTP_400_BAD_REQUEST)
        for param, field in (('cooperative', 'cooperative_id'), ('admin', None)):
            raw = request.query_params.get(param)
            if not raw:
                continue
            value = _uuid(raw)
            if value is None:
                return Response({'detail': 'Identifiant invalide.'}, status=status.HTTP_400_BAD_REQUEST)
            accounts = accounts.filter(**{field: value}) if field else accounts.filter(
                Q(pk=value) | Q(cooperative__managed_by_id=value) | Q(plant__managed_by_id=value))
        return Response(overview(accounts, days))
