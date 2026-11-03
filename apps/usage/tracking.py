"""Enregistrement des pages vues : adresses normalisées, lots bornés, purge des anciennes lignes."""
import re
from datetime import timedelta

from django.core.cache import caches
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import PageView

RETENTION_DAYS = 180
MAX_EVENTS = 50                     # par envoi
MAX_AGE = timedelta(days=2)         # une page vue plus ancienne (horloge du téléphone fausse) est ignorée
PURGE_KEY = 'usage:purge'
ID_SEGMENT = re.compile(r'^([0-9a-f]{8}-[0-9a-f-]{27}|\d+|[A-Z]{2,}[-\w]*\d[-\w]*)$', re.I)
SESSION = re.compile(r'^[\w-]{8,32}$')


def normalize(path):
    """'/coop/lots/3f0c…?x=1' → '/coop/lots/:id' (jamais d'identifiant ni de paramètre)."""
    clean = (path or '/').split('?')[0].split('#')[0][:200]
    parts = [':id' if ID_SEGMENT.match(p) else p.lower() for p in clean.split('/') if p]
    return ('/' + '/'.join(parts))[:120]


def device(user_agent):
    ua = (user_agent or '').lower()
    if 'ipad' in ua or 'tablet' in ua or ('android' in ua and 'mobile' not in ua):
        return 'tablette'
    if 'mobi' in ua or 'iphone' in ua:
        return 'mobile'
    return 'ordinateur'


def record(user, events, user_agent):
    """Enregistre les pages vues valides ; renvoie leur nombre."""
    now = timezone.now()
    kind = device(user_agent)
    rows = []
    for e in (events or [])[:MAX_EVENTS]:
        if not isinstance(e, dict) or not SESSION.match(str(e.get('session') or '')):
            continue
        at = parse_datetime(str(e.get('at') or '')) or now
        if timezone.is_naive(at):
            at = timezone.make_aware(at)
        if not now - MAX_AGE <= at <= now + timedelta(minutes=5):
            continue
        rows.append(PageView(user=user, session=e['session'], path=normalize(e.get('path')), device=kind, at=min(at, now)))
    PageView.objects.bulk_create(rows)
    purge()
    return len(rows)


def purge():
    """Une fois par jour au plus : efface les pages vues au-delà de la durée de conservation."""
    if caches['shared'].add(PURGE_KEY, 1, 24 * 3600):
        PageView.objects.filter(at__lt=timezone.now() - timedelta(days=RETENTION_DAYS)).delete()
