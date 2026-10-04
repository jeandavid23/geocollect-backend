"""
Métriques Prometheus de GeoCollect (lues par Grafana).

Gunicorn lance plusieurs processus : en production, les compteurs sont partagés via le mode « multiprocess »
de prometheus_client (dossier PROMETHEUS_MULTIPROC_DIR, préparé dans gunicorn.conf.py).
"""
from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter('geocollect_http_requests_total', 'Requêtes HTTP traitées', ['method', 'route', 'status'])
HTTP_LATENCY = Histogram('geocollect_http_request_duration_seconds', 'Durée de traitement des requêtes HTTP', ['method', 'route'],
                         buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120))
HTTP_EXCEPTIONS = Counter('geocollect_http_exceptions_total', 'Exceptions non gérées (erreurs 500)', ['route', 'exception'])
LOGINS = Counter('geocollect_logins_total', 'Tentatives de connexion', ['result'])
TOOL_RUNS = Counter('geocollect_tool_runs_total', 'Traitements lancés', ['module'])
TOOL_ITEMS = Counter('geocollect_tool_items_total', 'Polygones traités par les outils', ['module'])
FRONTEND_ERRORS = Counter('geocollect_frontend_errors_total', "Erreurs JavaScript remontées par l'application web", ['kind'])


def safe_inc(counter, amount=1, **labels):
    try:
        (counter.labels(**labels) if labels else counter).inc(amount)
    except Exception:  # noqa: BLE001 — la supervision ne doit jamais casser une requête
        pass
