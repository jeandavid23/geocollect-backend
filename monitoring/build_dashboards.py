"""
Génère les tableaux de bord Grafana de GeoCollect (JSON importables dans Grafana Cloud ou Grafana local).
    python monitoring/build_dashboards.py
La source de données est une variable (« datasource ») : Grafana prend la source Prometheus par défaut, modifiable en haut du tableau.
"""
import json
from pathlib import Path

OUT = Path(__file__).parent / 'grafana' / 'dashboards'
DS = {'type': 'prometheus', 'uid': '${datasource}'}
TOOLS_RE = 'deforestation|validator|selfintersection|legacy/import|producers/bulk|registry/import|reports|gis/save'
_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def target(expr, legend='', instant=False, fmt=None):
    t = {'datasource': DS, 'expr': expr, 'legendFormat': legend}
    if instant:
        t['instant'] = True; t['range'] = False
    if fmt:
        t['format'] = fmt
    return t


def refs(targets):
    for i, t in enumerate(targets):
        t['refId'] = chr(65 + i)
    return targets


def stat(title, expr, x, y, w=4, h=4, unit='short', thresholds=None, decimals=None, desc='', color_mode='value'):
    steps = thresholds or [{'color': 'green', 'value': None}]
    p = {'id': nid(), 'type': 'stat', 'title': title, 'description': desc, 'datasource': DS, 'gridPos': {'x': x, 'y': y, 'w': w, 'h': h},
         'targets': refs([target(expr, instant=True)]),
         'fieldConfig': {'defaults': {'unit': unit, 'noValue': '0', 'thresholds': {'mode': 'absolute', 'steps': steps}, **({'decimals': decimals} if decimals is not None else {})}, 'overrides': []},
         'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False}, 'colorMode': color_mode, 'graphMode': 'area', 'textMode': 'auto', 'justifyMode': 'auto', 'orientation': 'auto'}}
    return p


def ts(title, targets, x, y, w=12, h=8, unit='short', stack=False, desc='', bars=False):
    return {'id': nid(), 'type': 'timeseries', 'title': title, 'description': desc, 'datasource': DS, 'gridPos': {'x': x, 'y': y, 'w': w, 'h': h},
            'targets': refs(targets),
            'fieldConfig': {'defaults': {'unit': unit, 'noValue': 'Rien sur la période', 'custom': {'drawStyle': 'bars' if bars else 'line', 'lineWidth': 2, 'fillOpacity': 15,
                                                                   'stacking': {'mode': 'normal' if stack else 'none', 'group': 'A'}, 'showPoints': 'never'}}, 'overrides': []},
            'options': {'legend': {'displayMode': 'list', 'placement': 'bottom', 'showLegend': True}, 'tooltip': {'mode': 'multi', 'sort': 'desc'}}}


def bargauge(title, expr, legend, x, y, w=12, h=8, unit='short', desc=''):
    return {'id': nid(), 'type': 'bargauge', 'title': title, 'description': desc, 'datasource': DS, 'gridPos': {'x': x, 'y': y, 'w': w, 'h': h},
            'targets': refs([target(expr, legend, instant=True)]),
            'fieldConfig': {'defaults': {'unit': unit, 'noValue': 'Rien sur la période', 'color': {'mode': 'continuous-GrYlRd'}, 'min': 0}, 'overrides': []},
            'options': {'displayMode': 'gradient', 'orientation': 'horizontal', 'showUnfilled': True, 'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False}}}


def pie(title, expr, legend, x, y, w=6, h=8, overrides=None):
    return {'id': nid(), 'type': 'piechart', 'title': title, 'datasource': DS, 'gridPos': {'x': x, 'y': y, 'w': w, 'h': h},
            'targets': refs([target(expr, legend, instant=True)]),
            'fieldConfig': {'defaults': {'unit': 'short'}, 'overrides': overrides or []},
            'options': {'pieType': 'donut', 'legend': {'displayMode': 'table', 'placement': 'right', 'values': ['value', 'percent']}, 'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False}}}


def row(title, y):
    return {'id': nid(), 'type': 'row', 'title': title, 'collapsed': False, 'gridPos': {'x': 0, 'y': y, 'w': 24, 'h': 1}, 'panels': []}


def dashboard(uid, title, desc, panels, refresh='1m', time_from='now-24h'):
    return {
        'uid': uid, 'title': title, 'description': desc, 'tags': ['geocollect', 'eudr'], 'timezone': 'browser', 'schemaVersion': 39,
        'refresh': refresh, 'time': {'from': time_from, 'to': 'now'}, 'editable': True, 'graphTooltip': 1,
        'templating': {'list': [{'name': 'datasource', 'label': 'Source', 'type': 'datasource', 'query': 'prometheus', 'current': {}, 'hide': 0}]},
        'links': [{'title': 'Site GeoCollect', 'url': 'https://sunny-pegasus-8ea076.netlify.app', 'type': 'link', 'targetBlank': True}],
        'panels': panels,
    }


RED = [{'color': 'green', 'value': None}, {'color': 'orange', 'value': 1}, {'color': 'red', 'value': 5}]

# ─── 1. Santé technique ──────────────────────────────────────────────────────
tech = [
    row('Disponibilité', 0),
    stat('API en ligne', 'max(up{job="geocollect-api"})', 0, 1, unit='bool_on_off',
         thresholds=[{'color': 'red', 'value': None}, {'color': 'green', 'value': 1}], color_mode='background',
         desc='1 = le serveur Render répond à la collecte des mesures.'),
    stat('Requêtes / min', 'sum(rate(geocollect_http_requests_total[5m])) * 60', 4, 1, unit='short', decimals=1),
    stat('Erreurs 5xx', '100 * (sum(rate(geocollect_http_requests_total{status=~"5.."}[15m])) or vector(0)) / clamp_min(sum(rate(geocollect_http_requests_total[15m])), 1e-9)',
         8, 1, unit='percent', thresholds=RED, decimals=2, desc='Part des réponses en erreur serveur sur 15 min.'),
    stat('Latence p95 (pages et API)', f'histogram_quantile(0.95, sum by (le) (rate(geocollect_http_request_duration_seconds_bucket{{route!~"{TOOLS_RE}"}}[15m])))',
         12, 1, unit='s', decimals=2, thresholds=[{'color': 'green', 'value': None}, {'color': 'orange', 'value': 1}, {'color': 'red', 'value': 3}],
         desc='95 % des requêtes (hors traitements lourds) répondent en moins de ce temps.'),
    stat('Latence base Neon', 'max(geocollect_db_latency_seconds)', 16, 1, unit='s', decimals=3,
         thresholds=[{'color': 'green', 'value': None}, {'color': 'orange', 'value': 0.2}, {'color': 'red', 'value': 0.5}]),
    stat('Erreurs JavaScript (24 h)', 'sum(increase(geocollect_frontend_errors_total[24h]))', 20, 1, decimals=0,
         thresholds=[{'color': 'green', 'value': None}, {'color': 'orange', 'value': 10}, {'color': 'red', 'value': 100}]),
    row('Trafic', 5),
    ts('Requêtes par code de réponse', [target('sum by (status) (rate(geocollect_http_requests_total[5m])) * 60', '{{status}}')], 0, 6, unit='short', stack=True,
       desc='Requêtes par minute.'),
    ts('Latence (p50 / p95 / p99)', [
        target('histogram_quantile(0.5, sum by (le) (rate(geocollect_http_request_duration_seconds_bucket[5m])))', 'p50'),
        target('histogram_quantile(0.95, sum by (le) (rate(geocollect_http_request_duration_seconds_bucket[5m])))', 'p95'),
        target('histogram_quantile(0.99, sum by (le) (rate(geocollect_http_request_duration_seconds_bucket[5m])))', 'p99')], 12, 6, unit='s'),
    bargauge('Routes les plus lentes (p95, 1 h)', 'topk(10, histogram_quantile(0.95, sum by (le, route) (rate(geocollect_http_request_duration_seconds_bucket[1h]))))',
             '{{route}}', 0, 14, unit='s'),
    bargauge('Routes les plus appelées (1 h)', 'topk(10, sum by (route) (increase(geocollect_http_requests_total[1h])))', '{{route}}', 12, 14),
    row('Erreurs et sécurité', 22),
    ts('Erreurs 5xx et exceptions par route', [
        target('sum by (route) (increase(geocollect_http_requests_total{status=~"5.."}[5m]))', '5xx {{route}}'),
        target('sum by (route, exception) (increase(geocollect_http_exceptions_total[5m]))', '{{exception}} · {{route}}')], 0, 23, bars=True),
    ts('Connexions (réussies / échouées)', [target('sum by (result) (increase(geocollect_logins_total[5m]))', '{{result}}')], 12, 23, bars=True,
       desc='Une hausse brutale des échecs peut signaler une attaque par mot de passe.'),
    ts('Refus d\'accès (401 / 403 / 429)', [target('sum by (status) (increase(geocollect_http_requests_total{status=~"401|403|429"}[5m]))', '{{status}}')], 0, 31, bars=True),
    ts('Erreurs JavaScript de l\'application web', [target('sum by (kind) (increase(geocollect_frontend_errors_total[5m]))', '{{kind}}')], 12, 31, bars=True),
]

# ─── 2. Activité métier ──────────────────────────────────────────────────────
_id[0] = 100
EUDR_COLORS = [{'matcher': {'id': 'byName', 'options': n}, 'properties': [{'id': 'color', 'value': {'mode': 'fixed', 'fixedColor': c}}]}
               for n, c in (('compliant', 'green'), ('non_compliant', 'red'), ('pending', 'orange'))]
biz = [
    row('Plateforme', 0),
    stat('Clients actifs', 'max(geocollect_clients{status="actif"})', 0, 1, desc='Super admins actifs (clients).'),
    stat('Coopératives actives', 'max(geocollect_cooperatives{active="oui"})', 4, 1),
    stat('Producteurs', 'max(geocollect_producers)', 8, 1),
    stat('Parcelles mappées', 'max(geocollect_parcels)', 12, 1),
    stat('Hectares mappés', 'max(geocollect_parcels_hectares)', 16, 1, unit='short', decimals=1),
    stat('Abonnements expirant ≤ 7 j', 'max(geocollect_clients_expiring_7d)', 20, 1,
         thresholds=[{'color': 'green', 'value': None}, {'color': 'orange', 'value': 1}], color_mode='background'),
    stat('Utilisateurs actifs (24 h)', 'max(geocollect_active_users_24h)', 0, 5),
    stat('Connexions (24 h)', 'max(geocollect_logins_24h)', 4, 5),
    stat('Parcelles mappées (24 h)', 'max(geocollect_parcels_created_24h)', 8, 5),
    stat('Anciens polygones', 'max(geocollect_legacy_polygons)', 12, 5),
    stat('Messages envoyés (24 h)', 'max(geocollect_messages_24h)', 16, 5),
    stat('Alertes connexions échouées (24 h)', 'max(geocollect_failed_login_alerts_24h)', 20, 5,
         thresholds=[{'color': 'green', 'value': None}, {'color': 'red', 'value': 1}], color_mode='background'),
    row('Mapping et conformité EUDR', 9),
    ts('Parcelles et hectares mappés', [target('max(geocollect_parcels)', 'parcelles'), target('max(geocollect_parcels_hectares)', 'hectares')], 0, 10, w=12),
    pie('Statut EUDR des parcelles', 'max by (status) (geocollect_parcels_by_eudr)', '{{status}}', 12, 10, overrides=EUDR_COLORS),
    pie('Comptes par rôle', 'sum by (role) (geocollect_users{active="oui"})', '{{role}}', 18, 10),
    bargauge('Parcelles par client', 'max by (client) (geocollect_client_parcels)', '{{client}}', 0, 18),
    bargauge('Hectares par client', 'max by (client) (geocollect_client_hectares)', '{{client}}', 12, 18),
    row('Outils (déforestation, Polygon Validator, Self-intersection, GMR)', 26),
    ts('Traitements lancés', [target('sum by (module) (increase(geocollect_tool_runs_total[1h]))', '{{module}}')], 0, 27, bars=True,
       desc='Par heure (compteur du serveur).'),
    ts('Polygones traités', [target('sum by (module) (increase(geocollect_tool_items_total[1h]))', '{{module}}')], 12, 27, bars=True),
    bargauge('Traitements sur 24 h', 'max by (module) (geocollect_tool_runs_24h)', '{{module}}', 0, 35, w=8),
    bargauge('Polygones traités sur 24 h', 'max by (module) (geocollect_tool_items_24h)', '{{module}}', 8, 35, w=8),
    bargauge('Rapports enregistrés', 'max by (kind) (geocollect_reports)', '{{kind}}', 16, 35, w=8),
]

OUT.mkdir(parents=True, exist_ok=True)
for uid, title, desc, panels in (
    ('geocollect-tech', 'GeoCollect — Santé technique', 'Disponibilité, trafic, latence, erreurs et sécurité de l\'API Django (Render).', tech),
    ('geocollect-business', 'GeoCollect — Activité métier', 'Clients, coopératives, mapping, conformité EUDR et utilisation des outils.', biz),
):
    (OUT / f'{uid}.json').write_text(json.dumps(dashboard(uid, title, desc, panels), ensure_ascii=False, indent=2), encoding='utf-8')
    print('écrit', OUT / f'{uid}.json', len(panels), 'panneaux')
