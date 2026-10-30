"""
Étapes 2 et 3 — preuves par source, calculées sur une GRILLE COMMUNE (≈ 10 m) propre à chaque parcelle.

Chaque lecteur (rasterio, GDAL, ou tableaux synthétiques des tests) fournit, pour une parcelle :
  mask        tableau booléen : pixels de la grille dans la parcelle
  pixel_ha    surface de chaque pixel en ha (tableau de même forme, ou colonne : la surface varie avec la latitude)
  layers      dictionnaire des couches rééchantillonnées (plus proche voisin) sur la grille ; une couche absente = source
              non disponible (elle ne vote pas, elle n'est pas comptée)

Couches reconnues (encodage d'origine des producteurs, voir catalog.py) :
  jrc_gfc2020          1 = forêt 2020
  tmf_annual_2020      AnnualChange Dec2020 : 1 = forêt humide non perturbée
  tmf_deforestation    DeforestationYear : année (0 = aucune)
  tmf_degradation      DegradationYear : année (0 = aucune)
  worldcover2020       classes ESA (10 = arbres, 95 = mangroves)
  hansen_treecover     treecover2000 (0-100)
  hansen_lossyear      0 = aucune perte, 1..25 = 2001..2025
  radd_year, glad_l_year   année de l'alerte (0 = aucune) — optionnelles (Earth Engine)
  cocoa_prob, palm_prob, rubber_prob   probabilité 0-1 en 2020 — optionnelles (Earth Engine)

Aucune dépendance autre que numpy.
"""
import numpy as np

CUTOFF_YEAR = 2020


def _area(mask_like, pixel_ha):
    return float((np.broadcast_to(pixel_ha, mask_like.shape) * mask_like).sum())


def forest_masks(layers, t):
    """Masques « forêt au 31/12/2020 » par source (règles WHISP)."""
    out = {}
    if layers.get("jrc_gfc2020") is not None:
        out["jrc_gfc2020"] = layers["jrc_gfc2020"] == 1
    if layers.get("tmf_annual_2020") is not None:
        out["tmf"] = layers["tmf_annual_2020"] == 1
    if layers.get("worldcover2020") is not None:
        wc = layers["worldcover2020"]
        out["worldcover2020"] = (wc == 10) | (wc == 95)
    if layers.get("hansen_treecover") is not None and layers.get("hansen_lossyear") is not None:
        ly = layers["hansen_lossyear"]
        lost_before = (ly > 0) & (ly <= CUTOFF_YEAR - 2000)
        out["hansen"] = (layers["hansen_treecover"] > t["hansen_treecover_min"]) & ~lost_before
    return out


def loss_layers(layers):
    """Pertes APRÈS le 31/12/2020 par source : (masque, années)."""
    out = {}
    if layers.get("hansen_lossyear") is not None:
        ly = layers["hansen_lossyear"].astype(np.int32)
        years = np.where(ly > 0, ly + 2000, 0)
        out["hansen"] = (years > CUTOFF_YEAR, years)
    for key, name in (("tmf_deforestation", "tmf_deforestation"), ("tmf_degradation", "tmf_degradation"),
                      ("radd_year", "radd"), ("glad_l_year", "glad_l")):
        if layers.get(key) is not None:
            y = layers[key].astype(np.int32)
            out[name] = (y > CUTOFF_YEAR, y)
    return out


def predisturbance_masks(layers):
    """Perturbations AVANT le 31/12/2020 (WHISP ind_3 : déforestation, dégradation, perte d'arbres)."""
    out = {}
    if layers.get("hansen_lossyear") is not None:
        ly = layers["hansen_lossyear"]
        out["hansen"] = (ly > 0) & (ly <= CUTOFF_YEAR - 2000)
    for key, name in (("tmf_deforestation", "tmf_deforestation"), ("tmf_degradation", "tmf_degradation")):
        if layers.get(key) is not None:
            y = layers[key]
            out[name] = (y > 0) & (y <= CUTOFF_YEAR)
    return out


def compute_evidence(layers, mask, pixel_ha, t, area_ha=None):
    """
    Preuves d'une parcelle. `area_ha` : surface géodésique réelle (les parts mesurées sur les pixels y sont rapportées).
    Renvoie un dictionnaire sérialisable (JSON) — aucun classement ici.
    """
    mask = np.asarray(mask, dtype=bool)
    mask_ha = _area(mask, pixel_ha)
    if mask_ha <= 0:
        return {"error": "Parcelle hors de la grille d'analyse (aucun pixel)."}
    area = float(area_ha) if area_ha else mask_ha
    share = lambda m: _area(m & mask, pixel_ha) / mask_ha   # noqa: E731 — part de la parcelle (0-1)

    # Étape 2 — forêt au 31/12/2020, par source
    fm = forest_masks(layers, t)
    forest = {src: {"pct": round(100 * share(m), 2), "ha": round(share(m) * area, 4)} for src, m in fm.items()}
    forest_union = np.zeros(mask.shape, dtype=bool)
    for m in fm.values():
        forest_union |= m

    # Étape 3 — pertes après 2020, UNIQUEMENT dans les pixels forêt 2020 (union des sources de forêt)
    loss = {}
    union_loss = np.zeros(mask.shape, dtype=bool)
    for src, (m, years) in loss_layers(layers).items():
        inside = m & mask & forest_union
        ys = np.unique(years[inside]) if inside.any() else np.array([], dtype=np.int32)
        ys = [int(y) for y in ys if y > CUTOFF_YEAR]
        # perte hors forêt 2020 : mesurée pour information, jamais comptée
        outside_ha = round(share(m & ~forest_union) * area, 4)
        loss[src] = {"ha": round(share(inside) * area, 4), "pct": round(100 * share(inside), 2),
                     "first_year": ys[0] if ys else None, "last_year": ys[-1] if ys else None,
                     "outside_forest_ha": outside_ha}
        union_loss |= inside
    union_share = share(union_loss)

    # Perturbations avant 2020 (pour la règle WHISP)
    pre = {src: round(100 * share(m), 2) for src, m in predisturbance_masks(layers).items()}

    # Cultures en 2020 (probabilités > 0,5, règle WHISP) — disponibles seulement avec Earth Engine
    commodity = {}
    for key, name in (("cocoa_prob", "cacao"), ("palm_prob", "palmier"), ("rubber_prob", "hevea")):
        if layers.get(key) is not None:
            p = layers[key]
            commodity[name] = {"pct": round(100 * share(p > 0.5), 2),
                               "mean_prob": round(float(np.nanmean(np.where(mask, p, np.nan))), 3)}

    return {
        "area_ha": round(area, 4),
        "pixels": int(mask.sum()),
        "forest2020": forest,
        "forest2020_union_pct": round(100 * share(forest_union), 2),
        "loss_post2020": loss,
        "loss_post2020_ha": round(union_share * area, 4),
        "loss_post2020_pct": round(100 * union_share, 2),
        "predisturbance_pct": pre,
        "commodity2020": commodity,
    }


def pixel_area_ha(lat_centers, res_deg):
    """Surface (ha) d'un pixel de `res_deg` × `res_deg` degrés selon la latitude (colonne à diffuser)."""
    r = 6371007.2
    lat = np.radians(np.asarray(lat_centers, dtype=np.float64))
    d = np.radians(res_deg)
    a = (r * r * d * (np.sin(lat + d / 2) - np.sin(lat - d / 2))) / 10000.0
    return a.reshape(-1, 1)
