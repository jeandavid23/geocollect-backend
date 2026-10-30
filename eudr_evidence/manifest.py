"""Étape 7 — traçabilité : manifeste d'une analyse (à conserver au moins 5 ans, art. 12)."""
import datetime
import hashlib
import json
import uuid

from . import __version__, ENGINE_NAME
from .catalog import versions
from .config import REGULATION, country_risk


def sha256_features(features):
    """Empreinte de l'entrée : GeoJSON canonique (clés triées, identifiant + géométrie) → SHA-256."""
    canon = json.dumps([{"id": str(f.get("id")), "geometry": f.get("geometry")} for f in features],
                       sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def build(features, thresholds, tool, tool_version, datasets_used, summary=None, whisp=None, country="CI",
          layers_unavailable=None, extra=None, analysis_id=None):
    return {
        "analysis_id": analysis_id or str(uuid.uuid4()),
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "tool": {"name": tool, "version": tool_version},
        "engine": {"name": ENGINE_NAME, "version": __version__},
        "regulation": {k: REGULATION[k] for k in ("regulation", "cutoff_date", "retention_years", "application_dates")},
        "country": {"iso2": country, "eu_risk": country_risk(country)},
        "datasets": versions(datasets_used),
        "layers_unavailable": sorted(layers_unavailable or []),
        "whisp": whisp or {"executed": False, "version": None, "reason": "WHISP non exécuté"},
        "thresholds": thresholds,
        "input": {"features": len(features), "sha256": sha256_features(features)},
        "summary": summary,
        **(extra or {}),
    }
