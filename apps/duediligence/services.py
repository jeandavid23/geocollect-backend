"""
Exécution d'une analyse de diligence raisonnée, en tâche différée (fil d'exécution) avec reprise sur erreur.

Hors Earth Engine : moteur commun `eudr_evidence` (JRC GFC2020, TMF, WorldCover, Hansen lus en COG / cache),
+ contexte : aires protégées (WDPA, KBA en base) et matrice RDUE nationale (LandZone).
WHISP (Earth Engine) : seulement si WHISP_ENABLED et des identifiants Earth Engine sont configurés ; sinon le
rapport indique « WHISP non exécuté ». Aucune requête SQL par parcelle : chargement groupé, écritures groupées.
"""
import logging
import os
import threading
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, connection
from django.utils import timezone

log = logging.getLogger(__name__)

TOOL_NAME = 'GeoCollect EUDR'
WHISP_BATCH = 300
STALE_AFTER = timedelta(minutes=30)
DATASETS_OFFLINE = ['jrc_gfc2020', 'tmf', 'worldcover2020', 'hansen', 'rdue_civ']


def tool_version():
    return (os.environ.get('RENDER_GIT_COMMIT') or os.environ.get('GIT_COMMIT') or 'local')[:12]


def whisp_configured():
    return bool(getattr(settings, 'WHISP_ENABLED', False) and (
        getattr(settings, 'GEE_PROJECT', '') or getattr(settings, 'GEE_SERVICE_ACCOUNT', '')))


# ── Chargement des parcelles (requêtes groupées) ─────────────────────────────────────────────────

def load_features(analysis):
    from apps.parcels.models import LegacyParcel, Parcel
    coop = analysis.cooperative
    scope = analysis.scope or {}
    source = scope.get('source', 'both')
    producer_ids = None
    if analysis.lot_id:
        producer_ids = list(analysis.lot.lines.values_list('producer_id', flat=True))
    feats = []
    if source in ('parcels', 'both'):
        qs = Parcel.objects.filter(cooperative=coop)
        if producer_ids is not None:
            qs = qs.filter(producer_id__in=producer_ids)
        for p in qs.values('id', 'field_id', 'geometry', 'area_hectares', 'village', 'producer_id',
                           'producer__last_name', 'producer__first_name', 'producer__field_id_base'):
            feats.append({'id': f"parcel:{p['id']}", 'geometry': p['geometry'],
                          'area_ha': p['area_hectares'] if (p['geometry'] or {}).get('type') == 'Point' else None,
                          'properties': {'kind': 'parcel', 'label': p['field_id'], 'village': p['village'] or '',
                                         'producer_id': str(p['producer_id']) if p['producer_id'] else None,
                                         'producer_code': p['producer__field_id_base'] or '',
                                         'producer': f"{p['producer__last_name'] or ''} {p['producer__first_name'] or ''}".strip()}})
    if source in ('legacy', 'both'):
        qs = LegacyParcel.objects.filter(cooperative=coop)
        if producer_ids is not None:
            qs = qs.filter(producer_id__in=producer_ids)
        for p in qs.values('id', 'code', 'name', 'geometry', 'area_hectares', 'producer_id',
                           'producer__last_name', 'producer__first_name', 'producer__field_id_base', 'producer__village'):
            feats.append({'id': f"legacy:{p['id']}", 'geometry': p['geometry'], 'area_ha': None,
                          'properties': {'kind': 'legacy', 'label': p['code'] or p['name'] or str(p['id'])[:8],
                                         'village': p['producer__village'] or '',
                                         'producer_id': str(p['producer_id']) if p['producer_id'] else None,
                                         'producer_code': p['producer__field_id_base'] or '',
                                         'producer': f"{p['producer__last_name'] or ''} {p['producer__first_name'] or ''}".strip()}})
    return feats


# ── Contexte : aires protégées et matrice RDUE ───────────────────────────────────────────────────

class Context(object):
    def __init__(self):
        import shapely
        from shapely.geometry import shape
        from .models import ProtectedArea
        self.zones = []
        for src, name, desig, g in ProtectedArea.objects.values_list('source', 'name', 'designation', 'geometry'):
            try:
                z = shape(g)
                if not z.is_valid:
                    z = shapely.make_valid(z)
                self.zones.append((src, name, desig, z))
            except Exception:  # noqa: BLE001 — géométrie illisible ignorée
                continue
        self.tree = shapely.STRtree([z[3] for z in self.zones]) if self.zones else None
        self.versions = sorted({v for v in ProtectedArea.objects.values_list('source', 'version').distinct()})

    def __call__(self, item):
        import shapely
        from shapely.geometry import shape
        from apps.parcels import rdue
        geom = shape(item['geometry'])
        out = {'protected': [], 'rdue': {}}
        if self.tree is not None and geom.area > 0:
            for j in self.tree.query(geom, predicate='intersects').tolist():
                src, name, desig, z = self.zones[j]
                inter = shapely.intersection(geom, z).area
                pct = 100.0 * inter / geom.area
                if pct > 0:
                    out['protected'].append({'kind': src, 'name': name, 'designation': desig, 'pct': round(pct, 2),
                                             'ha': round(pct / 100.0 * item['report']['area_ha'], 4)})
            out['protected'].sort(key=lambda p: -p['pct'])
        try:
            cat, zone = rdue.category_of(item['geometry'])
            out['rdue'] = {'category': cat, 'zone': zone, 'legalite': rdue.CATEGORY_LEGALITY.get(cat, rdue.LEGAL_CHECK)}
        except Exception:  # noqa: BLE001
            pass
        # Forêts classées : la base nationale (RDUE) fait foi. Le contour WDPA d'une forêt classée englobe ses enclaves ;
        # une parcelle reconnue en enclave / agro-forêt n'est donc pas pénalisée par ce contour (doublon de la RDUE).
        if out['rdue'].get('category') in (rdue.CAT_ENCLAVE, rdue.CAT_AGROFORET):
            out['protected'] = [p for p in out['protected'] if not _is_classified_forest(p)]
        return out


