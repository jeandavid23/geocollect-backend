"""
Rapport de diligence raisonnée (art. 9 à 12) : textes communs, ligne « à plat » par parcelle (Excel, GeoJSON),
parcelles retenues pour la déclaration et export TRACES. Mêmes règles que le rapport de GeoCollect (utils/ddReport.ts).

Les textes sont regroupés dans TXT pour la future version anglaise.
"""
import datetime

from .catalog import FOREST_LABELS as SOURCE_FOREST_LABELS, LOSS_LABELS
from .config import FOREST_LABELS, RISK_LABELS

TXT = {
    "title": "Rapport de diligence raisonnée",
    "subtitle": "Règlement (UE) 2023/1115 relatif à la déforestation (EUDR) — articles 9, 10 et 11",
    "method": [
        "L'analyse croise plusieurs jeux de données publiés et reconnus (« convergence de preuves »), au lieu de "
        "s'appuyer sur une seule carte.",
        "1. Contrôle des géométries : coordonnées WGS84 à 6 décimales, polygones valides, polygone obligatoire "
        "au-delà de 4 ha.",
        "2. Forêt au 31/12/2020 : chaque source « vote » si elle classe au moins le seuil indiqué de la parcelle en "
        "forêt. La parcelle est « forêt » si le nombre de votes atteint le minimum ; « incertaine » avec un seul vote "
        "ou si la carte de référence de l'UE (JRC 2020) indique de la forêt.",
        "3. Perte après 2020 : seules les pertes situées dans les pixels qui étaient forêt en 2020 sont comptées ; "
        "une même perte vue par plusieurs sources n'est comptée qu'une fois (union des pixels).",
        "4. Contexte : usage agricole établi avant la date butoir (cultures, perturbations antérieures), aires "
        "protégées (WDPA, KBA), domaine forestier national (forêts classées, parcs, enclaves).",
        "5. Classement : risque faible, à vérifier ou élevé, avec un score de convergence (0 à 100) et une "
        "justification lisible. La logique suit celle de l'outil WHISP de la FAO pour les cultures pérennes "
        "(cacao, café).",
    ],
    "limits": [
        "Résolution des données de 10 à 30 m : les petites parcelles et les bordures sont mesurées avec une "
        "incertitude.",
        "Le cacao agroforestier (sous ombrage) peut être confondu avec de la forêt par certaines cartes ; c'est "
        "pourquoi aucune source n'est utilisée seule.",
        "Nuages et dates d'acquisition peuvent retarder la détection des pertes récentes.",
        "Les jeux de données sont des aides à la décision : ils ne constituent pas une preuve juridique. Les "
        "parcelles « à vérifier » ou « élevé » doivent faire l'objet d'une vérification (imagerie, visite terrain, "
        "documents).",
    ],
    "legality": [
        ("Droits d'usage des terres", "land"), ("Protection de l'environnement", "env"),
        ("Droit du travail", "work"), ("Droits humains", "rights"),
        ("Consentement libre, préalable et éclairé (CLIP)", "fpic"), ("Fiscalité", "tax"),
        ("Lutte contre la corruption", "corruption"), ("Réglementation commerciale et douanière", "trade"),
    ],
    "declaration": "L'opérateur déclare avoir exercé la diligence raisonnée prévue aux articles 8 à 11 du règlement "
                   "(UE) 2023/1115 pour les produits couverts par ce rapport, sur la base des informations, de "
                   "l'évaluation des risques et des mesures d'atténuation décrites ci-dessus.",
    "to_provide": "à fournir",
}

THRESHOLD_LABELS = {
    "min_forest_share": "Part minimale de forêt pour qu'une source vote « forêt » (%)",
    "min_votes": "Votes nécessaires pour « forêt 2020 »",
    "hansen_treecover_min": "Couvert arboré Hansen 2000 (%)",
    "min_loss_ha": "Perte minimale signalée (ha)",
    "min_loss_pct": "ou part minimale de la parcelle (%)",
    "align_whisp": "Logique WHISP (cultures pérennes)",
    "commodity_share_low": "Culture en 2020 → risque faible au-delà de (%)",
    "predisturbance_share_low": "Perturbation avant 2020 → risque faible au-delà de (%)",
    "protected_high_pct": "Aire protégée → risque élevé au-delà de (%)",
    "point_default_radius_m": "Rayon d'une parcelle déclarée par un point (m)",
    "whisp_treecover_pct": "WHISP : couvert arboré minimal (%)",
    "whisp_disturbance_after_pct": "WHISP : perturbation après 2020 (%)",
}

