"""
Catalogue unique des jeux de données de la convergence de preuves.

Chaque identifiant a été VÉRIFIÉ à la source indiquée dans `verified` (date de vérification : 2026-10-24) :
  - identifiants Earth Engine : code source de WHISP (github.com/forestdatapartnership/whisp, branche main,
    fichier src/openforis_whisp/datasets.py) et pages du catalogue Earth Engine ;
  - accès hors Earth Engine : requêtes HTTP réelles (code 200, taille du fichier).
Un champ à None signifie « pas d'accès de ce type » ; `todo` signale ce qui reste à confirmer.
Ne jamais écrire un identifiant ici sans l'avoir vérifié.
"""

VERIFIED_ON = "2026-10-24"
WHISP_SOURCE = "https://github.com/forestdatapartnership/whisp/blob/main/src/openforis_whisp/datasets.py"

HANSEN_VERSION = "GFC-2025-v1.13"
HANSEN_HTTP = ("https://storage.googleapis.com/earthenginepartners-hansen/" + HANSEN_VERSION
               + "/Hansen_" + HANSEN_VERSION + "_{layer}_{tile}.tif")
JRC_GFC2020_COG = ("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/FOREST/GFC2020/LATEST/single-cog/"
                   "JRC_GFC2020_V4_COG.tif")
WORLDCOVER_2020_HTTP = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v100/2020/map/"
                        "ESA_WorldCover_10m_2020_v100_{tile}_Map.tif")
TMF_HTTP = "https://ies-ows.jrc.ec.europa.eu/iforce/tmf_v1/download.py?type=tile&dataset={dataset}&lat={lat}&lon={lon}"
WDPA_HTTP = "https://d1gam3xoknrgr2.cloudfront.net/current/WDPA_WDOECM_{month}_Public_{iso3}_shp.zip"

