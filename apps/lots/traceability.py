"""
Traçabilité des volumes : numérotation des fiches, lots reconstruits à partir des pesées, volumes par producteur.

Volumes : pour chaque producteur et chaque campagne, le poids net livré est rapporté à la surface de ses parcelles
(cartographiées, sinon anciens polygones, sinon surface déclarée). Un rendement anormal signale un risque de
mélange avec du cacao d'origine inconnue (exigence de traçabilité EUDR). Toutes les sommes sont faites par la base,
en un nombre fixe de requêtes (jamais une requête par producteur : la base est distante).
"""
from collections import defaultdict
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, Sum

from apps.parcels.models import LegacyParcel, Parcel
from apps.producers.models import Producer
from .models import Delivery, LotLine, TransportSlip

# Rendements de référence (kg/ha/campagne). Au-delà de WATCH : à surveiller ; au-delà de MAX : dépassement.
# YIELD_MAX sert aussi aux contrôles des lots (views.MAX_YIELD).
YIELD_WATCH = {'cacao': 1000, 'cafe': 1800, 'autre': 10 ** 9}
YIELD_MAX = {'cacao': 1500, 'cafe': 2500, 'autre': 10 ** 9}
DEFAULT_TARE_PER_BAG_KG = Decimal('1.0')          # sac de jute vide ≈ 1 kg
NUMBER_ATTEMPTS = 5


def coop_prefix(coop):
    return ''.join(ch for ch in (coop.name or 'COOP').upper() if ch.isalnum())[:6] or 'COOP'


def next_number(model, kind, coop, year, width):
    base = f'{kind}-{coop_prefix(coop)}-{year}-'
    last = model.objects.filter(number__startswith=base).aggregate(m=Max('number'))['m']
    n = int(last.rsplit('-', 1)[1]) + 1 if last else 1
    return f'{base}{n:0{width}d}'


def create_numbered(model, kind, width, coop, year, **fields):
    """Crée l'objet avec le numéro suivant ; nouvel essai si deux saisies simultanées prennent le même numéro."""
    for attempt in range(NUMBER_ATTEMPTS):
        try:
            with transaction.atomic():
                return model.objects.create(number=next_number(model, kind, coop, year, width), cooperative=coop, **fields)
        except IntegrityError:
            if attempt == NUMBER_ATTEMPTS - 1:
                raise


def create_delivery(coop, **fields):
    return create_numbered(Delivery, 'PES', 5, coop, str(fields['delivery_date'])[:4], **fields)


def create_slip(coop, year, **fields):
    return create_numbered(TransportSlip, 'FA', 4, coop, year, **fields)


def weights(gross, bags, tare=None, deduction=0, unit_price=None):
    """(tare, net, montant) ; tare par défaut : 1 kg par sac."""
    tare = Decimal(tare) if tare is not None else DEFAULT_TARE_PER_BAG_KG * bags
    net = Decimal(gross) - tare - Decimal(deduction or 0)
    amount = (net * Decimal(unit_price)).quantize(Decimal('1')) if unit_price not in (None, '') else None
    return tare, net, amount


# ── Lots ─────────────────────────────────────────────────────────────────────────────────────────────

def rebuild_lines(lot):
    """Lignes du lot = pesées valides qui lui sont affectées, regroupées par producteur (poids, sacs, n° de fiches)."""
    rows = defaultdict(lambda: {'weight': Decimal(0), 'bags': 0, 'numbers': [], 'date': None})
    for d in lot.deliveries.filter(status='valide').order_by('delivery_date', 'number'):
        r = rows[d.producer_id]
        r['weight'] += d.net_kg
        r['bags'] += d.bags
        r['numbers'].append(d.number)
        r['date'] = d.delivery_date
    lot.lines.all().delete()
    LotLine.objects.bulk_create([
        LotLine(lot=lot, producer_id=pid, weight_kg=r['weight'], bags=r['bags'], delivery_date=r['date'],
                receipt=', '.join(r['numbers'])[:60])
        for pid, r in rows.items()])
    total_bags = sum(r['bags'] for r in rows.values())
    if total_bags and lot.bags != total_bags:
        lot.bags = total_bags
        lot.save(update_fields=['bags'])


def check_deliveries(lot_product, coop, ids, lot=None):
    """Pesées affectables au lot : de la coopérative, valides, même produit, libres (ou déjà dans ce lot)."""
    found = list(Delivery.objects.filter(id__in=ids, cooperative=coop))
    if len(found) != len(set(map(str, ids))):
        return None, 'Pesée inconnue dans cette coopérative.'
    for d in found:
        if d.status != 'valide':
            return None, f'Pesée {d.number} annulée : elle ne peut pas entrer dans un lot.'
        if d.product != lot_product:
            return None, f'Pesée {d.number} : produit différent de celui du lot.'
        if d.lot_id and (lot is None or d.lot_id != lot.id):
            return None, f'Pesée {d.number} déjà affectée au lot {d.lot.code}.'
    return found, None


