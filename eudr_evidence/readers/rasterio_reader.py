"""Lecture avec rasterio (GeoCollect, web app, pipeline) — COG distants via /vsicurl/."""
from .base import Backend

GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
    GDAL_HTTP_MULTIRANGE="YES", GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="1", GDAL_HTTP_TIMEOUT="60",
    VSI_CACHE="TRUE", VSI_CACHE_SIZE=str(64 * 1024 * 1024), GDAL_CACHEMAX=96,
)


class RasterioBackend(Backend):
    def _open(self, path):
        import rasterio
        return rasterio.open(path)

    def _georef(self, ds):
        t = ds.transform
        return t.c, t.a, t.f, -t.e, ds.width, ds.height

    def _read(self, ds, c0, r0, w, h):
        from rasterio.windows import Window
        return ds.read(1, window=Window(c0, r0, w, h))


def env():
    import rasterio
    return rasterio.Env(**GDAL_ENV)
