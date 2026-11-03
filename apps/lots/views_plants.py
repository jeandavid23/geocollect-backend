"""
Usines — /api/v1/lots/plants/
  GET                    propriétaire : toutes ; super admin : les siennes ; coopérative : celles qu'elle livre ;
                         usine : la sienne
  POST                   super admin / propriétaire : {name, location, cooperatives: [ids]}
  PATCH <id>/            {name, location, cooperatives, is_active, traceability_mode, gap_alert_pct, loss_alert_pct}
  POST  <id>/account/    crée un compte « usine » {username, full_name, email, phone, plant_role} → mot de passe provisoire
"""
import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.credentials import generate_password
from apps.accounts.models import ActivityLog, User
from apps.accounts.permissions import IsCooperativeAdminOrPlant, IsSuperAdmin, managed_cooperatives
from apps.accounts.tenancy import invalidate_tenant_cache
from .models import Plant


class _ReadAnyWriteAdmin(BasePermission):
    def has_permission(self, request, view):
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return IsCooperativeAdminOrPlant().has_permission(request, view)
        return IsSuperAdmin().has_permission(request, view)


def visible_plants(user):
    qs = Plant.objects.prefetch_related('cooperatives', 'accounts')
    if user.role == 'owner':
        return qs
    if user.role == 'super_admin':
        return qs.filter(managed_by=user)
    if user.role == 'usine':
        return qs.filter(pk=user.plant_id)
    if user.role == 'cooperative' and user.cooperative_id:
        return qs.filter(cooperatives=user.cooperative_id, is_active=True)
    return qs.none()


def serialize(p, with_accounts=False):
    out = {'id': str(p.id), 'name': p.name, 'location': p.location, 'is_active': p.is_active,
           'traceability_mode': p.traceability_mode, 'gap_alert_pct': float(p.gap_alert_pct),
           'loss_alert_pct': float(p.loss_alert_pct),
           'cooperatives': [{'id': str(c.id), 'name': c.name} for c in p.cooperatives.all()]}
    if with_accounts:
        out['accounts'] = [{'id': str(u.id), 'username': u.username, 'full_name': u.full_name, 'is_active': u.is_active,
                            'plant_role': u.plant_role or 'responsable'} for u in p.accounts.all()]
    return out


def _coops(user, ids):
    """Coopératives demandées, si toutes sont gérées par l'utilisateur ; sinon None."""
    ids = {str(i) for i in ids or []}
    found = list(managed_cooperatives(user).filter(id__in=ids))
    return found if len(found) == len(ids) else None


ALERT_MAX_PCT = 50


def _apply_settings(plant, data):
    """Mode de traçabilité et seuils d'alerte ; renvoie un message d'erreur, ou None."""
    if 'traceability_mode' in data:
        if data['traceability_mode'] not in Plant.TRACE_MODES:
            return 'Mode de traçabilité inconnu (bilan_masse ou segregue).'
        plant.traceability_mode = data['traceability_mode']
    for field in ('gap_alert_pct', 'loss_alert_pct'):
        if field in data:
            try:
                value = Decimal(str(data[field]))
            except (InvalidOperation, ValueError):
                return "Seuil d'alerte invalide."
            if not 0 < value <= ALERT_MAX_PCT:
                return f"Le seuil d'alerte doit être compris entre 0 et {ALERT_MAX_PCT} %."
            setattr(plant, field, value)
    return None


class PlantListView(APIView):
    permission_classes = [_ReadAnyWriteAdmin]

    def get(self, request):
        admin = request.user.role in ('owner', 'super_admin')
        return Response([serialize(p, admin) for p in visible_plants(request.user)])

    def post(self, request):
        name = (request.data.get('name') or '').strip()
        if not name:
            return Response({'detail': "Indiquez le nom de l'usine."}, status=status.HTTP_400_BAD_REQUEST)
        coops = _coops(request.user, request.data.get('cooperatives'))
        if coops is None:
            return Response({'detail': 'Coopérative inconnue ou hors de votre périmètre.'}, status=status.HTTP_400_BAD_REQUEST)
        manager = request.user if request.user.role == 'super_admin' else None
        plant = Plant.objects.create(name=name, location=(request.data.get('location') or '').strip(), managed_by=manager)
        plant.cooperatives.set(coops)
        ActivityLog.objects.create(user=request.user, action='create_plant', resource='plant', resource_id=str(plant.id),
                                   details=name)
        return Response(serialize(plant, True), status=status.HTTP_201_CREATED)


