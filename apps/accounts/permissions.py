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
