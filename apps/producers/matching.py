"""
Croisement registre ↔ polygones par le code producteur / code parcelle.

  - code trouvé dans les polygones  → producteur cartographié (il peut avoir 2, 3 polygones ou plus) ;
  - code sans polygone              → producteur « à mapper » : il apparaît chez les agents mappeurs.

Comparaison tolérante, du plus strict au plus large (on s'arrête au premier niveau qui donne UN seul producteur) :
  1. code normalisé : majuscules, accents et caractères invisibles retirés, apostrophe Excel retirée,
     séparateurs unifiés (« BA_SE_354 » = « BA SE 354 » = « BA/SE/354 » = « BA–SE–354 » = « BA-SE-354 »), « 12.0 » = « 12 » ;
  2. zéros de tête ignorés (« CI-SBR-0003 » = « CI-SBR-3 ») ;
  3. séparateurs ignorés (« BASE354 » = « BA-SE-354 ») ;
  4. code sans son suffixe de parcelle (« -P2 », « _2 », « /PARC3 », « P1 » collé) ;
  5. producteur codé lui-même avec un code de parcelle (« X-P1 ») : un polygone « X-P2 » lui est rattaché.
Un code qui correspond à plusieurs producteurs n'est jamais rattaché au hasard : il est signalé « ambigu ».
"""
import re
import unicodedata

from django.db import transaction

INVISIBLE = re.compile(r'[​-‏⁠﻿­]')
SEPS = re.compile(r'[\s\-_/\\.‐-―|:;,]+')
# suffixe de parcelle sur un code normalisé : « -P2 », « -PARC-3 », « -UA2 », « -2 » (1-2 chiffres), « P2 » collé
PARCEL_SUFFIX = re.compile(r'^(.+?)(?:-(?:P|PA|PAR|PARC|PARCELLE|UA|U|PL)-?\d{1,3}|-\d{1,2}|(?<=\d)P\d{1,3})$')
PRODUCER_CODE_KEYS = ['code producteur', 'code_producteur', 'code planteur', 'codeproducteur', 'code parcelle', 'code_parcelle',
                      'field_id', 'fieldid', 'field id', 'code', 'id producteur', 'matricule', 'producer_code',
                      'identifiant interne unique', 'identifiant']
EMPTY = {'NULL', 'NONE', 'NAN', 'N/A', 'NA', '#N/A', '0'}


def normalize_code(v):
    """Code canonique (stocké dans match_key) : comparaison exacte entre registre et polygones."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, float):
        if v != v:  # NaN
            return None
        v = int(v) if v.is_integer() else v
    s = INVISIBLE.sub('', str(v)).replace(' ', ' ').strip().strip('\'"`’‘«»').strip()
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().upper()
    if re.fullmatch(r'-?\d+\.0+', s):
        s = s.split('.')[0]
    s = SEPS.sub('-', s).strip('-')
    if not s or s in EMPTY:
        return None
    return s[:60]


def clean_code(v, limit=50):
    """Code tel qu'écrit dans le registre (conservé), sans les parasites d'Excel : « 354.0 », apostrophe, espaces insécables."""
    if v is None or isinstance(v, bool):
        return ''
    if isinstance(v, float):
        v = int(v) if v.is_integer() else v
    s = INVISIBLE.sub('', str(v)).replace('\u00a0', ' ').strip().strip('\'"`’‘«»').strip()
    if re.fullmatch(r'-?\d+\.0+', s):
        s = s.split('.')[0]
    s = re.sub(r'\s+', ' ', s)
    return '' if s.upper() in EMPTY else s[:limit]


def _no_zeros(k):
    return re.sub(r'(?<!\d)0+(?=\d)', '', k)


def _compact(k):
    return _no_zeros(k).replace('-', '')


def _drop_trailing_words(k):
    """Retire les segments finaux composés uniquement de lettres (un nom après le code), s'il reste un code avec chiffres."""
    parts = k.split('-')
    while len(parts) > 1 and parts[-1].isalpha() and len(parts[-1]) > 1:
        parts.pop()
    out = '-'.join(parts)
    return out if any(ch.isdigit() for ch in out) else None


def _strip(k):
    m = PARCEL_SUFFIX.match(k)
    return m.group(1) if m else None


