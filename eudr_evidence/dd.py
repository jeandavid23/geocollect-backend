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

# ── Explications du classement (identiques dans tous les outils) ──────────────────────────────────────

METHOD_STEPS = [
    {"title": "1. Contrôle de la parcelle",
     "text": "Le contour est vérifié : coordonnées GPS valides, polygone qui ne se croise pas lui-même, polygone "
             "obligatoire au-delà de 4 ha. Une parcelle qui échoue n'est pas analysée : elle apparaît comme rejetée, "
             "avec le motif."},
    {"title": "2. Y avait-il une forêt au 31/12/2020 ?",
     "text": "Quatre cartes satellites indépendantes « votent » : JRC 2020 (la carte de référence de l'Union "
             "européenne), TMF, WorldCover et Hansen. Une carte vote « forêt » si elle voit de la forêt sur au moins "
             "la part minimale de la parcelle. Deux votes ou plus : forêt. Un seul vote, ou un vote de la carte JRC : "
             "incertain. Aucun vote : pas de forêt."},
    {"title": "3. Des arbres ont-ils disparu après 2020 ?",
     "text": "Hansen et TMF indiquent, pixel par pixel, l'année de perte du couvert. Seules les pertes situées là "
             "où il y avait une forêt en 2020 sont comptées, et une même perte vue par deux cartes n'est comptée "
             "qu'une fois."},
    {"title": "4. Le contexte",
     "text": "La parcelle est croisée avec les aires protégées (WDPA, KBA) et le domaine foncier national (forêts "
             "classées, parcs, enclaves). Un usage agricole établi avant 2021 (culture déjà présente, défrichement "
             "ancien) est un argument pour un risque faible."},
    {"title": "5. Le classement",
     "text": "Chaque parcelle reçoit un risque (faible, à vérifier, élevé), un score de convergence de 0 à 100 (plus "
             "il est haut, plus les preuves d'un risque concordent) et une phrase qui explique pourquoi. Les cartes "
             "sont des aides à la décision : une parcelle « à vérifier » ou « élevé » doit être contrôlée par une "
             "personne avant toute déclaration."},
]

RISK_MEANING = {
    "faible": "Aucune preuve de déforestation après 2020 : la parcelle peut entrer dans la déclaration.",
    "a_verifier": "Les sources ne concordent pas ou une preuve est isolée : une vérification humaine est nécessaire "
                  "avant de déclarer la parcelle.",
    "eleve": "Plusieurs preuves concordent (perte de forêt après 2020, aire protégée ou zone interdite) : la parcelle "
             "ne doit pas être déclarée sans preuve contraire solide.",
}

REASON_EXPLAIN = {
    "rdue_illegal": "La parcelle est dans une forêt classée ou un parc selon la base foncière nationale (matrice "
                    "RDUE) : la production y est illégale, quel que soit l'état de la forêt.",
    "protected_area": "Une part importante de la parcelle (au-delà du seuil) se trouve dans une aire protégée "
                      "(WDPA ou KBA).",
    "forest_loss_convergent": "Les cartes s'accordent sur une forêt en 2020, et au moins deux sources indépendantes "
                              "détectent une perte de couvert après 2020, au-delà du seuil.",
    "single_source_loss": "Une seule source détecte une perte après 2020 : la preuve est isolée, elle peut venir "
                          "d'un nuage, d'une ombre ou d'une erreur de carte.",
    "forest_uncertain": "Une seule carte voit de la forêt en 2020 (ou la carte de l'UE en voit) : on ne peut pas "
                        "conclure sans vérification.",
    "protected_area_partial": "Le contour de la parcelle touche une aire protégée sur une faible part : souvent une "
                              "imprécision du relevé GPS, à vérifier.",
    "forest_loss_below_threshold": "Plusieurs sources voient une perte après 2020, mais trop petite pour dépasser "
                                   "le seuil.",
    "forest_no_agri_evidence": "Les cartes voient de la forêt en 2020 et aucune perte ensuite, mais rien ne prouve "
                               "un usage agricole avant 2021. C'est typique du cacao sous ombrage, que les satellites "
                               "confondent avec la forêt (WHISP : « plus d'informations nécessaires »).",
    "no_forest_2020": "Aucune des cartes ne voit de forêt sur la parcelle au 31/12/2020 : la règle « zéro "
                      "déforestation » est respectée.",
    "agri_before_cutoff": "Un usage agricole est établi avant 2021 (culture déjà présente ou défrichement ancien) "
                          "sur la majorité de la parcelle.",
    "forest_no_loss": "Forêt en 2020 selon les cartes, sans aucune perte détectée ensuite.",
    "no_data": "La parcelle est trop petite ou hors de la zone couverte par les cartes : aucun pixel n'a pu être lu.",
}

REASON_ACTIONS = {
    "rdue_illegal": "Exclure la parcelle du lot, ou fournir le titre ou la dérogation qui autorise la production.",
    "protected_area": "Exclure la parcelle du lot, ou fournir l'autorisation de l'autorité de gestion de l'aire "
                      "protégée.",
    "forest_loss_convergent": "Visite terrain et comparaison des images avant / après 2020 ; exclure si la perte est "
                              "confirmée.",
    "single_source_loss": "Contrôle sur les images Sentinel-2 avant / après 2020 ; visite terrain en cas de doute.",
    "forest_uncertain": "Prouver l'usage agricole avant 2021 (photos datées, attestation, âge des plants).",
    "forest_no_agri_evidence": "Prouver l'usage agricole avant 2021 (photos datées, attestation, âge des plants).",
    "protected_area_partial": "Vérifier le contour de la parcelle et la limite de l'aire protégée (relevé GPS).",
    "forest_loss_below_threshold": "Contrôle sur les images Sentinel-2 avant / après 2020.",
    "no_forest_2020": "Aucune action : parcelle déclarable.",
    "agri_before_cutoff": "Aucune action : parcelle déclarable (garder les preuves d'usage agricole au dossier).",
    "forest_no_loss": "Aucune action si la logique WHISP est désactivée ; sinon prouver l'usage agricole avant 2021.",
    "no_data": "Vérifier la géométrie de la parcelle (trop petite ou hors de la zone couverte).",
}

DECISIONS = {
    "confirmer": "Risque confirmé", "lever": "Risque levé", "visite": "Visite terrain demandée",
    "documents": "Documents demandés", "exclure": "Parcelle exclue du lot",
}
CONCORDANCE_LABELS = {"concordant": "Concordant", "plus_strict": "Plus strict que WHISP",
                      "moins_strict": "Moins strict que WHISP", "non_calcule": "Non calculé"}
# Charte GeoCollect : remplissages et badges ; différents aussi par la clarté (daltonisme, noir et blanc)
RISK_COLORS = {"faible": "#2c6741", "a_verifier": "#e08a1e", "eleve": "#9b1c1c"}
# Couleur de risque employée comme TEXTE sur fond blanc (contraste ≥ 4,5:1 ; l'orange seul est trop pâle)
RISK_TEXT_COLORS = {"faible": "#2c6741", "a_verifier": "#9a520c", "eleve": "#9b1c1c"}
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
