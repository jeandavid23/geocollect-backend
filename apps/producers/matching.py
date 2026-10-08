"""
Croisement registre ↔ polygones par le code producteur / code parcelle.

  - code trouvé dans les polygones  → producteur cartographié (il peut avoir 2, 3 polygones ou plus) ;
  - code sans polygone              → producteur « à mapper » : il apparaît chez les agents mappeurs.

Comparaison tolérante : majuscules, espaces retirés, « 12.0 » = « 12 ». Un polygone codé « CI-BEO-00418-P2 »
(ou _2, /2, .2) est rattaché au producteur « CI-BEO-00418 » s'il n'existe pas de producteur à ce code exact.
"""
import re

from django.db import transaction

SEPARATORS = re.compile(r'^(.+?)[-_/.\s]+[A-Z]{0,3}\d{1,3}$')
PRODUCER_CODE_KEYS = ['code producteur', 'code_producteur', 'code planteur', 'codeproducteur', 'code parcelle', 'code_parcelle',
                      'field_id', 'fieldid', 'field id', 'code', 'id producteur', 'matricule', 'producer_code']


def normalize_code(v):
    if v is None:
        return None
    if isinstance(v, float):
        v = int(v) if v.is_integer() else v
    s = re.sub(r'\s+', '', str(v)).strip().upper()
    if not s or s in ('NULL', 'NONE', 'NAN', '-'):
        return None
    if re.fullmatch(r'-?\d+\.0+', s):
        s = s.split('.')[0]
    return s[:110]


def producer_for(key, index):
    """Producteur d'un code de polygone : code exact, sinon code sans son suffixe de parcelle (-P2, _2, /2…)."""
    if not key:
        return None
    if key in index:
        return index[key]
    k = key
    for _ in range(2):
        m = SEPARATORS.match(k)
        if not m:
            break
        k = m.group(1)
        if k in index:
            return index[k]
    return None


def detect_code_field(cooperative, sample=3000):
    """Attribut des polygones qui correspond le mieux aux codes du registre."""
    from apps.parcels.models import LegacyParcel
    from .models import Producer
    keys = set(Producer.objects.filter(cooperative=cooperative).exclude(match_key='').values_list('match_key', flat=True))
    index = {k: True for k in keys}
    if not keys:
        return None, 0
    scores = {}
    for props in LegacyParcel.objects.filter(cooperative=cooperative).values_list('properties', flat=True)[:sample]:
        for f, v in (props or {}).items():
            if producer_for(normalize_code(v), index):
                scores[f] = scores.get(f, 0) + 1
    if not scores:
        return None, 0
    best = max(scores, key=scores.get)
    return best, scores[best]


def relink(cooperative, code_field=None):
    """Recalcule le lien polygone → producteur de toute la coopérative. Renvoie les statistiques."""
    from apps.parcels.models import LegacyParcel, Parcel
    from .models import Producer

    if code_field is None:
        code_field, _ = detect_code_field(cooperative)
    index = {k: pid for k, pid in Producer.objects.filter(cooperative=cooperative).exclude(match_key='').values_list('match_key', 'id')}
    changed = []
    for lp in LegacyParcel.objects.filter(cooperative=cooperative).only('id', 'properties', 'code', 'match_key', 'producer_id').iterator(chunk_size=2000):
        raw = (lp.properties or {}).get(code_field) if code_field else lp.code
        code = '' if raw is None else str(raw).strip()[:100]
        key = normalize_code(code) or ''
        pid = producer_for(key, index)
        if (lp.code, lp.match_key, lp.producer_id) != (code, key, pid):
            lp.code, lp.match_key, lp.producer_id = code, key, pid
            changed.append(lp)
    with transaction.atomic():
        LegacyParcel.objects.bulk_update(changed, ['code', 'match_key', 'producer'], batch_size=1000)
    return stats(cooperative, code_field)


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
    }
