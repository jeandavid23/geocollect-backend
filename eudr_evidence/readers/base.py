"""
Lecture des sources raster, commune aux bibliothèques (rasterio côté serveurs, GDAL côté QGIS).

Une source = une fonction qui donne, pour une emprise, les fichiers (tuiles) à lire ; chaque tuile est lue UNE fois par
groupe de parcelles (fenêtre couvrant le groupe), puis échantillonnée au plus proche voisin sur la grille de chaque
parcelle. Seule la lecture brute dépend de la bibliothèque (méthodes _open / _georef / _read).
"""
import math
import os
import threading

import numpy as np

from .. import catalog


# ── Tuilage des sources ──────────────────────────────────────────────────────────────────────────

def hansen_tiles(minx, miny, maxx, maxy):
    """Tuiles Hansen 10° nommées par le coin nord-ouest : « 10N_010W » couvre lat 0..10, lon -10..0."""
    out = []
    for top in range(int(math.ceil(maxy / 10.0)) * 10, int(math.floor(miny / 10.0)) * 10, -10):
        for left in range(int(math.floor(minx / 10.0)) * 10, int(math.ceil(maxx / 10.0)) * 10, 10):
            lat = f"{abs(top):02d}{'N' if top >= 0 else 'S'}"
            lon = f"{abs(left):03d}{'E' if left >= 0 else 'W'}"
            out.append(f"{lat}_{lon}")
    return out


def worldcover_tiles(minx, miny, maxx, maxy):
    """Tuiles WorldCover 3° nommées par le coin sud-ouest : « N06W006 » couvre lat 6..9, lon -6..-3."""
    out = []
    for lat in range(int(math.floor(miny / 3.0)) * 3, int(math.ceil(maxy / 3.0)) * 3, 3):
        for lon in range(int(math.floor(minx / 3.0)) * 3, int(math.ceil(maxx / 3.0)) * 3, 3):
            out.append(f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}")
    return out


def tmf_tiles(minx, miny, maxx, maxy):
    """Tuiles TMF 10° nommées par le coin nord-ouest : (« N10 », « W10 »)."""
    out = []
    for top in range(int(math.ceil(maxy / 10.0)) * 10, int(math.floor(miny / 10.0)) * 10, -10):
        for left in range(int(math.floor(minx / 10.0)) * 10, int(math.ceil(maxx / 10.0)) * 10, 10):
            out.append((f"{'N' if top >= 0 else 'S'}{abs(top)}", f"{'E' if left >= 0 else 'W'}{abs(left)}"))
    return out