DECISIONS = {
    "confirmer": "Risque confirmé", "lever": "Risque levé", "visite": "Visite terrain demandée",
    "documents": "Documents demandés", "exclure": "Parcelle exclue du lot",
}
CONCORDANCE_LABELS = {"concordant": "Concordant", "plus_strict": "Plus strict que WHISP",
                      "moins_strict": "Moins strict que WHISP", "non_calcule": "Non calculé"}
RISK_COLORS = {"faible": "#15803d", "a_verifier": "#d97706", "eleve": "#b91c1c"}
SOURCE_LABELS = {**SOURCE_FOREST_LABELS, **LOSS_LABELS}

TRACES_DECIMALS = 6
TRACES_MAX_TEXT = 255
TRACES_EXCLUDED_DECISIONS = ("exclure", "confirmer")


def fmt_num(value, decimals=2):
    """Nombre à la française : « 12,5 » (zéros inutiles retirés), « — » si absent."""
    if value is None:
        return "—"
    text = f"{float(value):,.{decimals}f}".replace(",", " ")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def fmt_threshold(value):
    """Valeur de seuil lisible : « activée / désactivée », « 10 », « 0,05 »."""
    if isinstance(value, bool):
        return "activée" if value else "désactivée"
    return fmt_num(value, 2)


def fmt_date(iso):
    """Date ISO → « 28/10/2026 »."""
    if not iso:
        return ""
    try:
        return datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        return str(iso)[:10]


def last_decision(decisions, key):
    """Dernière décision humaine enregistrée pour une parcelle (les décisions s'ajoutent, aucune n'est effacée)."""
    found = [d for d in (decisions or []) if d.get("parcel_key") == key]
    return found[-1] if found else None


def retained(results, decisions=None):
    """Parcelles retenues pour la déclaration : risque faible, ou risque levé par une décision humaine documentée."""
    out = []
    for r in results or []:
        if not r or r.get("rejected") or not r.get("classification"):
            continue
        d = last_decision(decisions, r["id"]) or {}
        if d.get("decision") in TRACES_EXCLUDED_DECISIONS:
            continue
        if r["classification"]["risk"] == "faible" or d.get("decision") == "lever":
            out.append(r)
    return out


def _years(v):
    first, last = v.get("first_year"), v.get("last_year")
    if not first:
        return ""
    return str(first) if first == last else f"{first}-{last}"


