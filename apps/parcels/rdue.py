"""
Matrice de risque RDUE (Règlement UE sur la Déforestation) — Côte d'Ivoire.
Port fidèle du plugin QGIS « Deforestation check » (v1.2.0) et de sa version web.

Croise deux critères pour conclure sur le placement sur le marché UE :
  - LÉGALITÉ : catégorie foncière de la parcelle (zones importées en base, LandZone) ;
      Parc/réserve, Forêt classée -> Non conforme ; Enclave, Agro-forêt, Domaine rural -> Conforme ;
      priorité : Enclave > Parc/réserve > Forêt classée > Agro-forêt > Domaine rural ;
  - ZÉRO DÉFORESTATION : usage du sol au 31/12 de l'année de coupure (forêt si >= 10 % de la
    parcelle était forêt dense non déboisée), et perte après la coupure.
Marché UE « Potentiellement conforme » seulement si les deux critères passent.
"""
import threading
import time

import shapely
from shapely.geometry import shape

CAT_ENCLAVE = 'Enclave'
CAT_PARC = 'Parc/reserve'
CAT_FORET_CLASSEE = 'Foret classee'
CAT_AGROFORET = 'Agro-foret classee'
CAT_RURAL = 'Domaine rural'
CATEGORY_PRIORITY = [CAT_ENCLAVE, CAT_PARC, CAT_FORET_CLASSEE, CAT_AGROFORET]

LEGAL_OK, LEGAL_NO, LEGAL_CHECK = 'Conforme', 'Non conforme', 'A verifier'
ZERO_OK, ZERO_NO = 'Conforme', 'Non conforme'
COVER_FOREST, COVER_AGRI, COVER_UNKNOWN = 'Foret', 'Agriculture', 'Indetermine'
RDUE_POT, RDUE_NO, RDUE_CHECK = 'Potentiellement conforme', 'Non conforme', 'A verifier'

CATEGORY_LEGALITY = {
    CAT_PARC: LEGAL_NO, CAT_FORET_CLASSEE: LEGAL_NO,
    CAT_ENCLAVE: LEGAL_OK, CAT_AGROFORET: LEGAL_OK, CAT_RURAL: LEGAL_OK,
}
_LEGAL_NOTE = {
    CAT_PARC: 'Parc/réserve : production interdite (aire protégée).',
    CAT_FORET_CLASSEE: 'Forêt classée : production illégale hors dérogation.',
    CAT_ENCLAVE: 'Enclave : production admise sous conditions.',
    CAT_AGROFORET: 'Agro-forêt classée : production admise sous conditions.',
    CAT_RURAL: 'Domaine rural : production agricole admise.',
}
LEGAL_EXTENDED_REMINDER = (
    "Critères étendus à vérifier sur pièces (non automatisés) : ARS 1000 (traçabilité cacao Côte d'Ivoire), "
    'code forestier national, droits fonciers coutumiers, législation du travail.')


# ─── Zones foncières (chargées une fois par processus, rechargées si elles changent) ───

class _Zones:
    def __init__(self):
        self.lock = threading.Lock()
        self.signature = None
        self.checked_at = 0.0
        self.layers = []          # [(catégorie, STRtree, [géométries], [noms])] par priorité

    def get(self):
        from django.db.models import Count, Max
        from .models import LandZone
        now = time.time()
        if now - self.checked_at < 60 and self.signature is not None:
            return self.layers
        with self.lock:
            sig = tuple(LandZone.objects.aggregate(n=Count('id'), m=Max('id')).values())
            self.checked_at = now
            if sig != self.signature:
                by_cat = {}
                for cat, name, geom in LandZone.objects.values_list('category', 'name', 'geometry').iterator():
                    try:
                        g = shape(geom)
                        if not g.is_valid:
                            g = shapely.make_valid(g)
                    except Exception:  # noqa: BLE001
                        continue
                    if not g.is_empty:
                        by_cat.setdefault(cat, ([], []))
                        by_cat[cat][0].append(g)
                        by_cat[cat][1].append(name)
                for geoms, _ in by_cat.values():
                    shapely.prepare(geoms)
                self.layers = [(cat, shapely.STRtree(by_cat[cat][0]), by_cat[cat][0], by_cat[cat][1])
                               for cat in CATEGORY_PRIORITY if cat in by_cat]
                self.signature = sig
        return self.layers


ZONES = _Zones()


def zone_count():
    return sum(len(layer[2]) for layer in ZONES.get())


def category_of(geom_json):
    """(catégorie, nom de la zone) : première catégorie (par priorité) qui recoupe la parcelle."""
    try:
        g = shape(geom_json)
        if not g.is_valid:
            g = shapely.make_valid(g)
    except Exception:  # noqa: BLE001
        return CAT_RURAL, ''
    for cat, tree, geoms, names in ZONES.get():
        # prédicat exact évalué par GEOS sur géométries préparées (pas de calcul d'intersection coûteux)
        hits = tree.query(g, predicate='intersects')
        if len(hits):
            return cat, names[int(hits[0])]
    return CAT_RURAL, ''


def apply_matrix(defor_ha, area_ha, forest2020_ha, category, tolerance_ha, forest_min_frac=0.10):
    """Règles du plugin : usage 2020, zéro déforestation, légalité, conclusion marché UE."""
    if forest2020_ha is not None and area_ha > 0:
        cover = COVER_FOREST if (forest2020_ha / area_ha) >= forest_min_frac else COVER_AGRI
    elif defor_ha > tolerance_ha:
        cover = COVER_FOREST
    else:
        cover = COVER_UNKNOWN
    zero_def = ZERO_NO if (cover == COVER_FOREST or defor_ha > tolerance_ha) else ZERO_OK
    legal = CATEGORY_LEGALITY.get(category, LEGAL_CHECK)
    if zero_def == ZERO_NO or legal == LEGAL_NO:
        market = RDUE_NO
    elif legal == LEGAL_OK and zero_def == ZERO_OK:
        market = RDUE_POT
    else:
        market = RDUE_CHECK
    return {
        'cover2020': cover, 'usage_cat': category, 'legalite': legal, 'zero_def': zero_def,
        'rdue_stat': market, 'legal_note': f"{_LEGAL_NOTE.get(category, 'Catégorie foncière indéterminée.')} {LEGAL_EXTENDED_REMINDER}",
    }
