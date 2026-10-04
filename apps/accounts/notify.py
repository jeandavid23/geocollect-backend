"""
Notifications (cloche de l'application).

Hiérarchie : propriétaire → super admin (client) → coopérative → agent. Chaque événement n'est envoyé
qu'aux personnes qui ont le droit de voir la coopérative concernée (jamais à un autre client).
"""
import logging

from django.core.cache import caches

cache = caches['shared']          # partagé entre les processus gunicorn

log = logging.getLogger(__name__)

LOGIN_NOTICE_COOLDOWN = 30 * 60          # une alerte de connexion par compte toutes les 30 min au plus
FAILED_LOGIN_LIMIT = 5                   # échecs avant alerte
FAILED_LOGIN_WINDOW = 15 * 60


def _create(user_ids, *, title, message='', ntype='info', cooperative=None):
    from .models import Notification

    objs = [Notification(recipient_id=uid, cooperative=cooperative, type=ntype, title=title[:200], message=message)
            for uid in {u for u in user_ids if u}]
    if objs:
        Notification.objects.bulk_create(objs)
    return len(objs)


def notify_users(users, *, title, message='', ntype='info', cooperative=None):
    """Notifie une liste d'utilisateurs (objets ou identifiants)."""
    try:
        return _create([getattr(u, 'id', u) for u in users], title=title, message=message, ntype=ntype,
                       cooperative=cooperative)
    except Exception:  # noqa: BLE001 — une notification ne doit jamais faire échouer l'action
        log.exception('Notification')
        return 0


def owners():
    from .models import User
    return list(User.objects.filter(role='owner', is_active=True).values_list('id', flat=True))


def coop_accounts(cooperative):
    """Comptes « coopérative » (gestionnaires) d'une coopérative."""
    from .models import User
    if not cooperative:
        return []
    return list(User.objects.filter(cooperative=cooperative, role='cooperative', is_active=True).values_list('id', flat=True))


def coop_managers(cooperative, *, with_owner=True):
    """Comptes coopérative + super admin gestionnaire (+ propriétaire)."""
    ids = coop_accounts(cooperative)
    if cooperative and cooperative.managed_by_id:
        ids.append(cooperative.managed_by_id)
    if with_owner:
        ids += owners()
    return ids


def notify_cooperative(cooperative, *, title, message='', ntype='info', exclude_user=None):
    """Notifie tous les membres de la coopérative, son super admin gestionnaire et le propriétaire."""
    from .models import User

    recipients = set()
    if cooperative:
        recipients.update(User.objects.filter(cooperative=cooperative, is_active=True).values_list('id', flat=True))
        if cooperative.managed_by_id:
            recipients.add(cooperative.managed_by_id)
    recipients.update(owners())
    if exclude_user is not None:
        recipients.discard(getattr(exclude_user, 'id', exclude_user))
    notify_users(recipients, title=title, message=message, ntype=ntype, cooperative=cooperative)


# ─── Connexions ────────────────────────────────────────────────────────────────

def _device(request):
    ua = (request.META.get('HTTP_USER_AGENT') or '').lower()
    browser = next((n for k, n in (('edg', 'Edge'), ('opr', 'Opera'), ('chrome', 'Chrome'), ('firefox', 'Firefox'),
                                   ('safari', 'Safari'), ('dart', 'application mobile'), ('okhttp', 'application mobile'))
                    if k in ua), 'navigateur')
    system = next((n for k, n in (('android', 'Android'), ('iphone', 'iPhone'), ('ipad', 'iPad'), ('windows', 'Windows'),
                                  ('mac os', 'Mac'), ('linux', 'Linux')) if k in ua), '')
    return f'{browser}{" · " + system if system else ""}'


def client_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR')
    return (fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR')) or ''


def notify_login(user, request, previous=None):
    """
    Connexion réussie :
    - au titulaire du compte, si l'appareil ou l'adresse IP change (alerte de sécurité) ;
    - à son responsable : agent → coopérative, coopérative → super admin, super admin → propriétaire ;
    - au super admin, si son abonnement expire dans 7 jours ou moins (une fois par jour).
    `previous` : (ip, appareil) de la connexion précédente.
    """
    try:
        ip, device = client_ip(request), _device(request)
        name = user.full_name or user.username
        if previous and (previous[0] != ip or previous[1] != device):
            notify_users([user], ntype='warning', title='Nouvelle connexion à votre compte',
                         message=f'{device} · IP {ip}. Si ce n\'est pas vous, changez votre mot de passe (Mon compte).')

        if cache.add(f'login-notice:{user.pk}', 1, LOGIN_NOTICE_COOLDOWN):
            coop = user.cooperative if user.role in ('agent', 'cooperative') else None
            if user.role == 'agent':
                notify_users(coop_accounts(coop), title=f'Agent connecté — {name}', message=device, cooperative=coop)
            elif user.role == 'cooperative' and coop and coop.managed_by_id:
                notify_users([coop.managed_by_id], title=f'Coopérative connectée — {coop.name}',
                             message=f'{name} · {device}', cooperative=coop)
            elif user.role == 'super_admin':
                org = getattr(getattr(user, 'admin_license', None), 'organization', '') or name
                notify_users(owners(), title=f'Client connecté — {org}', message=f'{user.username} · {device}')

        if user.role == 'super_admin':
            lic = getattr(user, 'admin_license', None)
            if lic and lic.expires_at:
                from django.utils import timezone
                left = (lic.expires_at - timezone.localdate()).days
                if 0 <= left <= 7 and cache.add(f'expiry-notice:{user.pk}:{timezone.localdate()}', 1, 86400):
                    notify_users([user], ntype='warning', title='Votre abonnement arrive à échéance',
                                 message=f'Fin le {lic.expires_at:%d/%m/%Y} ({left} jour(s)). Contactez GeoLab Service pour le renouveler.')
    except Exception:  # noqa: BLE001
        log.exception('Notification de connexion')


def notify_failed_login(username, request):
    """Après 5 échecs en 15 min sur un identifiant existant : alerte au titulaire et au propriétaire."""
    from .models import User
    try:
        key = f'failed-login:{(username or "").lower()}'
        cache.add(key, 0, FAILED_LOGIN_WINDOW)
        n = cache.incr(key)
        if n != FAILED_LOGIN_LIMIT:
            return
        user = User.objects.filter(username__iexact=username).first()
        if user is None:
            return
        msg = f'{n} mots de passe erronés en moins de 15 min (IP {client_ip(request)}).'
        notify_users([user], ntype='error', title='Tentatives de connexion échouées',
                     message=msg + ' Si ce n\'est pas vous, changez votre mot de passe.')
        notify_users(owners(), ntype='error', title=f'Tentatives de connexion échouées — {user.username}', message=msg)
    except Exception:  # noqa: BLE001
        log.exception('Alerte de connexion échouée')


# ─── Traitements ───────────────────────────────────────────────────────────────

def notify_tool(request, title, message, ntype='success'):
    """Fin d'un traitement : l'auteur, les comptes de sa coopérative et son super admin gestionnaire."""
    user = request.user
    coop = getattr(user, 'cooperative', None) if user.role in ('agent', 'cooperative') else None
    ids = [user.id]
    if coop:
        ids += coop_accounts(coop)
        if coop.managed_by_id:
            ids.append(coop.managed_by_id)
    notify_users(ids, title=title, message=message, ntype=ntype, cooperative=coop)
