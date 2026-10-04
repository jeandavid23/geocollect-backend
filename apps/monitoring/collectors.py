"""
Indicateurs métier calculés en base à chaque lecture par Grafana (mis en cache 60 s).
Aucune donnée personnelle : uniquement des comptages.
"""
import time
from datetime import timedelta

from django.core.cache import caches
from django.db import connection
from django.db.models import Count, Sum
from django.utils import timezone
from prometheus_client.core import GaugeMetricFamily

CACHE_KEY = 'monitoring:business'
CACHE_TTL = 60


def _compute():
    from apps.accounts.models import ActivityLog, AdminLicense, Notification, User
    from apps.cooperatives.models import Cooperative
    from apps.parcels.models import LegacyParcel, Parcel
    from apps.producers.models import Producer
    from apps.registry.models import RegistrySheet
    from apps.reports.models import AnalysisReport

    now = timezone.now()
    day = now - timedelta(hours=24)
    t0 = time.perf_counter()
    with connection.cursor() as c:
        c.execute('SELECT 1')
    out = {'db_latency': time.perf_counter() - t0}

    out['users'] = list(User.objects.values('role', 'is_active').annotate(n=Count('id')))
    out['coops'] = list(Cooperative.objects.values('is_active').annotate(n=Count('id')))
    admins = []
    today = timezone.localdate()
    for a in User.objects.filter(role='super_admin').select_related('admin_license'):
        lic = getattr(a, 'admin_license', None) if hasattr(a, 'admin_license') else None
        st = 'suspendu' if not a.is_active else 'expire' if lic and lic.expires_at and lic.expires_at < today else 'actif'
        admins.append((st, (lic.organization if lic and lic.organization else a.full_name or a.username)[:60], a.id,
                       bool(lic and lic.expires_at and 0 <= (lic.expires_at - today).days <= 7)))
    out['admins'] = admins
    out['producers'] = Producer.objects.count()
    p = Parcel.objects.aggregate(n=Count('id'), ha=Sum('area_hectares'))
    out['parcels'], out['hectares'] = p['n'] or 0, p['ha'] or 0.0
    out['eudr'] = list(Parcel.objects.values('eudr_status').annotate(n=Count('id')))
    out['parcels_24h'] = Parcel.objects.filter(created_at__gte=day).count()
    out['legacy'] = LegacyParcel.objects.count()
    out['registry'] = RegistrySheet.objects.count()
    out['reports'] = list(AnalysisReport.objects.values('kind').annotate(n=Count('id')))
    out['client_parcels'] = {r['cooperative__managed_by']: (r['n'], r['ha'] or 0) for r in
                             Parcel.objects.values('cooperative__managed_by').annotate(n=Count('id'), ha=Sum('area_hectares'))}
    logs = ActivityLog.objects.filter(timestamp__gte=day)
    out['tools_24h'] = {}
    for module, details in logs.filter(action='run_tool').values_list('resource', 'details'):
        r = out['tools_24h'].setdefault(module, [0, 0])
        r[0] += 1
        r[1] += int(details) if (details or '').isdigit() else 0
    out['logins_24h'] = logs.filter(action='login').count()
    out['active_users_24h'] = logs.filter(user__isnull=False).values('user').distinct().count()
    out['messages_24h'] = logs.filter(action='send_message').count()
    out['gis_edits_24h'] = logs.filter(action='gis_edit').count()
    out['notifications_24h'] = Notification.objects.filter(created_at__gte=day).count()
    out['failed_login_alerts_24h'] = Notification.objects.filter(created_at__gte=day, title__startswith='Tentatives de connexion',
                                                                 recipient__role='owner').count()
    out['licenses'] = AdminLicense.objects.count()
    return out


class BusinessCollector:
    def collect(self):
        shared = caches['shared']
        data = shared.get(CACHE_KEY)
        if data is None:
            data = _compute()
            shared.set(CACHE_KEY, data, CACHE_TTL)

        def g(name, doc, labels=None):
            return GaugeMetricFamily(name, doc, labels=labels or [])

        m = g('geocollect_db_latency_seconds', 'Latence de la base (SELECT 1)'); m.add_metric([], data['db_latency']); yield m
        m = g('geocollect_users', 'Comptes par rôle', ['role', 'active'])
        for r in data['users']:
            m.add_metric([r['role'], 'oui' if r['is_active'] else 'non'], r['n'])
        yield m
        m = g('geocollect_cooperatives', 'Coopératives', ['active'])
        for r in data['coops']:
            m.add_metric(['oui' if r['is_active'] else 'non'], r['n'])
        yield m
        m = g('geocollect_clients', 'Clients (super admins) par statut', ['status'])
        counts = {}
        for st, *_ in data['admins']:
            counts[st] = counts.get(st, 0) + 1
        for st in ('actif', 'suspendu', 'expire'):
            m.add_metric([st], counts.get(st, 0))
        yield m
        m = g('geocollect_clients_expiring_7d', 'Abonnements qui expirent dans 7 jours ou moins'); m.add_metric([], sum(1 for a in data['admins'] if a[3])); yield m
        m = g('geocollect_client_parcels', 'Parcelles mappées par client', ['client'])
        h = g('geocollect_client_hectares', 'Hectares mappés par client', ['client'])
        for st, org, aid, _ in data['admins']:
            n, ha = data['client_parcels'].get(aid, (0, 0))
            m.add_metric([org], n); h.add_metric([org], ha)
        yield m
        yield h
        for name, doc, key in (
            ('geocollect_producers', 'Producteurs enregistrés', 'producers'), ('geocollect_parcels', 'Parcelles mappées', 'parcels'),
            ('geocollect_parcels_hectares', 'Surface mappée (ha)', 'hectares'), ('geocollect_parcels_created_24h', 'Parcelles mappées sur 24 h', 'parcels_24h'),
            ('geocollect_legacy_polygons', 'Anciens polygones importés', 'legacy'), ('geocollect_registry_sheets', 'Feuilles de registre', 'registry'),
            ('geocollect_logins_24h', 'Connexions réussies sur 24 h', 'logins_24h'), ('geocollect_active_users_24h', 'Utilisateurs actifs sur 24 h', 'active_users_24h'),
            ('geocollect_messages_24h', 'Messages envoyés sur 24 h', 'messages_24h'), ('geocollect_gis_edits_24h', 'Enregistrements de modifications SIG sur 24 h', 'gis_edits_24h'),
            ('geocollect_notifications_24h', 'Notifications créées sur 24 h', 'notifications_24h'),
            ('geocollect_failed_login_alerts_24h', 'Alertes « tentatives de connexion échouées » sur 24 h', 'failed_login_alerts_24h'),
        ):
            m = g(name, doc); m.add_metric([], data[key]); yield m
        m = g('geocollect_parcels_by_eudr', 'Parcelles par statut EUDR', ['status'])
        for r in data['eudr']:
            m.add_metric([r['eudr_status'] or 'inconnu'], r['n'])
        yield m
        m = g('geocollect_reports', 'Rapports enregistrés par type', ['kind'])
        for r in data['reports']:
            m.add_metric([r['kind']], r['n'])
        yield m
        runs = g('geocollect_tool_runs_24h', 'Traitements sur 24 h', ['module'])
        items = g('geocollect_tool_items_24h', 'Polygones traités sur 24 h', ['module'])
        for module in ('deforestation', 'validator', 'selfintersection', 'gmr'):
            r = data['tools_24h'].get(module, [0, 0])
            runs.add_metric([module], r[0]); items.add_metric([module], r[1])
        yield runs
        yield items
