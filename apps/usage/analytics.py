"""
Statistiques d'utilisation des comptes (audience de l'application) : utilisateurs actifs, sessions, pages vues,
connexions, actions, appareils, heures — globales, par compte, par coopérative et par client (super admin).

Sources : pages vues de l'application web (PageView) et journal d'activité (connexions et actions, application
mobile comprise). Nombre de requêtes fixe, quel que soit le nombre de comptes.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.db.models import Count, Max, Min
from django.db.models.functions import ExtractHour, TruncDate
from django.utils import timezone

from apps.accounts.models import ActivityLog
from .models import PageView

ACTIVE_DAYS = 7                 # « actif » : vu dans les 7 derniers jours
DORMANT_DAYS = 30               # au-delà : « inactif »
MAX_ACCOUNTS = 500
TOP_PAGES = 12
NOT_ACTIONS = ('login', 'impersonate')


def _day(dt):
    return timezone.localtime(dt).date() if dt else None


def _status(last_seen, today):
    if last_seen is None:
        return 'jamais'
    age = (today - last_seen).days
    return 'actif' if age <= ACTIVE_DAYS else 'peu_actif' if age <= DORMANT_DAYS else 'inactif'


def _per_user(views, logins, actions):
    """Chiffres par compte sur la période, en quatre requêtes groupées."""
    stats = defaultdict(lambda: {'page_views': 0, 'sessions': 0, 'logins': 0, 'actions': 0, 'days': set(), 'last': None})
    for r in views.values('user_id').annotate(n=Count('id'), s=Count('session', distinct=True), last=Max('at')):
        stats[r['user_id']].update(page_views=r['n'], sessions=r['s'], last=r['last'])
    for r in views.annotate(d=TruncDate('at')).values('user_id', 'd').distinct():
        stats[r['user_id']]['days'].add(r['d'])
    for qs, key in ((logins, 'logins'), (actions, 'actions')):
        for r in qs.values('user_id').annotate(n=Count('id'), last=Max('timestamp')):
            s = stats[r['user_id']]
            s[key] = r['n']
            s['last'] = max(filter(None, (s['last'], r['last'])), default=None)
    for r in (logins | actions).annotate(d=TruncDate('timestamp')).values('user_id', 'd').distinct():
        stats[r['user_id']]['days'].add(r['d'])
    return stats


def _daily(views, logs, logins, start, today):
    """Par jour : comptes actifs (page vue, connexion ou action, application mobile comprise), pages vues, connexions."""
    users = defaultdict(set)
    for r in views.annotate(d=TruncDate('at')).values('d', 'user_id').distinct():
        users[r['d']].add(r['user_id'])
    for r in logs.annotate(d=TruncDate('timestamp')).values('d', 'user_id').distinct():
        users[r['d']].add(r['user_id'])
    page_views = {r['d']: r['n'] for r in views.annotate(d=TruncDate('at')).values('d').annotate(n=Count('id'))}
    login_day = {r['d']: r['n'] for r in logins.annotate(d=TruncDate('timestamp')).values('d').annotate(n=Count('id'))}
    out, d = [], start
    while d <= today:
        out.append({'day': d.isoformat(), 'active_users': len(users.get(d, ())), 'page_views': page_views.get(d, 0),
                    'logins': login_day.get(d, 0)})
        d += timedelta(days=1)
    return out


def _sessions(views):
    """Nombre de sessions et durée moyenne (de la première à la dernière page de la session)."""
    rows = list(views.values('session').annotate(first=Min('at'), last=Max('at'), n=Count('id')))
    if not rows:
        return 0, None, None
    seconds = [(r['last'] - r['first']).total_seconds() for r in rows]
    return len(rows), round(sum(seconds) / len(rows) / 60, 1), round(sum(r['n'] for r in rows) / len(rows), 1)


def _organisation(u):
    if u.cooperative_id:
        return u.cooperative.name
    if u.plant_id:
        return u.plant.name
    return ''


def _admin_of(u):
    """Client (super admin) dont dépend le compte."""
    if u.role == 'super_admin':
        return u.id, u.full_name or u.username
    owner = (u.cooperative.managed_by if u.cooperative_id else u.plant.managed_by if u.plant_id else None)
    return (owner.id, owner.full_name or owner.username) if owner else (None, 'Propriétaire')


def overview(accounts, days, today=None):
    today = today or timezone.localdate()
    start = today - timedelta(days=days - 1)
    since = timezone.make_aware(datetime.combine(start, time.min))
    accounts = list(accounts.select_related('cooperative__managed_by', 'plant__managed_by')[:MAX_ACCOUNTS])
    ids = [u.id for u in accounts]
    views = PageView.objects.filter(user_id__in=ids, at__gte=since)
    logs = ActivityLog.objects.filter(user_id__in=ids, timestamp__gte=since)
    logins, actions = logs.filter(action='login'), logs.exclude(action__in=NOT_ACTIONS)
    stats = _per_user(views, logins, actions)
    sessions, avg_minutes, pages_per_session = _sessions(views)

    rows = []
    for u in accounts:
        s = stats.get(u.id) or {'page_views': 0, 'sessions': 0, 'logins': 0, 'actions': 0, 'days': set(), 'last': None}
        last = max(filter(None, (s['last'], u.last_login)), default=None)
        admin_id, admin_name = _admin_of(u)
        rows.append({'id': str(u.id), 'name': u.full_name or u.username, 'username': u.username, 'role': u.role,
                     'is_active': u.is_active, 'organisation': _organisation(u),
                     'cooperative_id': str(u.cooperative_id) if u.cooperative_id else None,
                     'admin_id': str(admin_id) if admin_id else None, 'admin_name': admin_name,
                     'last_seen': last, 'status': _status(_day(last), today), 'days_active': len(s['days']),
                     'page_views': s['page_views'], 'sessions': s['sessions'], 'logins': s['logins'], 'actions': s['actions']})
    rows.sort(key=lambda r: (r['last_seen'] is None, -(r['last_seen'].timestamp() if r['last_seen'] else 0)))

    def active_within(n):
        limit = today - timedelta(days=n - 1)
        return sum(1 for r in rows if r['last_seen'] and _day(r['last_seen']) >= limit)

    return {
        'days': days, 'start': start, 'end': today,
        'totals': {'accounts': len(rows), 'active_today': active_within(1), 'active_7d': active_within(ACTIVE_DAYS),
                   'active_period': sum(1 for r in rows if r['days_active']), 'page_views': sum(r['page_views'] for r in rows),
                   'sessions': sessions, 'avg_session_minutes': avg_minutes, 'pages_per_session': pages_per_session,
                   'logins': sum(r['logins'] for r in rows), 'actions': sum(r['actions'] for r in rows),
                   'dormant': sum(1 for r in rows if r['status'] in ('inactif', 'jamais'))},
        'daily': _daily(views, logs.exclude(action='impersonate'), logins, start, today),
        'top_pages': [{'path': r['path'], 'views': r['n'], 'users': r['u']} for r in views.values('path')
                      .annotate(n=Count('id'), u=Count('user', distinct=True)).order_by('-n')[:TOP_PAGES]],
        'devices': [{'device': r['device'], 'sessions': r['s']} for r in views.values('device')
                    .annotate(s=Count('session', distinct=True)).order_by('-s')],
        'hours': _hours(views),
        'accounts': rows,
        'by_cooperative': _groups(rows, lambda r: (r['cooperative_id'], r['organisation']) if r['cooperative_id'] else None),
        'by_admin': _groups(rows, lambda r: (r['admin_id'], r['admin_name'])),
    }


def _hours(views):
    counts = {r['h']: r['n'] for r in views.annotate(h=ExtractHour('at')).values('h').annotate(n=Count('id'))}
    return [{'hour': h, 'page_views': counts.get(h, 0)} for h in range(24)]


def _groups(rows, key):
    out = {}
    for r in rows:
        k = key(r)
        if k is None:
            continue
        g = out.setdefault(k, {'id': k[0], 'name': k[1], 'accounts': 0, 'active': 0, 'page_views': 0, 'actions': 0,
                               'logins': 0, 'last_seen': None})
        g['accounts'] += 1
        g['active'] += r['status'] == 'actif'
        for f in ('page_views', 'actions', 'logins'):
            g[f] += r[f]
        g['last_seen'] = max(filter(None, (g['last_seen'], r['last_seen'])), default=None)
    return sorted(out.values(), key=lambda g: (-g['active'], -g['page_views']))