def tmf_cache_dir():
    d = os.environ.get("EUDR_TMF_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "eudr_evidence", "tmf")
    os.makedirs(d, exist_ok=True)
    return d


def tile_cache_dir():
    """Cache des tuiles téléchargées à l'avance (Hansen, WorldCover…) pour travailler hors ligne."""
    return os.environ.get("EUDR_TILE_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "eudr_evidence", "tiles")


def tile_cache_path(url):
    """Chemin local de la tuile `url` dans le cache (qu'elle existe ou non) ; même nom de fichier que sur le serveur."""
    return os.path.join(tile_cache_dir(), os.path.basename(url.split("?")[0]))


def _local_or_remote(remote_prefix, url):
    """Tuile du cache local si elle a été téléchargée, sinon lecture partielle distante."""
    local = tile_cache_path(url)
    return local if os.path.exists(local) else remote_prefix + url


_dl_lock = threading.Lock()


def tmf_tile_url(dataset_key, lat, lon):
    return catalog.TMF_HTTP.format(dataset=catalog.SOURCES["tmf"]["http_datasets"][dataset_key], lat=lat, lon=lon)


def tmf_tile_path(dataset_key, lat, lon):
    """Chemin d'une tuile TMF dans le cache (qu'elle existe ou non) ; les plugins la téléchargent par la pile réseau de QGIS."""
    ds = catalog.SOURCES["tmf"]["http_datasets"][dataset_key]
    return os.path.join(tmf_cache_dir(), f"TMF_{ds}_{lat}_{lon}.tif")


def tmf_local_path(dataset_key, lat, lon, download=True, timeout=1800):
    """Chemin local d'une tuile TMF (téléchargée et gardée en cache : le serveur du JRC refuse la lecture partielle)."""
    path = tmf_tile_path(dataset_key, lat, lon)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    # EUDR_TMF_DOWNLOAD=0 : jamais de téléchargement pendant une analyse (cache préparé à l'avance) ; couche absente sinon
    if not download or os.environ.get("EUDR_TMF_DOWNLOAD", "1") == "0":
        return None
    import urllib.request
    url = tmf_tile_url(dataset_key, lat, lon)
    with _dl_lock:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        tmp = path + ".part"
        with urllib.request.urlopen(url, timeout=timeout) as r, open(tmp, "wb") as f:
            expected = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        if expected and got != expected:      # le serveur du JRC coupe parfois les longs transferts
            os.remove(tmp)
            raise IOError(f"Tuile TMF incomplète ({got} / {expected} octets) : nouvel essai nécessaire")
        os.replace(tmp, path)
    return path


def warm_tmf_cache(bounds, log=print):
    """Télécharge à l'avance les tuiles TMF d'une emprise (minx, miny, maxx, maxy) ; renvoie les chemins."""
    out = []
    for la, lo in tmf_tiles(*bounds):
        for key in ("annual_change", "deforestation_year", "degradation_year"):
            log(f"TMF {key} {la}_{lo}…")
            out.append(tmf_local_path(key, la, lo, download=True))
    return out


def source_specs(remote_prefix):
    """
    Couches lues hors Earth Engine : nom de couche → fonction emprise → liste de chemins (GDAL / rasterio).
    `remote_prefix` : « /vsicurl/ » (lecture partielle HTTP).
    """
    v = catalog.HANSEN_VERSION

    def hansen(layer):
        return lambda b: [_local_or_remote(remote_prefix, catalog.HANSEN_HTTP.format(layer=layer, tile=t))
                          for t in hansen_tiles(*b)]

    def tmf(key):
        return lambda b: [p for p in (tmf_local_path(key, la, lo) for la, lo in tmf_tiles(*b)) if p]

    return {
        "jrc_gfc2020": lambda b: [remote_prefix + catalog.JRC_GFC2020_COG],      # 40,5 Go : toujours lu à distance
        "worldcover2020": lambda b: [_local_or_remote(remote_prefix, catalog.WORLDCOVER_2020_HTTP.format(tile=t))
                                     for t in worldcover_tiles(*b)],
        "hansen_treecover": hansen("treecover2000"),
        "hansen_lossyear": hansen("lossyear"),
        "tmf_annual_2020": tmf("annual_change"),
        "tmf_deforestation": tmf("deforestation_year"),
        "tmf_degradation": tmf("degradation_year"),
    }, v


# ── Lecture et échantillonnage ───────────────────────────────────────────────────────────────────

class TileSet(object):
    """Couche composée de tuiles ; une instance par fil d'exécution (les jeux de données GDAL ne se partagent pas)."""

    def __init__(self, backend, paths_for):
        self.backend = backend
        self.paths_for = paths_for
        self.windows = []          # [(tableau, x0, y0, xres, yres)]

    def load(self, bounds, pad=0.002):
        minx, miny, maxx, maxy = bounds[0] - pad, bounds[1] - pad, bounds[2] + pad, bounds[3] + pad
        self.windows = []
        for path in self.paths_for((minx, miny, maxx, maxy)):
            ds = self.backend.open(path)
            if ds is None:
                continue
            gx0, gxres, gy0, gyres, width, height = self.backend.georef(ds)
            c0 = max(0, int(math.floor((minx - gx0) / gxres)))
            c1 = min(width, int(math.ceil((maxx - gx0) / gxres)))
            r0 = max(0, int(math.floor((gy0 - maxy) / gyres)))
            r1 = min(height, int(math.ceil((gy0 - miny) / gyres)))
            if c1 <= c0 or r1 <= r0:
                continue
            arr = self.backend.read(ds, c0, r0, c1 - c0, r1 - r0)
            self.windows.append((arr, gx0 + c0 * gxres, gy0 - r0 * gyres, gxres, gyres))
        return bool(self.windows)

    def sample(self, lon, lat, fill=0):
        out = np.full(lon.shape, fill, dtype=self.windows[0][0].dtype if self.windows else np.uint8)
        for arr, x0, y0, xres, yres in self.windows:
            cols = np.floor((lon - x0) / xres).astype(np.int64)
            rows = np.floor((y0 - lat) / yres).astype(np.int64)
            ok = (cols >= 0) & (rows >= 0) & (cols < arr.shape[1]) & (rows < arr.shape[0])
            out[ok] = arr[rows[ok], cols[ok]]
        return out


class Backend(object):
    """Interface de lecture brute ; voir rasterio_reader.RasterioBackend et gdal_reader.GdalBackend."""

    def __init__(self):
        self._cache = {}

    def open(self, path):
        if path not in self._cache:
            try:
                self._cache[path] = self._open(path)
            except Exception:  # noqa: BLE001 — tuile absente (océan) ou coupure réseau : la couche reste vide
                self._cache[path] = None
        return self._cache[path]

    def georef(self, ds):
        return self._georef(ds)

    def read(self, ds, c0, r0, w, h, attempts=3):
        last = None
        for _ in range(attempts):
            try:
                return self._read(ds, c0, r0, w, h)
            except Exception as exc:  # noqa: BLE001 — coupure réseau : nouvel essai
                last = exc
        raise last

    def close(self):
        for ds in self._cache.values():
            try:
                if ds is not None and hasattr(ds, "close"):
                    ds.close()
            except Exception:  # noqa: BLE001
                pass
        self._cache = {}
