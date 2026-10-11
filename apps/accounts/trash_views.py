from django.db import IntegrityError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ActivityLog, Trash
from .permissions import IsOwner
from .purge import erase, purge_expired, restore


def _row(t):
    return {'id': str(t.id), 'label': t.label, 'username': t.username, 'role': t.role,
            'deleted_by': (t.deleted_by.full_name or t.deleted_by.username) if t.deleted_by else None,
            'deleted_at': t.deleted_at, 'expires_at': t.expires_at, 'summary': t.summary,
            'object_count': t.object_count, 'size_kb': round(len(t.payload or b'') / 1024)}


class TrashListView(APIView):
    """GET /api/v1/platform/trash/ : comptes supprimés depuis moins de 30 jours (propriétaire)."""
    permission_classes = [IsOwner]

    def get(self, request):
        purge_expired()
        return Response([_row(t) for t in Trash.objects.select_related('deleted_by')])


class TrashItemView(APIView):
    """
    POST   /api/v1/platform/trash/<id>/restore/  restaure le compte et toutes ses données
    DELETE /api/v1/platform/trash/<id>/          efface définitivement, sans attendre 30 jours
    """
    permission_classes = [IsOwner]

    def post(self, request, pk):
        t = Trash.objects.filter(pk=pk).first()
        if t is None:
            return Response({'detail': 'Élément introuvable (déjà restauré ou expiré).'}, status=status.HTTP_404_NOT_FOUND)
        label = t.label
        try:
            restore(t)
        except IntegrityError:
            return Response({'detail': 'Restauration impossible : un identifiant ou un code de ces données a été réutilisé '
                                       'depuis la suppression (même identifiant de connexion, même code…).'},
                            status=status.HTTP_409_CONFLICT)
        from .tenancy import invalidate_tenant_cache
        invalidate_tenant_cache()
        ActivityLog.objects.create(user=request.user, action='restore_user', resource='trash', details=label,
                                   ip_address=request.META.get('REMOTE_ADDR'))
        return Response({'detail': f'{label} restauré avec toutes ses données.'})

    def delete(self, request, pk):
        t = Trash.objects.filter(pk=pk).first()
        if t is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        label = t.label
        erase(t)
        ActivityLog.objects.create(user=request.user, action='erase_trash', resource='trash', details=label,
                                   ip_address=request.META.get('REMOTE_ADDR'))
        return Response(status=status.HTTP_204_NO_CONTENT)
