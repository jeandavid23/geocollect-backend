from rest_framework.permissions import BasePermission


class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'super_admin'


class IsCooperativeOrAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ('super_admin', 'cooperative')


class IsAgentOrAbove(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated


class BelongsToCooperative(BasePermission):
    """Cooperative users can only see their own cooperative's data."""

    def has_object_permission(self, request, view, obj):
        if request.user.role == 'super_admin':
            return True
        cooperative_id = getattr(obj, 'cooperative_id', None)
        return cooperative_id and cooperative_id == request.user.cooperative_id


def resolve_cooperative(request, source=None):
    """
    Coopérative ciblée par la requête.
    - coopérative / agent : toujours la leur (impossible d'en viser une autre) ;
    - super admin : celle passée en paramètre `cooperative` (query string ou corps).
    Renvoie None si aucune n'est déterminée.
    """
    from apps.cooperatives.models import Cooperative

    user = request.user
    if user.role in ('cooperative', 'agent'):
        return user.cooperative
    coop_id = (source or {}).get('cooperative') or request.query_params.get('cooperative')
    if not coop_id:
        return None
    return Cooperative.objects.filter(pk=coop_id).first()


def scope_to_cooperative(qs, user, field='cooperative_id'):
    """
    Cloisonnement : une coopérative ou un agent ne voit QUE les objets de sa coopérative
    (sinon 404, comme si l'objet n'existait pas). Le super admin voit tout.
    """
    if getattr(user, 'role', None) == 'super_admin':
        return qs
    if not getattr(user, 'cooperative_id', None):
        return qs.none()
    return qs.filter(**{field: user.cooperative_id})
