"""
Étapes 4 et 5 — contexte (cultures, aires protégées, matrice RDUE nationale) et classement du risque.

Classement par défaut aligné sur la logique WHISP « cultures pérennes » (cacao, café), complétée par le vote entre
sources, la concordance des pertes, les aires protégées et la matrice RDUE (réglage `align_whisp`) :

  ÉLEVÉ       parcelle illégale selon la matrice RDUE (forêt classée, parc / réserve), OU ≥ `protected_high_pct` %
              dans une aire protégée (WDPA / KBA), OU forêt 2020 (« oui ») + perte après 2020 confirmée par
              au moins DEUX sources (au-delà de `min_loss_ha` ou `min_loss_pct`), sans indice d'usage agricole antérieur.
  À VÉRIFIER  forêt 2020 « incertaine » (une seule source), OU une seule source signale une perte après 2020, OU
              intersection avec une aire protégée sous le seuil, OU (align_whisp) forêt 2020 sans perte détectée et sans
              indice d'usage agricole (WHISP : « more_info_needed »).
  FAIBLE      pas de forêt en 2020, OU culture en 2020 / perturbation avant 2020 sur la majorité de la parcelle
              (WHISP : usage agricole établi avant la date butoir), OU forêt 2020 sans perte (si align_whisp désactivé),
              et aucune aire protégée ni illégalité.
"""
from .catalog import FOREST_LABELS, LOSS_LABELS, FOREST_SOURCES

ORDER = {"faible": 0, "a_verifier": 1, "eleve": 2}
WHISP_TO_OURS = {"low": "faible", "more_info_needed": "a_verifier", "high": "eleve"}


def forest_vote(evidence, t):
    """Étape 2 : votes des sources disponibles ; JRC GFC2020 prioritaire (forêt JRC → au moins « incertain »)."""
    f = evidence.get("forest2020", {})
    voters = [s for s in FOREST_SOURCES if s in f and f[s]["pct"] >= t["min_forest_share"]]
    n = len(voters)
    if n >= t["min_votes"]:
        state = "oui"
    elif n == 1:
        state = "incertain"
    else:
        state = "non"
    if "jrc_gfc2020" in voters and state == "non":
        state = "incertain"
    return state, voters, [s for s in FOREST_SOURCES if s in f]


def _fmt_ha(x):
    return f"{x:,.2f}".replace(",", " ").replace(".", ",") + " ha"


