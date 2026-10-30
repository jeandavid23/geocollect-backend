"""
WHISP sans Earth Engine : export GeoJSON pour whisp.earthmap.org et import du CSV de résultats (jointure par
identifiant de parcelle), plus l'appel direct au paquet `openforis-whisp` quand Earth Engine est configuré.
"""
import csv
import io
import json

from .classify import concordance
from .geometry import round_geometry

ID_FIELD = "external_id"      # colonne conservée telle quelle par WHISP dans sa sortie


def export_geojson(features):
    """FeatureCollection prête pour whisp.earthmap.org (WGS84, 6 décimales, identifiant dans `external_id`)."""
    out = []
    for f in features:
        g = f.get("geometry")
        if not g:
            continue
        out.append({"type": "Feature", "geometry": round_geometry(g, 6),
                    "properties": {ID_FIELD: str(f.get("id")), **{k: v for k, v in (f.get("properties") or {}).items()
                                                                  if isinstance(v, (str, int, float)) and k != ID_FIELD}}})
    return json.dumps({"type": "FeatureCollection", "features": out}, ensure_ascii=False)


RISK_COLUMNS = ("risk_pcrop", "risk_acrop", "risk_timber", "risk_livestock")


def import_csv(text, commodity="pcrop"):
    """
    Lit le CSV de résultats WHISP. Renvoie {identifiant: {"risk": …, "raw": {colonne: valeur}}}.
    Jointure sur `external_id` (repli sur `plotId`, puis sur l'ordre des lignes : « #1 », « #2 »…).
    """
    if text.startswith("﻿"):
        text = text[1:]
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    col = f"risk_{commodity}"
    out = {}
    for n, row in enumerate(rows, start=1):
        key = (row.get(ID_FIELD) or row.get("plotId") or f"#{n}").strip()
        risk = (row.get(col) or "").strip()
        out[key] = {"risk": risk, "raw": dict(row)}
    return out


def apply(results, whisp_rows, commodity="pcrop"):
    """Ajoute la sortie WHISP brute et la concordance à chaque résultat (jointure sur l'identifiant)."""
    for r in results:
        if not r or r.get("rejected"):
            continue
        w = whisp_rows.get(str(r.get("id")))
        r["whisp"] = w["raw"] if w else None
        r["whisp_risk"] = w["risk"] if w else None
        r["whisp_concordance"] = concordance(r["classification"]["risk"], w["risk"] if w else None)
    return results


def run_whisp(features, commodity="pcrop", project=None, service_account=None, key_file=None, t=None):
    """
    Appel direct de WHISP (Earth Engine requis). Renvoie (lignes indexées comme import_csv, version du paquet).
    Lève RuntimeError avec un message clair si le paquet ou l'authentification manque.
    """
    try:
        import ee
        import openforis_whisp as whisp
    except ImportError as exc:
        raise RuntimeError("WHISP non installé : pip install openforis-whisp earthengine-api") from exc
    try:
        if service_account and key_file:
            ee.Initialize(ee.ServiceAccountCredentials(service_account, key_file), project=project)
        else:
            ee.Initialize(project=project)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Earth Engine non configuré ou refusé : {exc}") from exc
    import tempfile, os  # noqa: E401
    with tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False, encoding="utf-8") as fh:
        fh.write(export_geojson(features))
        path = fh.name
    try:
        df = whisp.whisp_formatted_stats_geojson_to_df(path, external_id_column=ID_FIELD)
        # mêmes seuils que notre classement pour les 4 indicateurs « cultures pérennes »
        from .config import thresholds
        t = t or thresholds()
        df = whisp.whisp_risk(df, ind_1_pcent_threshold=t["whisp_treecover_pct"],
                              ind_2_pcent_threshold=t["commodity_share_low"],
                              ind_3_pcent_threshold=t["predisturbance_share_low"],
                              ind_4_pcent_threshold=t["whisp_disturbance_after_pct"])
    finally:
        os.unlink(path)
    version = getattr(whisp, "__version__", None)
    if version is None:
        try:
            from importlib.metadata import version as _v
            version = _v("openforis-whisp")
        except Exception:  # noqa: BLE001
            version = "inconnue"
    return import_csv(df.to_csv(index=False), commodity), version
