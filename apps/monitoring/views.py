"""
/api/v1/monitoring/
  metrics/        métriques Prometheus (Grafana) — jeton MONITORING_TOKEN obligatoire (Bearer ou Basic), sinon 404
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
from django.views.decorators.http import require_GET, require_POST
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry, generate_latest

from .collectors import BusinessCollector
from .metrics import FRONTEND_ERRORS, safe_inc

log = logging.getLogger(__name__)
STARTED = time.time()
_business = CollectorRegistry(auto_describe=False)
_business.register(BusinessCollector())


def _authorized(request):
    token = (getattr(settings, 'MONITORING_TOKEN', '') or '').strip()
    if not token:
        return False
    auth = request.META.get('HTTP_AUTHORIZATION', '')
    if auth.startswith('Bearer '):
        return hmac.compare_digest(auth[7:].strip(), token)
    if auth.startswith('Basic '):
        try:
            _, _, pwd = base64.b64decode(auth[6:]).decode().partition(':')
        except Exception:  # noqa: BLE001
            return False
        return hmac.compare_digest(pwd.strip(), token)
    return False


@require_GET
def metrics(request):
    if not _authorized(request):
        return HttpResponseNotFound()          # rien n'indique qu'un point de supervision existe
    if os.environ.get('PROMETHEUS_MULTIPROC_DIR'):
        from prometheus_client import multiprocess
        reg = CollectorRegistry()
        multiprocess.MultiProcessCollector(reg)
        body = generate_latest(reg)
    else:
        body = generate_latest(REGISTRY)
    try:
        body += generate_latest(_business)
    except Exception:  # noqa: BLE001
        log.exception('Indicateurs métier')
    return HttpResponse(body, content_type=CONTENT_TYPE_LATEST)


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