class PlantDetailView(APIView):
    permission_classes = [IsSuperAdmin]

    def patch(self, request, pk):
        plant = visible_plants(request.user).filter(pk=pk).first()
        if plant is None:
            return Response({'detail': 'Usine introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        for field in ('name', 'location'):
            if field in request.data:
                setattr(plant, field, (request.data[field] or '').strip())
        if 'is_active' in request.data:
            plant.is_active = bool(request.data['is_active'])
        error = _apply_settings(plant, request.data)
        if error:
            return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)
        if not plant.name:
            return Response({'detail': "Indiquez le nom de l'usine."}, status=status.HTTP_400_BAD_REQUEST)
        if 'cooperatives' in request.data:
            coops = _coops(request.user, request.data['cooperatives'])
            if coops is None:
                return Response({'detail': 'Coopérative inconnue ou hors de votre périmètre.'},
                                status=status.HTTP_400_BAD_REQUEST)
            plant.cooperatives.set(coops)
        plant.save()
        invalidate_tenant_cache()                      # une usine désactivée bloque ses comptes tout de suite
        return Response(serialize(plant, True))


class PlantAccountView(APIView):
    permission_classes = [IsSuperAdmin]

    @transaction.atomic
    def post(self, request, pk):
        plant = visible_plants(request.user).filter(pk=pk).first()
        if plant is None:
            return Response({'detail': 'Usine introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        username = (request.data.get('username') or '').strip()
        full_name = (request.data.get('full_name') or '').strip() or plant.name
        if not username:
            return Response({'detail': "Indiquez l'identifiant de connexion."}, status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(username__iexact=username).exists():
            return Response({'detail': 'Cet identifiant est déjà utilisé.'}, status=status.HTTP_400_BAD_REQUEST)
        email = (request.data.get('email') or '').strip() or None
        if email and User.objects.filter(email__iexact=email).exists():
            return Response({'detail': 'Cette adresse e-mail est déjà utilisée.'}, status=status.HTTP_400_BAD_REQUEST)
        plant_role = request.data.get('plant_role') or 'responsable'
        if plant_role not in User.PLANT_ROLES:
            return Response({'detail': "Rôle inconnu (responsable ou pesee)."}, status=status.HTTP_400_BAD_REQUEST)
        password = generate_password(12)
        user = User.objects.create_user(username=username, password=password, full_name=full_name, email=email,
                                        phone=(request.data.get('phone') or '').strip(), role='usine', plant=plant,
                                        plant_role=plant_role)
        user.must_change_password = True
        user.save(update_fields=['must_change_password'])
        ActivityLog.objects.create(user=request.user, action='create_plant_account', resource='user',
                                   resource_id=str(user.id), details=f'{username} — {plant.name}')
        return Response({'id': str(user.id), 'username': username, 'full_name': full_name, 'plant_role': plant_role,
                         'temporary_password': password}, status=status.HTTP_201_CREATED)

    def patch(self, request, pk):
        """Change le rôle d'un compte de l'usine : {user, plant_role}."""
        plant = visible_plants(request.user).filter(pk=pk).first()
        if plant is None:
            return Response({'detail': 'Usine introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        account = plant.accounts.filter(pk=request.data.get('user')).first() if _is_uuid(request.data.get('user')) else None
        if account is None:
            return Response({'detail': "Compte introuvable dans cette usine."}, status=status.HTTP_404_NOT_FOUND)
        if request.data.get('plant_role') not in User.PLANT_ROLES:
            return Response({'detail': "Rôle inconnu (responsable ou pesee)."}, status=status.HTTP_400_BAD_REQUEST)
        account.plant_role = request.data['plant_role']
        account.save(update_fields=['plant_role'])
        ActivityLog.objects.create(user=request.user, action='update_plant_account', resource='user',
                                   resource_id=str(account.id), details=f'{account.username} — {account.plant_role}')
        return Response({'id': str(account.id), 'plant_role': account.plant_role})


def _is_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False