@transaction.atomic
def assign_deliveries(lot, deliveries):
    """Le lot contient exactement ces pesées : les autres sont libérées, puis les lignes sont reconstruites."""
    keep = [d.id for d in deliveries]
    lot.deliveries.exclude(id__in=keep).update(lot=None)
    Delivery.objects.filter(id__in=keep).update(lot=lot)
    rebuild_lines(lot)


# ── Volumes par producteur ───────────────────────────────────────────────────────────────────────────

def _areas(coop, producer_ids):
    """Surface par producteur et sa source : parcelles cartographiées > anciens polygones > surface déclarée."""
    mapped = dict(Parcel.objects.filter(cooperative=coop, producer_id__in=producer_ids)
                  .values('producer_id').annotate(a=Sum('area_hectares')).values_list('producer_id', 'a'))
    legacy = dict(LegacyParcel.objects.filter(cooperative=coop, producer_id__in=producer_ids)
                  .values('producer_id').annotate(a=Sum('area_hectares')).values_list('producer_id', 'a'))
    declared = dict(Producer.objects.filter(id__in=producer_ids).values_list('id', 'total_area_ha'))
    out = {}
    for pid in producer_ids:
        for source, value in (('cartographiee', mapped.get(pid)), ('anciens_polygones', legacy.get(pid)),
                              ('declaree', declared.get(pid))):
            if value:
                out[pid] = (float(value), source)
                break
        else:
            out[pid] = (0.0, 'inconnue')
    return out


def _status(net, area, product):
    if not area:
        return 'sans_surface', None
    y = net / area
    if y > YIELD_MAX.get(product, 10 ** 9):
        return 'depassement', y
    if y > YIELD_WATCH.get(product, 10 ** 9):
        return 'a_surveiller', y
    return 'ok', y


def volumes(coop, campaign, product='cacao'):
    """Volumes livrés par producteur sur une campagne, comparés à la capacité de ses parcelles."""
    qs = Delivery.objects.filter(cooperative=coop, campaign=campaign, product=product, status='valide')
    rows = list(qs.values('producer_id').annotate(
        net=Sum('net_kg'), bags=Sum('bags'), n=Count('id'), last=Max('delivery_date'),
        in_lots=Sum('net_kg', filter=Q(lot__isnull=False)), amount=Sum('amount')))
    pids = [r['producer_id'] for r in rows]
    names = {p['id']: p for p in Producer.objects.filter(id__in=pids)
             .values('id', 'last_name', 'first_name', 'field_id_base', 'village')}
    areas = _areas(coop, pids)
    out = []
    for r in rows:
        p = names[r['producer_id']]
        area, source = areas[r['producer_id']]
        net = float(r['net'] or 0)
        status, y = _status(net, area, product)
        in_lots = float(r['in_lots'] or 0)
        out.append({
            'producer': str(r['producer_id']), 'code': p['field_id_base'], 'village': p['village'],
            'name': f"{p['last_name'] or ''} {p['first_name'] or ''}".strip(),
            'deliveries': r['n'], 'bags': r['bags'] or 0, 'net_kg': round(net, 1), 'in_lots_kg': round(in_lots, 1),
            'not_in_lot_kg': round(net - in_lots, 1), 'amount': float(r['amount'] or 0), 'last_delivery': r['last'],
            'area_ha': round(area, 2), 'area_source': source, 'yield_kg_ha': round(y) if y is not None else None,
            'capacity_kg': round(area * YIELD_MAX.get(product, 0)) if area else None, 'status': status,
        })
    out.sort(key=lambda x: -x['net_kg'])
    return {
        'campaign': campaign, 'product': product,
        'thresholds': {'watch_kg_ha': YIELD_WATCH.get(product), 'max_kg_ha': YIELD_MAX.get(product)},
        'producers': out,
        'totals': {'producers': len(out), 'deliveries': sum(x['deliveries'] for x in out),
                   'net_kg': round(sum(x['net_kg'] for x in out), 1),
                   'in_lots_kg': round(sum(x['in_lots_kg'] for x in out), 1),
                   'not_in_lot_kg': round(sum(x['not_in_lot_kg'] for x in out), 1),
                   'amount': sum(x['amount'] for x in out),
                   'producers_over_capacity': sum(1 for x in out if x['status'] == 'depassement'),
                   'producers_to_watch': sum(1 for x in out if x['status'] == 'a_surveiller'),
                   'producers_without_area': sum(1 for x in out if x['status'] == 'sans_surface')},
    }
