"""
Seuils de l'analyse et paramètres réglementaires — un seul endroit, affichés dans le rapport, modifiables par
l'utilisateur (réglages du plugin, réglages de la coopérative dans GeoCollect).
"""
import copy

# ── Seuils par défaut ─────────────────────────────────────────────────────────────────────────────
DEFAULTS = {
    # Étape 2 — forêt au 31/12/2020
    "min_forest_share": 10.0,       # % de la parcelle classé forêt par une source pour qu'elle « vote » forêt (FAO : couvert > 10 %)
    "min_votes": 2,                 # votes nécessaires pour « oui » (sur les sources disponibles) ; 1 vote = « incertain »
    "hansen_treecover_min": 10.0,   # couvert arboré Hansen 2000 strictement supérieur à ce seuil (règle WHISP « GFC_TC_2020 »)
    # Étape 3 — perte après 2020 (comptée uniquement dans les pixels forêt 2020)
    "min_loss_ha": 0.05,            # une source « signale une perte » au-delà de cette surface…
    "min_loss_pct": 1.0,            # … ou de cette part de la parcelle (%)
    # Étape 4 — usage du sol et contexte (logique WHISP pour les cultures pérennes)
    "align_whisp": True,            # culture en 2020 ou perturbation avant 2020 sur la majorité de la parcelle → risque faible ;
                                    # forêt 2020 sans perte détectée → « à vérifier » (WHISP : more_info_needed)
    "commodity_share_low": 50.0,    # % de la parcelle couvert par une culture en 2020 (WHISP ind_2 : 50 %)
    "predisturbance_share_low": 50.0,  # % perturbé avant 2020 (WHISP ind_3 : 50 %)
    "protected_high_pct": 10.0,     # % de la parcelle dans une aire protégée au-delà duquel le risque est élevé
    # Points (parcelles < 4 ha déclarées par un point)
    "point_default_radius_m": 50.0,
    # Correspondance indicative avec WHISP (indicateurs 1 et 4 : 10 %)
    "whisp_treecover_pct": 10.0,
    "whisp_disturbance_after_pct": 10.0,
}

# ── Paramètres réglementaires (paramètres, jamais codés en dur ailleurs) ────────────────────────────
REGULATION = {
    "regulation": "Règlement (UE) 2023/1115 (EUDR)",
    "cutoff_date": "2020-12-31",
    "retention_years": 5,
    "point_max_area_ha": 4.0,       # au-delà : polygone obligatoire (art. 9)
    "coordinate_decimals": 6,
    # Classement des pays par l'UE (règlement d'exécution) — à mettre à jour quand l'UE le révise
    "country_risk": {"CI": "standard", "GH": "standard", "CM": "standard", "NG": "standard", "TG": "standard",
                     "LR": "standard", "GN": "standard", "SL": "standard"},
    # Calendrier d'application (modifiable)
    "application_dates": {"grands_operateurs": "2026-12-30", "micro_petites_entreprises": "2027-06-30"},
    "hs_codes": {"cacao": "1801", "cafe": "0901"},
}

RISK_LEVELS = ["faible", "a_verifier", "eleve"]
RISK_LABELS = {"fr": {"faible": "Faible", "a_verifier": "À vérifier", "eleve": "Élevé"},
               "en": {"faible": "Low", "a_verifier": "To be verified", "eleve": "High"}}
FOREST_LABELS = {"fr": {"oui": "Forêt", "incertain": "Incertain", "non": "Non forêt"},
                 "en": {"oui": "Forest", "incertain": "Uncertain", "non": "Non-forest"}}


def thresholds(overrides=None):
    """Seuils effectifs : valeurs par défaut + réglages de l'utilisateur (clés inconnues ignorées)."""
    t = copy.deepcopy(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k in t and v is not None:
            t[k] = type(t[k])(v) if not isinstance(t[k], bool) else bool(v)
    return t


def country_risk(iso2):
    return REGULATION["country_risk"].get((iso2 or "").upper(), "non classé")
