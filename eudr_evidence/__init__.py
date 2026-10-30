"""
eudr_evidence — moteur commun d'analyse du risque de déforestation EUDR par convergence de preuves.

Partagé (recopié par tools/sync.py) entre GeoCollect EUDR, les plugins QGIS « Deforestation check » et
« Deforestation Risk Analyzer », la web app « Deforestation check » et le pipeline jdk-eudr : la même logique
de classement partout, couverte par les mêmes tests.

  catalog   jeux de données (identifiants vérifiés, versions, licences, citations)
  config    seuils et paramètres réglementaires (un seul endroit)
  evidence  étapes 2 et 3 : forêt au 31/12/2020 et pertes après 2020, sur une grille commune (numpy)
  classify  étapes 4 et 5 : contexte, classement du risque, score de convergence, justification
  whisp_io  export GeoJSON pour whisp.earthmap.org, import du CSV de résultats, concordance
  manifest  traçabilité (versions, seuils, empreinte SHA-256 des parcelles)
  geometry  contrôles géométriques et surfaces géodésiques
  readers   lecteurs de rasters : rasterio (serveurs) et GDAL (QGIS)
"""

__version__ = "1.0.0"
ENGINE_NAME = "eudr_evidence"