def _is_classified_forest(p):
    d = (p.get('designation') or '').lower()
    return p.get('kind') == 'WDPA' and ('classified forest' in d or 'forêt classée' in d or 'foret classee' in d)


# ── Exécution ────────────────────────────────────────────────────────────────────────────────────

def start(analysis_id):
    th = threading.Thread(target=run, args=(analysis_id,), daemon=True, name=f'dd-{analysis_id}')
    th.start()
    return th


def run(analysis_id):
    from eudr_evidence import manifest as mf
    from eudr_evidence.analyze import analyze
    from eudr_evidence.classify import summarize
    from eudr_evidence.config import thresholds
    from eudr_evidence.readers import rasterio_reader
    from .models import DDAnalysis

    close_old_connections()
    a = DDAnalysis.objects.select_related('cooperative', 'lot').get(pk=analysis_id)
    try:
        DDAnalysis.objects.filter(pk=a.pk).update(status='running', progress=0, error='')
        features = load_features(a)
        DDAnalysis.objects.filter(pk=a.pk).update(total=len(features))
        t = thresholds(a.thresholds)
        ctx = Context()
        last = [timezone.now()]

        def progress(done, total):
            now = timezone.now()
            if done == total or (now - last[0]).total_seconds() > 3:
                last[0] = now
                DDAnalysis.objects.filter(pk=a.pk).update(progress=done, updated_at=now)

        out = analyze(features, rasterio_reader.RasterioBackend, env_factory=rasterio_reader.env, t=t,
                      context_fn=ctx, threads=settings.EUDR_THREADS, progress=progress)
        props = {f['id']: f['properties'] for f in features}
        results = []
        for r in out['results']:
            if r is None:
                continue
            r['properties'] = props.get(r['id'], {})
            r['whisp_concordance'] = 'non_calcule'
            results.append(r)

        used = DATASETS_OFFLINE + (['wdpa'] if any(z[0] == 'WDPA' for z in ctx.zones) else []) \
            + (['kba'] if any(z[0] == 'KBA' for z in ctx.zones) else [])
        whisp = {'executed': False, 'version': None, 'reason': 'Earth Engine non configuré (WHISP non exécuté)'}
        if whisp_configured():
            DDAnalysis.objects.filter(pk=a.pk).update(whisp_status='en_cours')
            whisp = _run_whisp(a, features, results, t)
        summary = summarize(results)
        summary['layers_unavailable'] = out['layers_unavailable']
        summary['protected_versions'] = ctx.versions
        a.refresh_from_db()
        a.set_results(results)
        a.summary = summary
        a.manifest = mf.build(features, t, TOOL_NAME, tool_version(), used, summary=summary, whisp=whisp,
                              country=(a.report_meta or {}).get('country', 'CI'),
                              layers_unavailable=out['layers_unavailable'], analysis_id=str(a.pk),
                              extra={'cooperative': {'id': str(a.cooperative_id), 'name': a.cooperative.name},
                                     'scope': a.scope, 'lot': a.lot.code if a.lot_id else None,
                                     'whisp_status': a.whisp_status})
        a.status = 'done'
        a.progress = a.total
        a.save()
    except Exception as exc:  # noqa: BLE001 — l'erreur est enregistrée et l'analyse peut être relancée
        log.exception('Analyse de diligence raisonnée')
        DDAnalysis.objects.filter(pk=a.pk).update(status='error', error=str(exc)[:2000])
    finally:
        connection.close()


def _run_whisp(analysis, features, results, t):
    """WHISP par lots (taille limitée, reprise : un lot en erreur n'arrête pas les suivants)."""
    from eudr_evidence import whisp_io
    from .models import DDAnalysis
    rows, version, errors = {}, None, []
    ok_feats = [f for f in features if f.get('geometry')]
    for i in range(0, len(ok_feats), WHISP_BATCH):
        batch = ok_feats[i:i + WHISP_BATCH]
        for attempt in range(2):
            try:
                r, version = whisp_io.run_whisp(batch, project=getattr(settings, 'GEE_PROJECT', '') or None,
                                                service_account=getattr(settings, 'GEE_SERVICE_ACCOUNT', '') or None,
                                                key_file=getattr(settings, 'GEE_KEY_FILE', '') or None, t=t)
                rows.update(r)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 1:
                    errors.append(f'lot {i // WHISP_BATCH + 1} : {exc}')
    whisp_io.apply(results, rows)
    status = 'execute' if rows and not errors else ('erreur' if not rows else 'execute')
    DDAnalysis.objects.filter(pk=analysis.pk).update(whisp_status=status, whisp_version=version or '',
                                                     whisp_message='; '.join(errors)[:2000])
    return {'executed': bool(rows), 'version': version, 'errors': errors}


def mark_stale():
    """Analyse « en cours » sans nouvelles depuis 30 min : serveur redémarré → erreur (à relancer)."""
    from .models import DDAnalysis
    DDAnalysis.objects.filter(status__in=('pending', 'running'), updated_at__lt=timezone.now() - STALE_AFTER).update(
        status='error', error='Analyse interrompue (redémarrage du serveur) : relancez-la.')
