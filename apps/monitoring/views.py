"""
/api/v1/monitoring/
  metrics/        métriques Prometheus (Grafana) — jeton MONITORING_TOKEN obligatoire (Bearer ou Basic), sinon 401
  health/         état du service (base de données) pour les sondes de disponibilité — public, sans donnée sensible
  client-error/   erreurs JavaScript de l'application web (comptées, rien n'est stocké)
"""
import base64
import hmac
import logging
import os
import time

from django.conf import settings
from django.db import connection
from django.http import HttpResponse, HttpResponseNotFound, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry, generate_latest

from .collectors import BusinessCollector
from .metrics import FRONTEND_ERRORS, safe_inc

log = logging.getLogger(__name__)
STARTED = time.time()
_business = CollectorRegistry(auto_describe=False)
_business.register(BusinessCollector())


def _candidates(auth):
    """Valeurs où le jeton peut se trouver selon l'outil (Bearer, Basic utilisateur ou mot de passe, jeton brut)."""
    auth = (auth or '').strip()
    scheme, _, rest = auth.partition(' ')
    rest = rest.strip()
    if scheme.lower() in ('bearer', 'token'):
        return 'bearer', [rest]
    if scheme.lower() == 'basic':
        try:
            user, _, pwd = base64.b64decode(rest).decode('utf-8', 'replace').partition(':')
        except Exception:  # noqa: BLE001
            return 'basic-illisible', []
        return 'basic', [pwd.strip(), user.strip()]
    return ('brut' if auth else 'absent'), [auth]


def _authorized(request):
    token = (getattr(settings, 'MONITORING_TOKEN', '') or '').strip()
    if not token:
        return False
    scheme, values = _candidates(request.META.get('HTTP_AUTHORIZATION', ''))
    ok = any(v and hmac.compare_digest(v, token) for v in values)
    if not ok:
        # diagnostic sans secret : type d'authentification et longueurs seulement
        try:
            from django.core.cache import caches
            caches['shared'].set('monitoring:last_denied', {
                'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'scheme': scheme, 'lengths': [len(v) for v in values],
                'expected_length': len(token), 'user_agent': request.META.get('HTTP_USER_AGENT', '')[:80],
            }, 86400)
        except Exception:  # noqa: BLE001
            pass
    return ok


def _trace(request, status, size, fmt):
    """Dernières visites du point de mesures (diagnostic de Grafana Cloud) — aucun secret enregistré."""
    try:
        from django.core.cache import caches
        c = caches['shared']
        hist = (c.get('monitoring:visits') or [])[-9:]
        hist.append({'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'method': request.method, 'status': status, 'bytes': size, 'format': fmt,
                     'ua': request.META.get('HTTP_USER_AGENT', '')[:60], 'accept': request.META.get('HTTP_ACCEPT', '')[:120],
                     'encoding': request.META.get('HTTP_ACCEPT_ENCODING', '')[:40], 'auth_header': bool(request.META.get('HTTP_AUTHORIZATION')),
                     'token_in_url': 'token' in request.GET})
        c.set('monitoring:visits', hist, 86400 * 3)
    except Exception:  # noqa: BLE001
        pass


@require_http_methods(['GET', 'HEAD'])
def metrics(request):
    if not _authorized(request):
        # 401 + WWW-Authenticate : Grafana Cloud vérifie d'abord que l'adresse refuse une visite sans identifiants,
        # puis revient avec le jeton (une réponse 404, ou 200 sans jeton, fait échouer son test)
        _trace(request, 401, 0, '')
        resp = HttpResponse('Authentification requise.\n', status=401, content_type='text/plain; charset=utf-8')
        resp['WWW-Authenticate'] = 'Bearer realm="geocollect-metrics", Basic realm="geocollect-metrics"'
        return resp
    # Format demandé : OpenMetrics (Grafana Cloud, Prometheus récents) ou texte Prometheus classique
    openmetrics = 'application/openmetrics-text' in request.META.get('HTTP_ACCEPT', '')
    if openmetrics:
        from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST as ctype, generate_latest as gen
    else:
        gen, ctype = generate_latest, CONTENT_TYPE_LATEST
    if os.environ.get('PROMETHEUS_MULTIPROC_DIR'):
        from prometheus_client import multiprocess
        reg = CollectorRegistry()
        multiprocess.MultiProcessCollector(reg)
    else:
        reg = REGISTRY
    body = gen(reg)
    try:
        extra = gen(_business)
        if openmetrics:   # un seul « # EOF », à la toute fin
            body = body.replace(b'# EOF\n', b'')
        body += extra
    except Exception:  # noqa: BLE001
        log.exception('Indicateurs métier')
    _trace(request, 200, len(body), 'openmetrics' if openmetrics else 'prometheus')
    return HttpResponse(body, content_type=ctype)


@require_GET
def health(request):
    t0 = time.perf_counter()
    try:
        with connection.cursor() as c:
            c.execute('SELECT 1')
        db, code = 'ok', 200
    except Exception:  # noqa: BLE001
        db, code = 'erreur', 503
    return JsonResponse({
        'status': 'ok' if code == 200 else 'degrade', 'database': db, 'db_ms': round((time.perf_counter() - t0) * 1000, 1),
        'version': (os.environ.get('RENDER_GIT_COMMIT') or 'local')[:12], 'uptime_s': int(time.time() - STARTED),
    }, status=code)


@csrf_exempt
@require_POST
def client_error(request):
    kind = (request.GET.get('kind') or 'error')[:20]
    safe_inc(FRONTEND_ERRORS, kind=kind if kind in ('error', 'unhandledrejection', 'chunk') else 'error')
    try:
        log.warning('Erreur navigateur : %s', request.body[:500].decode('utf-8', 'replace'))
    except Exception:  # noqa: BLE001
        pass
    return HttpResponse(status=204)