# rôle : forest2020 | loss_post2020 | disturbance_pre2020 | alerts | commodity | protected | national | aggregator
SOURCES = {
    "jrc_gfc2020": {
        "name": "JRC Global Forest Cover 2020",
        "producer": "Commission européenne — Centre commun de recherche (JRC), Observatoire UE de la déforestation",
        "resolution": "10 m", "period": "31/12/2020", "version": "V4",
        "licence": "CC BY 4.0",
        "citation": "European Commission, Joint Research Centre (2026). Global Forest Cover 2020, version 4.",
        "roles": ["forest2020"], "priority": True,
        "encoding": "Map = 1 : forêt au 31/12/2020",
        "gee": 'ee.Image("JRC/GFC2020/V4").select("Map")',
        "http": JRC_GFC2020_COG, "http_kind": "cog",
        "verified": [WHISP_SOURCE, "https://developers.google.com/earth-engine/datasets/catalog/JRC_GFC2020_V4",
                     JRC_GFC2020_COG + " (HTTP 200, 40,5 Go, lecture par fenêtre)"],
    },
    "tmf": {
        "name": "JRC Tropical Moist Forest (TMF)",
        "producer": "JRC (Vancutsem et al.)",
        "resolution": "30 m", "period": "1990 → 2025", "version": "v1_2025 (Earth Engine) / tmf_v1 (téléchargement)",
        "licence": "Réutilisation autorisée avec mention de la source (politique de données ouvertes du JRC)",
        "citation": "Vancutsem C., Achard F., Pekel J.-F. et al. (2021). Long-term (1990–2019) monitoring of forest cover "
                    "changes in the humid tropics. Science Advances 7(10): eabe1603.",
        "roles": ["forest2020", "loss_post2020", "disturbance_pre2020"],
        "encoding": "AnnualChange Dec2020 = 1 : forêt humide non perturbée (règle WHISP « TMF_undist ») ; "
                    "DeforestationYear / DegradationYear = année de la perturbation (0 = aucune)",
        "gee": {"annual_change": 'ee.ImageCollection("projects/JRC/TMF/v1_2025/AnnualChanges") bande Dec2020',
                "deforestation_year": 'ee.ImageCollection("projects/JRC/TMF/v1_2025/DeforestationYear")',
                "degradation_year": 'ee.ImageCollection("projects/JRC/TMF/v1_2025/DegradationYear")'},
        "http": TMF_HTTP, "http_kind": "tile_download",
        "http_datasets": {"annual_change": "AnnualChange_2020", "deforestation_year": "DeforestationYear",
                          "degradation_year": "DegradationYear"},
        "verified": [WHISP_SOURCE, "tuiles 10° (HTTP 200, 65 à 115 Mo, PAS de lecture partielle : téléchargement + cache)"],
    },
    "worldcover2020": {
        "name": "ESA WorldCover 2020",
        "producer": "Agence spatiale européenne (ESA), Copernicus",
        "resolution": "10 m", "period": "2020", "version": "v100",
        "licence": "CC BY 4.0",
        "citation": "Zanaga D. et al. (2021). ESA WorldCover 10 m 2020 v100. doi:10.5281/zenodo.5571936",
        "roles": ["forest2020"],
        "encoding": "classe 10 (couvert arboré) ou 95 (mangrove) — règle WHISP « ESA_TC_2020 »",
        "gee": 'ee.Image("ESA/WorldCover/v100/2020")',
        "http": WORLDCOVER_2020_HTTP, "http_kind": "cog",
        "verified": [WHISP_SOURCE, "https://developers.google.com/earth-engine/datasets/catalog/ESA_WorldCover_v100",
                     "tuile N06W006 (HTTP 200)"],
    },
    "hansen": {
        "name": "Hansen Global Forest Change",
        "producer": "Université du Maryland (UMD) / Google",
        "resolution": "30 m", "period": "2000 → 2025", "version": HANSEN_VERSION,
        "licence": "CC BY 4.0",
        "citation": "Hansen M. C. et al. (2013). High-Resolution Global Maps of 21st-Century Forest Cover Change. "
                    "Science 342: 850–853. Données " + HANSEN_VERSION + ".",
        "roles": ["forest2020", "loss_post2020", "disturbance_pre2020"],
        "encoding": "forêt 2020 = treecover2000 > seuil (10 %) sans perte 2001-2020 (règle WHISP « GFC_TC_2020 ») ; "
                    "lossyear 21 à 25 = perte 2021 à 2025",
        "gee": 'ee.Image("UMD/hansen/global_forest_change_2025_v1_13")',
        "http": HANSEN_HTTP, "http_kind": "cog",
        "verified": [WHISP_SOURCE,
                     "https://developers.google.com/earth-engine/datasets/catalog/UMD_hansen_global_forest_change_2025_v1_13",
                     "tuiles lossyear / treecover2000 10N_010W (HTTP 200)"],
    },
    "radd": {
        "name": "Alertes RADD (radar Sentinel-1)",
        "producer": "Université de Wageningen / Global Forest Watch",
        "resolution": "10 m", "period": "2019 → aujourd'hui (Afrique)", "version": "v1",
        "licence": "CC BY 4.0",
        "citation": "Reiche J. et al. (2021). Forest disturbance alerts for the Congo Basin using Sentinel-1. "
                    "Environmental Research Letters 16: 024005.",
        "roles": ["alerts", "loss_post2020"],
        "encoding": "bande Date = AAJJJ (année sur 2 chiffres + jour) ; alertes de la couche « alert »",
        "gee": 'ee.ImageCollection("projects/radar-wur/raddalert/v1")',
        "http": None, "http_kind": None,
        "gfw_api": "API Global Forest Watch (clé gratuite) — jeu « wur_radd_alerts »",
        "verified": [WHISP_SOURCE],
        "todo": "nom exact du jeu dans l'API Global Forest Watch à confirmer avec une clé",
    },
    "glad_l": {
        "name": "Alertes GLAD-L (Landsat)",
        "producer": "UMD / GLAD",
        "resolution": "30 m", "period": "2021 → aujourd'hui", "version": "2021final, 2022final, 2023final, UpdResult",
        "licence": "CC BY 4.0",
        "citation": "Hansen M. C. et al. (2016). Humid tropical forest disturbance alerts using Landsat data. "
                    "Environmental Research Letters 11: 034008.",
        "roles": ["alerts", "loss_post2020"],
        "encoding": "confNN = niveau de confiance de l'année 20NN",
        "gee": "projects/glad/alert/2021final, 2022final, 2023final (2023 et 2024), UpdResult (2025+)",
        "http": None, "http_kind": None,
        "verified": [WHISP_SOURCE],
    },
    "fdp_cocoa": {
        "name": "Probabilité de cacao (Forest Data Partnership)",
        "producer": "Forest Data Partnership (Google, WRI, Unilever, FAO…)",
        "resolution": "10 m", "period": "2020 et 2024", "version": "model_2025b",
        "licence": "à confirmer (conditions du Forest Data Partnership)",
        "citation": "Forest Data Partnership (2025). Cocoa probability model 2025b.",
        "roles": ["commodity"],
        "encoding": "probabilité 0-1 ; cacao en 2020 si > 0,5 (règle WHISP « Cocoa_FDaP »)",
        "gee": 'ee.ImageCollection("projects/forestdatapartnership/assets/cocoa/model_2025b") filtré sur 2020',
        "http": None, "http_kind": None,
        "verified": [WHISP_SOURCE],
        "todo": "licence d'usage commercial à confirmer",
    },
    "fdp_palm": {
        "name": "Probabilité de palmier à huile (Forest Data Partnership)", "producer": "Forest Data Partnership",
        "resolution": "10 m", "period": "2020", "version": "model_2025b", "licence": "à confirmer",
        "citation": "Forest Data Partnership (2025). Palm probability model 2025b.", "roles": ["commodity"],
        "encoding": "probabilité 0-1, > 0,5", "gee": 'ee.ImageCollection("projects/forestdatapartnership/assets/palm/model_2025b")',
        "http": None, "http_kind": None, "verified": [WHISP_SOURCE],
    },
    "fdp_rubber": {
        "name": "Probabilité d'hévéa (Forest Data Partnership)", "producer": "Forest Data Partnership",
        "resolution": "10 m", "period": "2020", "version": "model_2025b", "licence": "à confirmer",
        "citation": "Forest Data Partnership (2025). Rubber probability model 2025b.", "roles": ["commodity"],
        "encoding": "probabilité 0-1, > 0,5", "gee": 'ee.ImageCollection("projects/forestdatapartnership/assets/rubber/model_2025b")',
        "http": None, "http_kind": None, "verified": [WHISP_SOURCE],
    },
    "wdpa": {
        "name": "World Database on Protected Areas (WDPA)",
        "producer": "UNEP-WCMC et UICN — Protected Planet",
        "resolution": "vecteur", "period": "mise à jour mensuelle", "version": "Oct2026",
        "licence": "Conditions d'utilisation de la WDPA (usage commercial soumis à accord — couvert par l'opérateur)",
        "citation": "UNEP-WCMC and IUCN (2026). Protected Planet: The World Database on Protected Areas (WDPA), "
                    "October 2026. Cambridge, UK. www.protectedplanet.net",
        "roles": ["protected"],
        "gee": 'ee.FeatureCollection("WCMC/WDPA/current/polygons")',
        "http": WDPA_HTTP, "http_kind": "vector_zip",
        "verified": ["https://developers.google.com/earth-engine/datasets/catalog/WCMC_WDPA_current_polygons",
                     WDPA_HTTP.format(month="Oct2026", iso3="CIV") + " (HTTP 200, 11,9 Mo)"],
    },
    "kba": {
        "name": "Key Biodiversity Areas (KBA)",
        "producer": "BirdLife International, au nom du partenariat KBA",
        "resolution": "vecteur", "period": "mise à jour semestrielle", "version": "à renseigner à l'import",
        "licence": "Licence KBA / IBAT (accordée sur demande)",
        "citation": "BirdLife International (2026). The World Database of Key Biodiversity Areas. "
                    "www.keybiodiversityareas.org",
        "roles": ["protected"],
        "gee": None, "http": None, "http_kind": "on_request",
        "verified": ["données fournies sur demande : https://www.keybiodiversityareas.org"],
        "todo": "fichier à demander à BirdLife puis à importer",
    },
    "rdue_civ": {
        "name": "Domaine forestier national (forêts classées, parcs et réserves, enclaves)",
        "producer": "SODEFOR / OIPR (données de l'opérateur)",
        "resolution": "vecteur", "period": "en vigueur", "version": "base de l'opérateur",
        "licence": "données privées de l'opérateur (jamais publiées)",
        "citation": "Base des forêts classées, parcs, réserves et enclaves de Côte d'Ivoire (données de l'opérateur).",
        "roles": ["national"], "gee": None, "http": None, "http_kind": None, "verified": ["base de l'opérateur"],
    },
    "whisp": {
        "name": "WHISP — What is in that plot?",
        "producer": "FAO / Forest Data Partnership",
        "resolution": "—", "period": "—", "version": "openforis-whisp (version figée à l'exécution)",
        "licence": "MIT",
        "citation": "FAO & Forest Data Partnership. Whisp (openforis-whisp). https://github.com/forestdatapartnership/whisp",
        "roles": ["aggregator"],
        "gee": "nécessite Google Earth Engine", "http": "https://whisp.earthmap.org", "http_kind": "web",
        "verified": ["https://pypi.org/project/openforis-whisp/ (3.0.0a22 au 2026-10-16, version préliminaire)"],
    },
}

FOREST_SOURCES = ["jrc_gfc2020", "tmf", "worldcover2020", "hansen"]
LOSS_SOURCES = ["hansen", "tmf_deforestation", "tmf_degradation", "radd", "glad_l"]
LOSS_LABELS = {"hansen": "Hansen", "tmf_deforestation": "TMF déforestation", "tmf_degradation": "TMF dégradation",
               "radd": "RADD", "glad_l": "GLAD-L"}
FOREST_LABELS = {"jrc_gfc2020": "JRC 2020", "tmf": "TMF", "worldcover2020": "WorldCover", "hansen": "Hansen"}


def dataset_table(used=None):
    """Tableau des jeux de données pour le rapport (nom, producteur, version, résolution, licence, citation)."""
    keys = used or list(SOURCES)
    return [{"id": k, **{f: SOURCES[k].get(f) for f in ("name", "producer", "version", "resolution", "licence", "citation")}}
            for k in keys if k in SOURCES]


def versions(used=None):
    return {k: SOURCES[k]["version"] for k in (used or SOURCES) if k in SOURCES}