def classify(evidence, context=None, t=None):
    """
    evidence : sortie de evidence.compute_evidence
    context  : {"protected": [{"name", "kind", "pct", "ha"}], "rdue": {"category", "legalite", "zone"}}
    t        : seuils effectifs (config.thresholds)
    """
    from .config import thresholds
    t = t or thresholds()
    context = context or {}
    if evidence.get("error"):
        return {"risk": "a_verifier", "score": 0, "forest2020": "non", "reasons": ["no_data"],
                "justification": {"fr": evidence["error"], "en": "Plot outside the analysis grid (no pixel)."}}

    state, voters, available = forest_vote(evidence, t)
    loss = evidence.get("loss_post2020", {})
    loss_sources = [s for s, v in loss.items() if v["ha"] >= t["min_loss_ha"] or v["pct"] >= t["min_loss_pct"]]
    union_ok = evidence["loss_post2020_ha"] >= t["min_loss_ha"] or evidence["loss_post2020_pct"] >= t["min_loss_pct"]
    commodity = max([c["pct"] for c in evidence.get("commodity2020", {}).values()] or [0.0])
    predist = max(list(evidence.get("predisturbance_pct", {}).values()) or [0.0])
    agri_before = t["align_whisp"] and (commodity > t["commodity_share_low"] or predist > t["predisturbance_share_low"])

    protected = context.get("protected") or []
    pa_pct = max([p.get("pct", 0) for p in protected] or [0.0])
    rdue = context.get("rdue") or {}
    illegal = rdue.get("legalite") == "Non conforme"

    reasons = []
    if illegal:
        risk = "eleve"; reasons.append("rdue_illegal")
    elif pa_pct >= t["protected_high_pct"]:
        risk = "eleve"; reasons.append("protected_area")
    elif state == "oui" and not agri_before and len(loss_sources) >= 2 and union_ok:
        risk = "eleve"; reasons.append("forest_loss_convergent")
    elif state != "non" and not agri_before and len(loss_sources) == 1:
        risk = "a_verifier"; reasons.append("single_source_loss")
    elif state == "incertain" and not agri_before:
        risk = "a_verifier"; reasons.append("forest_uncertain")
    elif pa_pct > 0:
        risk = "a_verifier"; reasons.append("protected_area_partial")
    elif state == "oui" and not agri_before and len(loss_sources) >= 2:
        risk = "a_verifier"; reasons.append("forest_loss_below_threshold")
    elif state == "oui" and not agri_before and t["align_whisp"]:
        risk = "a_verifier"; reasons.append("forest_no_agri_evidence")
    else:
        risk = "faible"
        reasons.append("no_forest_2020" if state == "non" else ("agri_before_cutoff" if agri_before else "forest_no_loss"))

    # Score de convergence (0-100) : force des preuves en faveur d'un risque de déforestation
    f_strength = (len(voters) / len(available)) if available else 0.0
    if "jrc_gfc2020" in voters:
        f_strength = min(1.0, f_strength + 0.15)
    l_strength = min(1.0, len(loss_sources) / 2.0) if state != "non" else 0.0
    ctx = 1.0 if (illegal or pa_pct >= t["protected_high_pct"]) else (0.5 if pa_pct > 0 else 0.0)
    base = 0.45 * f_strength + 0.55 * l_strength
    if agri_before:
        base *= 0.5
    score = int(round(100 * max(ctx, base)))

    return {
        "risk": risk, "score": score, "reasons": reasons,
        "forest2020": state, "forest2020_votes": len(voters), "forest2020_sources": voters,
        "forest2020_available": available,
        "loss_sources": loss_sources, "loss_post2020_ha": evidence["loss_post2020_ha"],
        "agri_before_cutoff": bool(agri_before), "commodity2020_pct": commodity, "predisturbance_pct": predist,
        "protected_max_pct": pa_pct, "rdue_illegal": illegal,
        "whisp_like": whisp_like(evidence, t),
        "justification": justification(state, voters, loss_sources, loss, evidence, protected, rdue, agri_before,
                                       commodity, predist),
    }


def whisp_like(evidence, t):
    """Réponse que donnerait la logique WHISP « cultures pérennes » sur NOS données (indicateurs 1 à 4)."""
    f = evidence.get("forest2020", {})
    ind1 = any(v["pct"] > t["whisp_treecover_pct"] for v in f.values())
    ind2 = any(c["pct"] > t["commodity_share_low"] for c in evidence.get("commodity2020", {}).values())
    ind3 = any(v > t["predisturbance_share_low"] for v in evidence.get("predisturbance_pct", {}).values())
    ind4 = any((v["pct"] + 100 * v["outside_forest_ha"] / evidence["area_ha"] if evidence["area_ha"] else v["pct"])
               > t["whisp_disturbance_after_pct"] for v in evidence.get("loss_post2020", {}).values())
    if not ind1 or ind2 or ind3:
        return "low"
    return "high" if ind4 else "more_info_needed"


