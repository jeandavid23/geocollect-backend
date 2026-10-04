import time

from .metrics import HTTP_EXCEPTIONS, HTTP_LATENCY, HTTP_REQUESTS

SKIP = ('/api/v1/monitoring/metrics',)   # couvre aussi l'adresse sans barre finale


def _route(request):
    # motif de l'URL (« api/v1/parcels/<uuid:pk>/ ») et non l'URL réelle : nombre de séries borné
    match = getattr(request, 'resolver_match', None)
    if match is not None and match.route:
        return match.route[:120]
    return 'static' if request.path.startswith(('/static/', '/media/')) else 'non_reconnue'


class PrometheusMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(SKIP):
            return self.get_response(request)
        start = time.perf_counter()
        response = self.get_response(request)
        try:
            route = _route(request)
            HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - start)
            HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        except Exception:  # noqa: BLE001
            pass
        return response

    def process_exception(self, request, exception):
        try:
            HTTP_EXCEPTIONS.labels(_route(request), type(exception).__name__[:60]).inc()
        except Exception:  # noqa: BLE001
            pass
