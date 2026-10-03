"""
Multi-clients (« tenants ») : chaque super admin est un client de la plateforme.

  owner (Super Super Admin) ── voit et gère tout, crée les super admins et leur accorde des droits
     └── super_admin (client) ── ne voit que ses coopératives (Cooperative.managed_by)
            └── cooperative ── ne voit que ses données
                   └── agent ── ne voit que les données de sa coopérative

L'état d'un client (actif, abonnement expiré, modules) est mis en cache 60 s par processus
pour éviter des allers-retours vers la base à chaque requête (la base Neon est distante).
"""
from django.core.cache import caches

CACHE_TTL = 60
ALL_MODULES = None  # None = tous les modules (propriétaire, ou super admin sans licence)


def _cache():
    return caches['tenancy']


def tenant_admin(user):
    """Super admin (client) responsable de l'utilisateur ; None pour le propriétaire ou une coopérative sans gestionnaire."""
    role = getattr(user, 'role', None)
    if role == 'super_admin':
        return user
    if role in ('cooperative', 'agent'):
        coop = getattr(user, 'cooperative', None)
        return getattr(coop, 'managed_by', None) if coop else None
    return None


def _license(admin):
    if admin is None:
        return None
    try:
        return admin.admin_license
    except Exception:  # noqa: BLE001 — RelatedObjectDoesNotExist : super admin sans licence = tous les droits
        return None


def _compute_status(user):
    role = getattr(user, 'role', None)
    if role == 'owner':
        return {'active': True, 'reason': '', 'modules': ALL_MODULES}
    if role in ('cooperative', 'agent'):
        coop = getattr(user, 'cooperative', None)
        if coop is not None and not coop.is_active:
            return {'active': False, 'reason': 'Cette coopérative a été désactivée. Contactez votre administrateur.', 'modules': []}
    admin = tenant_admin(user)
    if admin is not None and admin is not user and not admin.is_active:
        return {'active': False, 'reason': "L'accès de votre organisation est suspendu. Contactez votre administrateur.", 'modules': []}
    lic = _license(admin)
    if lic is not None and lic.is_expired:
        msg = (f"L'abonnement de votre organisation a expiré le {lic.expires_at.strftime('%d/%m/%Y')}. "
               'Contactez GeoLab Service pour le renouveler.')
        return {'active': False, 'reason': msg, 'modules': []}
    return {'active': True, 'reason': '', 'modules': list(lic.modules) if lic is not None else ALL_MODULES}


def tenant_status(user):
    """{'active': bool, 'reason': str, 'modules': list | None (None = tous)}."""
    key = f'tenant:{user.pk}'
    status = _cache().get(key)
    if status is None:
        status = _compute_status(user)
        _cache().set(key, status, CACHE_TTL)
    return status


def has_module(user, module):
    modules = tenant_status(user)['modules']
    return modules is None or module in modules


def invalidate_tenant_cache():
    """À appeler après une modification de licence, de suspension ou de rattachement (processus courant ;
    les autres processus se mettent à jour en 60 s au plus)."""
    _cache().clear()
