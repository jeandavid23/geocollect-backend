"""Lecture avec GDAL (osgeo) — plugins QGIS, où rasterio n'est pas toujours installé."""
from .base import Backend

GDAL_CONFIG = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIRANGE": "YES", "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "4", "GDAL_HTTP_RETRY_DELAY": "1", "GDAL_HTTP_TIMEOUT": "60", "VSI_CACHE": "TRUE",
}


class GdalBackend(Backend):
    def __init__(self):
        super().__init__()
        from osgeo import gdal
        # Pas de gdal.UseExceptions() : ce réglage est global et changerait le comportement des autres extensions
        # QGIS ; les erreurs sont signalées explicitement ci-dessous.
        # options limitées au fil de lecture (un Backend par fil) : la session QGIS de l'utilisateur n'est pas modifiée
        for k, v in GDAL_CONFIG.items():
            gdal.SetThreadLocalConfigOption(k, v)

    def _open(self, path):
        from osgeo import gdal
        ds = gdal.Open(path)
        if ds is None:
            raise IOError(f"Ouverture impossible : {path}")
        return ds

    def _georef(self, ds):
        gt = ds.GetGeoTransform()
        return gt[0], gt[1], gt[3], -gt[5], ds.RasterXSize, ds.RasterYSize

    def _read(self, ds, c0, r0, w, h):
        arr = ds.GetRasterBand(1).ReadAsArray(c0, r0, w, h)
        if arr is None:
            raise IOError("Lecture impossible (coupure réseau ?)")
        return arr


class env(object):  # noqa: N801 — même usage que rasterio_reader.env()
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
