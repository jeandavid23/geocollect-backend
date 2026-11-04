"""
Registre de la coopérative, une ligne par parcelle : code producteur + code parcelle.

Les lignes d'un même code producteur forment UN producteur (données de sa première ligne) ; chaque code parcelle
devient une parcelle du registre (RegistryParcel). Surface du producteur = somme de ses parcelles. Production
estimée : la même valeur sur toutes ses lignes est un total producteur (gardé une fois), sinon la somme des parcelles.
"""
from collections import defaultdict
from decimal import Decimal

from .matching import normalize_code
from .models import RegistryParcel


def _total_estimate(values):
    known = [v for v in values if v is not None]
    if not known:
        return None
    return known[0] if len(set(known)) == 1 else round(sum(known), 1)


def registry_groups(coded, errors):
    """
    coded : [(données, code producteur, code parcelle)].
    → ({clé producteur: (données, code)}, [(clé producteur, code parcelle, surface, production, colonnes)])
    """
    groups, parcels, seen_parcels = {}, defaultdict(list), set()
    for data, code, parcel in coded:
        key = normalize_code(code)
        pkey = normalize_code(parcel) if parcel else None
        if key in groups and not pkey:
            errors.append({'index': None, 'errors': {'code': f'Code {code} en double dans le fichier (sans code parcelle) : '
                                                              'seule la 1re ligne est prise.'}})
            continue
        if pkey and pkey in seen_parcels:
            errors.append({'index': None, 'errors': {'parcel_code': f'Code parcelle {parcel} en double dans le fichier : '
                                                                     'seule la 1re ligne est prise.'}})
            continue
        if key not in groups:
            groups[key] = (dict(data), code)
        if pkey:
            seen_parcels.add(pkey)
            parcels[key].append((parcel, data.get('total_area_ha'), data.get('estimated_production_kg'),
                                 data.get('extra_data') or {}))
    for key, rows in parcels.items():
        if len(rows) < 2:
            continue
        first = groups[key][0]
        areas = [r[1] for r in rows if r[1] is not None]
        if areas:
            first['total_area_ha'] = round(sum(Decimal(str(a)) for a in areas), 2)
        estimate = _total_estimate([r[2] for r in rows])
        if estimate is not None:
            first['estimated_production_kg'] = estimate
    rows = [(key, parcel, area, est, extra) for key, items in parcels.items() for parcel, area, est, extra in items]
    return groups, rows


def save_registry_parcels(cooperative, rows, producers_by_key):
    """Crée ou met à jour les parcelles du registre (code parcelle unique dans la coopérative)."""
    if not rows:
        return 0
    keys = [normalize_code(parcel) for _, parcel, _, _, _ in rows]
    existing = {rp.match_key: rp for rp in RegistryParcel.objects.filter(cooperative=cooperative, match_key__in=keys)}
    new, changed = [], []
    for (pkey, parcel, area, estimate, extra), mk in zip(rows, keys):
        producer = producers_by_key.get(pkey)
        if producer is None:
            continue
        values = {'producer': producer, 'parcel_code': parcel, 'area_ha': area, 'estimated_production_kg': estimate,
                  'extra_data': extra}
        rp = existing.get(mk)
        if rp is None:
            new.append(RegistryParcel(cooperative=cooperative, match_key=mk, **values))
            continue
        for field, value in values.items():
            setattr(rp, field, value)
        changed.append(rp)
    RegistryParcel.objects.bulk_create(new, batch_size=1000)
    RegistryParcel.objects.bulk_update(changed, ['producer', 'parcel_code', 'area_ha', 'estimated_production_kg', 'extra_data'],
                                       batch_size=500)
    return len(new) + len(changed)