def justification(state, voters, loss_sources, loss, evidence, protected, rdue, agri_before, commodity, predist):
    """Phrase courte, lisible par un auditeur, en français et en anglais."""
    fr, en = [], []
    if voters:
        names = " et ".join(FOREST_LABELS[s] for s in voters)
        fr.append(f"Forêt 2020 selon {names}")
        en.append(f"Forest in 2020 according to {names.replace(' et ', ' and ')}")
    else:
        fr.append("Pas de forêt en 2020 selon les sources disponibles")
        en.append("No forest in 2020 according to available sources")
    if loss_sources:
        years = sorted({y for s in loss_sources for y in (loss[s]["first_year"], loss[s]["last_year"]) if y})
        when = (f" en {years[0]}" if len(years) == 1 else f" de {years[0]} à {years[-1]}") if years else ""
        when_en = (f" in {years[0]}" if len(years) == 1 else f" from {years[0]} to {years[-1]}") if years else ""
        names = " et ".join(LOSS_LABELS[s] for s in loss_sources)
        fr.append(f"perte {_fmt_ha(evidence['loss_post2020_ha'])}{when} selon {names}")
        en.append(f"loss of {evidence['loss_post2020_ha']:.2f} ha{when_en} according to {names.replace(' et ', ' and ')}")
    elif state != "non":
        fr.append("aucune perte après 2020 détectée")
        en.append("no loss detected after 2020")
    if agri_before:
        if commodity > predist:
            fr.append(f"culture en 2020 sur {commodity:.0f} % de la parcelle (usage agricole établi avant la date butoir)")
            en.append(f"crop in 2020 on {commodity:.0f}% of the plot (agricultural use before the cut-off date)")
        else:
            fr.append(f"perturbation avant 2020 sur {predist:.0f} % de la parcelle (usage agricole probable avant la date butoir)")
            en.append(f"disturbance before 2020 on {predist:.0f}% of the plot (likely agricultural use before the cut-off date)")
    for p in protected[:2]:
        fr.append(f"{p.get('pct', 0):.0f} % dans l'aire protégée « {p.get('name', '?')} » ({p.get('kind', '')})")
        en.append(f"{p.get('pct', 0):.0f}% inside protected area “{p.get('name', '?')}” ({p.get('kind', '')})")
    if rdue.get("legalite") == "Non conforme":
        fr.append(f"dans le domaine forestier classé : {rdue.get('zone') or rdue.get('category')}")
        en.append(f"inside the classified forest estate: {rdue.get('zone') or rdue.get('category')}")
    return {"fr": " ; ".join(fr) + ".", "en": "; ".join(en) + "."}


def concordance(ours, whisp_risk):
    """Comparaison avec WHISP : concordant | plus_strict | moins_strict | non_calcule."""
    w = WHISP_TO_OURS.get((whisp_risk or "").strip().lower())
    if not w or ours not in ORDER:
        return "non_calcule"
    if ORDER[ours] == ORDER[w]:
        return "concordant"
    return "plus_strict" if ORDER[ours] > ORDER[w] else "moins_strict"


def summarize(results):
    """Synthèse d'un lot : répartition, surfaces, concordance WHISP, conclusion (risque négligeable ou non)."""
    counts = {k: 0 for k in ORDER}
    area = {k: 0.0 for k in ORDER}
    conc = {"concordant": 0, "plus_strict": 0, "moins_strict": 0, "non_calcule": 0}
    rejected = 0
    for r in results:
        if r.get("rejected"):
            rejected += 1
            continue
        c = r.get("classification") or {}
        k = c.get("risk")
        if k in counts:
            counts[k] += 1
            area[k] += float((r.get("evidence") or {}).get("area_ha") or 0)
        conc[r.get("whisp_concordance") or "non_calcule"] += 1
    analysed = sum(counts.values())
    computed = analysed - conc["non_calcule"]
    negligible = analysed > 0 and counts["eleve"] == 0 and counts["a_verifier"] == 0
    return {
        "analysed": analysed, "rejected": rejected, "counts": counts,
        "area_ha": {k: round(v, 2) for k, v in area.items()}, "total_area_ha": round(sum(area.values()), 2),
        "whisp_concordance": conc,
        "whisp_concordance_rate": round(100 * conc["concordant"] / computed, 1) if computed else None,
        "conclusion": "negligeable" if negligible else "non_negligeable",
    }
