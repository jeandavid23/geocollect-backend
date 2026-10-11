"""
Suppression d'un compte AVEC toutes ses données (règle de la plateforme : un utilisateur supprimé ne laisse rien en base).

  - agent mappeur      : ses parcelles mappées, les anciens polygones qu'il a importés, ses rapports, ses journaux ;
  - coopérative        : si c'est le dernier compte de la coopérative, TOUTE la coopérative (producteurs, registre,
                         parcelles, anciens polygones, agents et leurs comptes, fiches de lot, rapports, documents) ;
                         sinon ses rapports, polygones importés et journaux ;
  - super admin        : toutes les coopératives qu'il gère, comme ci-dessus, et sa licence ;
  - propriétaire       : ses journaux et notifications.
Les fichiers (logos, documents, avatars) sont effacés du stockage. Les notifications du compte partent avec lui.
`plan()` calcule ce qui sera supprimé (affiché avant confirmation) ; `purge()` supprime, en une transaction, et garde
une copie compressée 30 jours dans la corbeille (`Trash`) : le propriétaire peut tout restaurer pendant ce délai.
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


class _Snapshot:
    """
    Supprime par étapes (avec les mêmes cascades que Django) en gardant une copie de TOUT ce qui disparaît :
    objets supprimés, liens remis à vide (SET_NULL) et fichiers (effacés seulement à l'expiration de la corbeille).
    """

    def __init__(self):
        self.objects = {}      # (modèle, pk) → objet sérialisé (copie faite AVANT la suppression)
        self.relinks = []      # [label du modèle, pk, attribut, ancienne valeur] (liens SET_NULL à rétablir)
        self.files = []        # chemins de fichiers à effacer à l'expiration

    def delete(self, objs, files=()):
        from django.db.models.deletion import Collector
        objs = list(objs)
        if not objs:
            return
        for obj in objs:
            for field in files:
                f = getattr(obj, field, None)
                if f:
                    self.files.append(f.name)
        import json
        from django.core import serializers
        c = Collector(using='default')
        c.collect(objs)
        doomed = [i for instances in c.data.values() for i in instances] + [i for qs in c.fast_deletes for i in qs]
        for d in json.loads(serializers.serialize('json', doomed)):
            self.objects[(d['model'], d['pk'])] = d
        from django.db.models import QuerySet
        for key, groups in c.field_updates.items():
            field = key[0]
            for group in groups:                      # objets ou requêtes, selon la cascade
                rows = group if isinstance(group, QuerySet) else [group]
                for inst in rows:
                    old = getattr(inst, field.attname)
                    if old is not None:
                        self.relinks.append([inst._meta.label, str(inst.pk), field.attname, str(old)])
        c.delete()

    def payload(self):
        import gzip
        import json
        data = {'objects': list(self.objects.values()), 'relinks': self.relinks}
        return gzip.compress(json.dumps(data, ensure_ascii=False, default=str).encode()), len(self.objects)


def purge(user, deleted_by=None):
    """
    Supprime le compte et toutes ses données ; une copie part à la corbeille 30 jours (restaurable par le propriétaire).
    Renvoie le plan effectivement appliqué.
    """
    from apps.cooperatives.models import Cooperative, CooperativeDocument
    from apps.lots.models import Lot
    from apps.parcels.models import LegacyParcel, Parcel
    from apps.reports.models import AnalysisReport
    from .models import ActivityLog, Trash, User

    done = plan(user)
    coops = _coops_wiped(user)
    ids = [c.id for c in coops]
    agent = getattr(user, 'agent_profile', None) if user.role == 'agent' else None
    label = f'{user.full_name or user.username} ({user.get_role_display()})'
    snap = _Snapshot()
    with transaction.atomic():
        if agent is not None:
            snap.delete(Parcel.objects.filter(agent=agent))
        snap.delete(LegacyParcel.objects.filter(uploaded_by=user))
        snap.delete(AnalysisReport.objects.filter(created_by=user))
        if ids:
            # fiches de lot d'abord : leurs lignes protègent les producteurs
            snap.delete(Lot.objects.filter(cooperative_id__in=ids))
            accounts = User.objects.filter(cooperative_id__in=ids).exclude(pk=user.pk)
            snap.delete(ActivityLog.objects.filter(user__in=accounts))
            snap.delete(accounts, files=('avatar',))               # agents et comptes de la coopérative
            for doc in CooperativeDocument.objects.filter(cooperative_id__in=ids):
                if doc.file:
                    snap.files.append(doc.file.name)
            snap.delete(Cooperative.objects.filter(id__in=ids), files=('logo',))   # producteurs, parcelles, registre…
        snap.delete(ActivityLog.objects.filter(user=user))
        snap.delete([user], files=('avatar',))                     # notifications, profil agent, licence
        blob, n = snap.payload()
        Trash.objects.create(label=label, username=user.username, role=user.role, deleted_by=deleted_by,
                             summary=done, payload=blob, object_count=n, files=snap.files)
    purge_expired()
    return done


def purge_expired():
    """Efface définitivement les éléments de la corbeille de plus de 30 jours (et leurs fichiers)."""
    from django.utils import timezone
    from .models import Trash
    for t in Trash.objects.filter(expires_at__lte=timezone.now()):
        erase(t)


def erase(trash):
    from django.core.files.storage import default_storage
    for name in trash.files or []:
        try:
            default_storage.delete(name)
        except Exception:  # noqa: BLE001 — fichier déjà absent
            pass
    trash.delete()


def restore(trash):
    """Remet en base tout le contenu d'un élément de la corbeille (dans l'état au moment de la suppression)."""
    import gzip
    import json
    from django.apps import apps as django_apps
    from django.core import serializers
    from django.db import connection
    data = json.loads(gzip.decompress(bytes(trash.payload)).decode())
    with transaction.atomic():
        with connection.constraint_checks_disabled():
            for obj in serializers.deserialize('json', json.dumps(data['objects'])):
                obj.save()
        for label, pk, attname, old in data.get('relinks', []):
            model = django_apps.get_model(label)
            model.objects.filter(pk=pk).update(**{attname: old})
        connection.check_constraints()
        trash.delete()