class CodeIndex:
    """Index des producteurs d'une coopérative, interrogé du plus strict au plus large."""

    def __init__(self, pairs):
        self.levels = [{}, {}, {}]   # exact, sans zéros, sans séparateurs
        self.base = {}               # code producteur sans suffixe de parcelle (producteurs codés « X-P1 »)
        for key, pid in pairs:
            if not key:
                continue
            for idx, k in zip(self.levels, (key, _no_zeros(key), _compact(key))):
                idx.setdefault(k, set()).add(pid)
            b = _strip(key)
            if b:
                self.base.setdefault(_compact(b), set()).add(pid)

    @classmethod
    def for_cooperative(cls, cooperative):
        from .models import Producer
        return cls(Producer.objects.filter(cooperative=cooperative).exclude(match_key='').values_list('match_key', 'id'))

    def __bool__(self):
        return bool(self.levels[0])

    def lookup(self, key):
        """(producteur, état) — état : 'ok', 'ambigu' ou 'absent'."""
        if not key:
            return None, 'absent'
        res = self._lookup(key)
        if res[1] == 'absent':
            # « GUI-029-P1 KAFANDO KARIDJATOU » : le nom du producteur suit le code → on retire les mots en lettres seules
            short = _drop_trailing_words(key)
            if short and short != key:
                res = self._lookup(short)
        return res

    def _lookup(self, key):
        candidates, k = [key], key
        for _ in range(2):
            k = _strip(k)
            if not k:
                break
            candidates.append(k)
        for c in candidates:
            for idx, form in zip(self.levels, (c, _no_zeros(c), _compact(c))):
                ids = idx.get(form)
                if ids:
                    return (next(iter(ids)), 'ok') if len(ids) == 1 else (None, 'ambigu')
        for c in candidates[1:]:
            ids = self.base.get(_compact(c))
            if ids:
                return (next(iter(ids)), 'ok') if len(ids) == 1 else (None, 'ambigu')
        return None, 'absent'


class ParcelIndex:
    """Parcelles du registre d'une coopérative : code parcelle → (parcelle du registre, producteur)."""

    def __init__(self, rows):
        self.levels = [{}, {}, {}]   # exact, sans zéros, sans séparateurs
        for key, rid, pid in rows:
            if key:
                for idx, k in zip(self.levels, (key, _no_zeros(key), _compact(key))):
                    idx.setdefault(k, set()).add((rid, pid))

    @classmethod
    def for_cooperative(cls, cooperative):
        from .models import RegistryParcel
        return cls(RegistryParcel.objects.filter(cooperative=cooperative).values_list('match_key', 'id', 'producer_id'))

    def lookup(self, key):
        """(parcelle, producteur) si le code désigne UNE parcelle du registre, sinon None."""
        if not key:
            return None
        for idx, form in zip(self.levels, (key, _no_zeros(key), _compact(key))):
            hits = idx.get(form)
            if hits:
                return next(iter(hits)) if len(hits) == 1 else None
        return None


def resolve(code_key, parcel_key, parcels, producers):
    """
    (parcelle du registre, producteur, état) d'un polygone : le code parcelle désigne la parcelle du registre (et donc
    son producteur) ; à défaut, le code producteur — ou le code parcelle sans son suffixe — désigne le producteur.
    """
    for key in (parcel_key, code_key):
        hit = parcels.lookup(key)
        if hit:
            return hit[0], hit[1], 'ok'
    state = 'absent'
    for key in (code_key, parcel_key):
        if key:
            pid, state = producers.lookup(key)
            if pid:
                return None, pid, 'ok'
    return None, None, state


def producer_for(key, index):
    """Producteur d'un code de polygone (index : CodeIndex, ou dict {match_key: id})."""
    if not isinstance(index, CodeIndex):
        index = CodeIndex(index.items())
    return index.lookup(key)[0]


def detect_code_field(cooperative, sample=3000):
    """Attribut des polygones qui correspond le mieux aux codes du registre."""
    from apps.parcels.models import LegacyParcel
    index = CodeIndex.for_cooperative(cooperative)
    if not index:
        return None, 0
    scores = {}
    for props in LegacyParcel.objects.filter(cooperative=cooperative).values_list('properties', flat=True)[:sample]:
        for f, v in (props or {}).items():
            if index.lookup(normalize_code(v))[1] == 'ok':
                scores[f] = scores.get(f, 0) + 1
    if not scores:
        return None, 0
    best = max(scores, key=scores.get)
    return best, scores[best]