def flat_row(r, decisions=None):
    """Ligne « à plat » d'une parcelle : toutes les colonnes de preuve (Excel, GeoJSON) — mêmes colonnes que GeoCollect."""
    c, e, ctx = r.get("classification") or {}, r.get("evidence") or {}, r.get("context") or {}
    p, rep = r.get("properties") or {}, r.get("geometry_report") or {}
    dec = last_decision(decisions, r.get("id")) or {}
    rdue = ctx.get("rdue") or {}
    row = {
        "identifiant": p.get("label") or r.get("id"), "code_producteur": p.get("producer_code", ""),
        "producteur": p.get("producer", ""), "village": p.get("village", ""),
        "surface_ha": e.get("area_ha", rep.get("area_ha")), "geometrie": rep.get("kind", ""),
        "rejetee": "OUI" if r.get("rejected") else "NON", "motif_rejet": " ; ".join(r.get("reasons") or []),
        "risque": RISK_LABELS["fr"].get(c.get("risk"), ""), "score_convergence": c.get("score"),
        "foret_2020": FOREST_LABELS["fr"].get(c.get("forest2020"), ""),
        "votes_foret_2020": f"{c['forest2020_votes']}/{len(c.get('forest2020_available') or [])}" if c else "",
        "sources_foret_2020": ", ".join(SOURCE_LABELS.get(s, s) for s in c.get("forest2020_sources") or []),
    }
    for s in ("jrc_gfc2020", "tmf", "worldcover2020", "hansen"):
        row[f"foret2020_{s}_pct"] = ((e.get("forest2020") or {}).get(s) or {}).get("pct")
    row["perte_post2020_ha"] = e.get("loss_post2020_ha")
    row["perte_post2020_pct"] = e.get("loss_post2020_pct")
    row["sources_perte"] = ", ".join(SOURCE_LABELS.get(s, s) for s in c.get("loss_sources") or [])
    for s, v in (e.get("loss_post2020") or {}).items():
        row[f"perte_{s}_ha"] = v.get("ha")
        row[f"perte_{s}_annees"] = _years(v)
    row["perturbation_avant_2020_pct"] = c.get("predisturbance_pct")
    row["culture_2020_pct"] = c.get("commodity2020_pct")
    row["usage_agricole_avant_2021"] = ("OUI" if c.get("agri_before_cutoff") else "NON") if c else ""
    row["aires_protegees"] = " ; ".join(f"{z.get('name')} ({z.get('kind')}, {fmt_num(z.get('pct'), 1)} %)"
                                        for z in ctx.get("protected") or [])
    row["categorie_fonciere"] = rdue.get("category", "")
    row["zone_fonciere"] = rdue.get("zone", "")
    row["legalite_fonciere"] = rdue.get("legalite", "")
    row["justification"] = (c.get("justification") or {}).get("fr", "")
    row["whisp_risque"] = r.get("whisp_risk") or ""
    row["whisp_concordance"] = CONCORDANCE_LABELS.get(r.get("whisp_concordance") or "non_calcule", "")
    row["whisp_logique_sur_nos_donnees"] = c.get("whisp_like", "")
    row["decision_humaine"] = dec.get("decision_label") or DECISIONS.get(dec.get("decision"), "")
    row["decision_commentaire"] = dec.get("comment", "")
    row["decision_auteur"] = dec.get("author_name", "")
    row["decision_date"] = fmt_date(dec.get("created_at"))
    return row


# ── Export TRACES (format de l'UE : FeatureCollection WGS84, 6 décimales, sans trou) ───────────────

def _clean_ring(ring):
    out = []
    for c in ring:                      # [x, y] ou [x, y, z] : TRACES n'accepte que x, y
        q = [round(float(c[0]), TRACES_DECIMALS), round(float(c[1]), TRACES_DECIMALS)]
        if not out or out[-1] != q:
            out.append(q)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    if len(out) < 3:
        return None
    return out + [list(out[0])]


def _traces_geometry(geom):
    """(géométrie TRACES, motif d'exclusion)."""
    kind = (geom or {}).get("type")
    if kind == "Point":
        x, y = geom["coordinates"][:2]
        return {"type": "Point", "coordinates": [round(x, TRACES_DECIMALS), round(y, TRACES_DECIMALS)]}, None
    if kind not in ("Polygon", "MultiPolygon"):
        return None, "Géométrie absente ou non surfacique"
    polys = [geom["coordinates"]] if kind == "Polygon" else geom["coordinates"]
    holes = sum(max(0, len(p) - 1) for p in polys)
    if holes:
        return None, f"{holes} trou(s) : refusé par TRACES (découpez la parcelle)"
    rings = [r for r in (_clean_ring(p[0]) for p in polys) if r]
    if not rings:
        return None, "Polygone trop petit (moins de 3 sommets distincts après arrondi)"
    if len(rings) == 1:
        return {"type": "Polygon", "coordinates": [rings[0]]}, None
    return {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}, None


def traces_collection(items, country):
    """
    items : [{"id", "geometry", "producer", "place", "area_ha"}] (parcelles retenues).
    Renvoie {"collection": FeatureCollection, "kept": n, "excluded": [(identifiant, motif)]}.
    """
    country = (country or "").strip().upper()
    features, excluded = [], []
    for it in items:
        geom, reason = _traces_geometry(it.get("geometry"))
        if geom is None:
            excluded.append((it.get("id"), reason))
            continue
        props = {"ProducerCountry": country, "Area": round(float(it.get("area_ha") or 0), 4)}
        if (it.get("producer") or "").strip():
            props["ProducerName"] = it["producer"].strip()[:TRACES_MAX_TEXT]
        if (it.get("place") or "").strip():
            props["ProductionPlace"] = it["place"].strip()[:TRACES_MAX_TEXT]
        features.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"collection": {"type": "FeatureCollection", "features": features}, "kept": len(features),
            "excluded": excluded}
