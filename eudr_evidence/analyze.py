"""
Orchestration hors Earth Engine : contrôle des géométries, regroupement des parcelles par fenêtres (du nord au sud),
une lecture par source et par groupe, puis preuves et classement de chaque parcelle.

Utilisé tel quel par GeoCollect (rasterio), la web app et le pipeline ; les plugins QGIS passent le moteur GDAL.
"""
import concurrent.futures
import threading

from . import evidence as ev
from .classify import classify
from .config import thresholds
from .geometry import GRID_RES, bbox, grid_for, rasterize, validate
from .readers.base import TileSet, source_specs

MAX_GROUP_DEG = 0.1      # cellule de regroupement : 0,1° (≈ 11 km) → une lecture par source et par cellule (≈ 1,5 Mpx à 10 m)


def _groups(items, cell):
    """Regroupe les parcelles par cellule fixe de `cell` degrés (selon le centre de l'emprise), du nord au sud."""
    import math
    buckets = {}
    for it in items:
        b = it["bbox"]
        key = (math.floor((b[1] + b[3]) / 2 / cell), math.floor((b[0] + b[2]) / 2 / cell))
        buckets.setdefault(key, []).append(it)
    for key in sorted(buckets, key=lambda k: (-k[0], k[1])):
        group = buckets[key]
        gb = (min(i["bbox"][0] for i in group), min(i["bbox"][1] for i in group),
              max(i["bbox"][2] for i in group), max(i["bbox"][3] for i in group))
        yield gb, group


def analyze(features, backend_factory, env_factory=None, t=None, context_fn=None, extra_layers=None,
            remote_prefix="/vsicurl/", layers=None, threads=3, progress=None, max_group_deg=MAX_GROUP_DEG):
    """
    features        [{"id", "geometry" (GeoJSON WGS84), "area_ha" (facultatif), "properties" (facultatif)}]
    backend_factory fonction → nouveau Backend (un par fil)
    env_factory     fonction → gestionnaire de contexte GDAL (rasterio.Env…) ou None
    context_fn      fonction(item) → {"protected": [...], "rdue": {...}} (fournie par l'outil hôte)
    extra_layers    fonction(item, lon, lat) → {couche: tableau} (couches Earth Engine facultatives)
    layers          sous-ensemble des couches à lire (par défaut : toutes celles de readers.base.source_specs)
    """
    t = t or thresholds()
    specs, _ = source_specs(remote_prefix)
    if layers:
        specs = {k: v for k, v in specs.items() if k in layers}

    items, rejected = [], []
    for i, f in enumerate(features):
        geom, report = validate(f.get("geometry"), f.get("area_ha"), t)
        if geom is None:
            rejected.append({"index": i, "id": f.get("id"), "rejected": True, "reasons": report["reasons"]})
            continue
        items.append({"index": i, "id": f.get("id"), "geometry": geom, "bbox": bbox(geom), "report": report,
                      "properties": f.get("properties") or {}})

    results = [None] * len(features)
    for r in rejected:
        results[r["index"]] = r
    unavailable = set()
    lock = threading.Lock()
    done = [0]

    def run_group(args):
        gb, group = args
        backend = backend_factory()
        ctx = env_factory() if env_factory else None
        try:
            if ctx:
                ctx.__enter__()
            sets = {}
            for name, fn in specs.items():
                ts = TileSet(backend, fn)
                try:
                    ok = ts.load(gb)
                except Exception:  # noqa: BLE001 — source injoignable : l'analyse continue sans elle (signalé)
                    ok = False
                sets[name] = ts if ok else None
                if not ok:
                    with lock:
                        unavailable.add(name)
            for it in group:
                lon, lat = grid_for(it["geometry"], GRID_RES)
                mask = rasterize(it["geometry"], lon, lat)
                pixel_ha = ev.pixel_area_ha(lat[:, 0], GRID_RES)
                lyr = {name: (ts.sample(lon, lat) if ts else None) for name, ts in sets.items()}
                if extra_layers:
                    lyr.update(extra_layers(it, lon, lat) or {})
                evid = ev.compute_evidence(lyr, mask, pixel_ha, t, it["report"]["area_ha"])
                context = context_fn(it) if context_fn else {}
                cls = classify(evid, context, t)
                results[it["index"]] = {"index": it["index"], "id": it["id"], "rejected": False,
                                        "geometry_report": it["report"], "evidence": evid, "context": context,
                                        "classification": cls}
                with lock:
                    done[0] += 1
                    if progress:
                        progress(done[0], len(items))
        finally:
            if ctx:
                ctx.__exit__(None, None, None)
            backend.close()

    groups = list(_groups(items, max_group_deg))
    if threads and threads > 1 and len(groups) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
            list(pool.map(run_group, groups))
    else:
        for g in groups:
            run_group(g)
    return {"results": results, "rejected": len(rejected), "analysed": len(items),
            "layers": sorted(specs), "layers_unavailable": sorted(unavailable)}
