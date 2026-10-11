"""
Suppression d'un compte AVEC toutes ses données (règle de la plateforme : un utilisateur supprimé ne laisse rien en base).

  - agent mappeur      : ses parcelles mappées, les anciens polygones qu'il a importés, ses rapports, ses journaux ;
  - coopérative        : si c'est le dernier compte de la coopérative, TOUTE la coopérative (producteurs, registre,
                         parcelles, anciens polygones, agents et leurs comptes, fiches de lot, rapports, documents) ;
                         sinon ses rapports, polygones importés et journaux ;
  - super admin        : toutes les coopératives qu'il gère, comme ci-dessus, et sa licence ;
  - propriétaire       : ses journaux et notifications.
Les fichiers (logos, documents, avatars) sont effacés du stockage. Les notifications du compte partent avec lui.
`plan()` calcule ce qui sera supprimé (affiché avant confirmation) ; `purge()` supprime, en une transaction.
"""
from django.db import transaction
from django.db.models import Q

KEYS = ['cooperatives', 'accounts', 'producers', 'parcels', 'legacy_polygons', 'registry_sheets', 'lots', 'reports', 'documents', 'logs']


def _coops_wiped(user):
    from apps.cooperatives.models import Cooperative
    from .models import User
    if user.role == 'super_admin':
        return list(Cooperative.objects.filter(managed_by=user))
    if user.role == 'cooperative' and user.cooperative_id:
        others = User.objects.filter(role='cooperative', cooperative_id=user.cooperative_id).exclude(pk=user.pk)
        if not others.exists():
            return [user.cooperative]
    return []


def plan(user):
    """Nombre d'éléments supprimés avec ce compte, par type, et noms des coopératives effacées."""
    from apps.lots.models import Lot
    from apps.parcels.models import LegacyParcel, Parcel
    from apps.producers.models import Producer
    from apps.registry.models import RegistrySheet
    from apps.reports.models import AnalysisReport
    from apps.cooperatives.models import CooperativeDocument
    from .models import ActivityLog, User

    coops = _coops_wiped(user)
    ids = [c.id for c in coops]
    agent = getattr(user, 'agent_profile', None) if user.role == 'agent' else None
    in_coops = Q(cooperative_id__in=ids)
    accounts = User.objects.filter(cooperative_id__in=ids).exclude(pk=user.pk)
    return {
        'user': user.username,
        'role': user.role,
        'cooperative_names': [c.name for c in coops],
        'cooperatives': len(coops),
        'accounts': 1 + accounts.count(),
        'producers': Producer.objects.filter(in_coops).count(),
        'parcels': Parcel.objects.filter(in_coops | Q(agent=agent) if agent else in_coops).count(),
        'legacy_polygons': LegacyParcel.objects.filter(in_coops | Q(uploaded_by=user)).count(),
        'registry_sheets': RegistrySheet.objects.filter(in_coops).count(),
        'lots': Lot.objects.filter(in_coops).count(),
        'reports': AnalysisReport.objects.filter(in_coops | Q(created_by=user)).count(),
        'documents': CooperativeDocument.objects.filter(in_coops).count(),
        'logs': ActivityLog.objects.filter(Q(user=user) | Q(user__in=accounts)).count(),
    }


def _delete_files(qs, field):
    for obj in qs:
        f = getattr(obj, field, None)
        if f:
            try:
                f.delete(save=False)
            except Exception:  # noqa: BLE001 — fichier déjà absent du stockage
                pass


def purge(user):
    """Supprime le compte et toutes ses données. Renvoie le plan effectivement appliqué."""
    from apps.cooperatives.models import Cooperative, CooperativeDocument
    from apps.lots.models import Lot
    from apps.parcels.models import LegacyParcel, Parcel
    from apps.reports.models import AnalysisReport
    from .models import ActivityLog, User

    done = plan(user)
    coops = _coops_wiped(user)
    ids = [c.id for c in coops]
    agent = getattr(user, 'agent_profile', None) if user.role == 'agent' else None
    with transaction.atomic():
        if agent is not None:
            Parcel.objects.filter(agent=agent).delete()
        LegacyParcel.objects.filter(uploaded_by=user).delete()
        AnalysisReport.objects.filter(created_by=user).delete()
        if ids:
            # fiches de lot d'abord : leurs lignes protègent les producteurs
            Lot.objects.filter(cooperative_id__in=ids).delete()
            accounts = User.objects.filter(cooperative_id__in=ids).exclude(pk=user.pk)
            ActivityLog.objects.filter(user__in=accounts).delete()
            _delete_files(accounts, 'avatar')
            accounts.delete()                                     # agents et comptes de la coopérative
            _delete_files(CooperativeDocument.objects.filter(cooperative_id__in=ids), 'file')
            _delete_files(Cooperative.objects.filter(id__in=ids), 'logo')
            Cooperative.objects.filter(id__in=ids).delete()      # producteurs, parcelles, registre, rapports…
        ActivityLog.objects.filter(user=user).delete()
        _delete_files([user], 'avatar')
        user.delete()                                             # notifications, profil agent, licence
    return done
