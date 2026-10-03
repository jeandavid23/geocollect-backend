"""
Droits d'accès et cloisonnement des données (voir tenancy.py pour la hiérarchie des rôles).

  owner        : tout
  super_admin  : uniquement les coopératives qu'il gère (Cooperative.managed_by = lui)
  cooperative  : uniquement sa coopérative
  agent        : uniquement sa coopérative
"""
from rest_framework.permissions import BasePermission

from .tenancy import has_module

PLATFORM_ADMIN_ROLES = ('owner', 'super_admin')


def is_owner(user):
    return getattr(user, 'role', None) == 'owner'


def is_platform_admin(user):
    return getattr(user, 'role', None) in PLATFORM_ADMIN_ROLES


class IsOwner(BasePermission):
    """Propriétaire de la plateforme (Super Super Admin) uniquement."""
    message = 'Action réservée au propriétaire de la plateforme.'

    def has_permission(self, request, view):
        return request.user.is_authenticated and is_owner(request.user)


class IsSuperAdmin(BasePermission):
    """Super admin ou propriétaire (le propriétaire a tous les droits d'un super admin, sur tous les clients)."""

    def has_permission(self, request, view):
        return request.user.is_authenticated and is_platform_admin(request.user)


class IsCooperativeOrAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ('owner', 'super_admin', 'cooperative')


class IsAgentOrAbove(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated


def module_required(module):
    """Permission : le module doit être activé dans la licence du client de l'utilisateur."""
    from .models import AdminLicense

    class _HasModule(BasePermission):
        message = f"Le module « {AdminLicense.MODULES.get(module, module)} » n'est pas activé pour votre organisation."

        def has_permission(self, request, view):
            return request.user.is_authenticated and has_module(request.user, module)

    _HasModule.__name__ = f'HasModule_{module}'
    return _HasModule


def managed_cooperatives(user):
    """Coopératives accessibles par l'utilisateur."""
    from apps.cooperatives.models import Cooperative

    role = getattr(user, 'role', None)
    if role == 'owner':
        return Cooperative.objects.all()
    if role == 'super_admin':
        return Cooperative.objects.filter(managed_by=user)
    if getattr(user, 'cooperative_id', None):
        return Cooperative.objects.filter(pk=user.cooperative_id)
    return Cooperative.objects.none()


def can_access_cooperative(user, cooperative_id):
    if cooperative_id is None:
        return is_owner(user)
    if is_owner(user):
        return True
    return managed_cooperatives(user).filter(pk=cooperative_id).exists()


class BelongsToCooperative(BasePermission):
    """L'objet doit appartenir à une coopérative accessible par l'utilisateur."""

    def has_object_permission(self, request, view, obj):
        return can_access_cooperative(request.user, getattr(obj, 'cooperative_id', None))


def resolve_cooperative(request, source=None):
    """
    Coopérative ciblée par la requête.
    - coopérative / agent : toujours la leur (impossible d'en viser une autre) ;
    - super admin : celle passée en paramètre `cooperative`, si elle fait partie de celles qu'il gère ;
    - propriétaire : n'importe quelle coopérative passée en paramètre.
    Renvoie None si aucune n'est déterminée ou autorisée.
    """
    user = request.user
    if user.role in ('cooperative', 'agent'):
        return user.cooperative
    coop_id = (source or {}).get('cooperative') or request.query_params.get('cooperative')
    if not coop_id:
        return None
    try:
        return managed_cooperatives(user).filter(pk=coop_id).first()
    except Exception:  # noqa: BLE001 — identifiant mal formé
        return None


def scope_to_cooperative(qs, user, field='cooperative_id'):
    """
    Cloisonnement d'une requête : chacun ne voit que les objets des coopératives auxquelles il a accès
    (sinon 404, comme si l'objet n'existait pas). `field` : champ coopérative de l'objet ('cooperative_id',
    ou 'id' pour la coopérative elle-même). Une seule requête SQL (jointure), sans aller-retour supplémentaire.
    """
    role = getattr(user, 'role', None)
    if role == 'owner':
        return qs
    if role == 'super_admin':
        if field == 'id':
            return qs.filter(managed_by=user)
        prefix = field[:-3] if field.endswith('_id') else field
        return qs.filter(**{f'{prefix}__managed_by': user})
    if not getattr(user, 'cooperative_id', None):
        return qs.none()
    return qs.filter(**{field: user.cooperative_id})


def scope_users(qs, user):
    """Comptes visibles : propriétaire → tous ; super admin → lui-même et les comptes de ses coopératives."""
    from django.db.models import Q

    role = getattr(user, 'role', None)
    if role == 'owner':
        return qs
    if role == 'super_admin':
        return qs.filter(Q(pk=user.pk) | Q(cooperative__managed_by=user))
    return qs.filter(pk=user.pk)


def can_manage_user(actor, target):
    """Le propriétaire gère tout le monde ; un super admin gère les coopératives et agents de ses coopératives."""
    if is_owner(actor):
        return True
    if actor.role == 'super_admin':
        return target.role in ('cooperative', 'agent') and target.cooperative is not None \
            and target.cooperative.managed_by_id == actor.pk
    if actor.role == 'cooperative':
        return target.role == 'agent' and target.cooperative_id == actor.cooperative_id
    return False