def relink(cooperative, code_field=None, parcel_field=None):
    """
    Recalcule le lien polygone → parcelle du registre → producteur de toute la coopérative.
    `parcel_field` : attribut des polygones portant le code parcelle (sinon le code parcelle déjà lu est gardé).
    """
    from apps.parcels.models import LegacyParcel

    if code_field is None:
        code_field, _ = detect_code_field(cooperative)
    producers, parcels = CodeIndex.for_cooperative(cooperative), ParcelIndex.for_cooperative(cooperative)
    changed, unmatched, ambiguous = [], [], []
    fields = ('id', 'properties', 'code', 'match_key', 'producer_id', 'parcel_code', 'registry_parcel_id')
    for lp in LegacyParcel.objects.filter(cooperative=cooperative).only(*fields).iterator(chunk_size=2000):
        props = lp.properties or {}
        raw = props.get(code_field) if code_field else lp.code
        code = '' if raw is None else str(raw).strip()[:100]
        parcel = str(props.get(parcel_field) or '').strip()[:100] if parcel_field else lp.parcel_code
        key = normalize_code(code) or ''
        rid, pid, state = resolve(key, normalize_code(parcel), parcels, producers)
        label = parcel or code
        if label and state == 'absent' and len(unmatched) < 50:
            unmatched.append(label)
        if state == 'ambigu' and len(ambiguous) < 50:
            ambiguous.append(label)
        new = (code, key, pid, parcel, rid)
        if (lp.code, lp.match_key, lp.producer_id, lp.parcel_code, lp.registry_parcel_id) != new:
            lp.code, lp.match_key, lp.producer_id, lp.parcel_code, lp.registry_parcel_id = new
            changed.append(lp)
    with transaction.atomic():
        LegacyParcel.objects.bulk_update(changed, ['code', 'match_key', 'producer', 'parcel_code', 'registry_parcel'],
                                         batch_size=1000)
    return {**stats(cooperative, code_field), 'unmatched_codes': unmatched, 'ambiguous_codes': ambiguous}


def stats(cooperative, code_field=None):
    from django.db.models import Count, Q
    from apps.parcels.models import LegacyParcel, Parcel
    from .models import Producer
    legacy = dict(LegacyParcel.objects.filter(cooperative=cooperative, producer__isnull=False)
                  .values_list('producer').annotate(n=Count('id')).values_list('producer', 'n'))
    mapped = dict(Parcel.objects.filter(cooperative=cooperative).values_list('producer').annotate(n=Count('id')).values_list('producer', 'n'))
    dist = {'1': 0, '2': 0, '3+': 0}
    total = to_map = 0
    for pid in Producer.objects.filter(cooperative=cooperative).values_list('id', flat=True):
        total += 1
        n = legacy.get(pid, 0) + mapped.get(pid, 0)
        if n == 0:
            to_map += 1
        else:
            dist['1' if n == 1 else '2' if n == 2 else '3+'] += 1
    lp = LegacyParcel.objects.filter(cooperative=cooperative).aggregate(
        total=Count('id'), linked=Count('id', filter=Q(producer__isnull=False)), nocode=Count('id', filter=Q(match_key='')))
    return {
        'code_field': code_field, 'producers': total, 'mapped_producers': total - to_map, 'to_map': to_map,
        'polygons_per_producer': dist, 'legacy_polygons': lp['total'], 'linked_polygons': lp['linked'],
        'orphan_polygons': lp['total'] - lp['linked'], 'polygons_without_code': lp['nocode'],
        **_registry_stats(cooperative),
    }


def _registry_stats(cooperative):
    """Parcelles du registre, et combien ont au moins un polygone rattaché par leur code parcelle."""
    from django.db.models import Count, Q
    from .models import RegistryParcel
    r = RegistryParcel.objects.filter(cooperative=cooperative).aggregate(
        n=Count('id'), with_polygon=Count('id', filter=Q(polygons__isnull=False), distinct=True))
    return {'registry_parcels': r['n'], 'registry_parcels_with_polygon': r['with_polygon'],
            'registry_parcels_without_polygon': r['n'] - r['with_polygon']}
