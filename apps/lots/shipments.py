"""
Expéditions sortantes de l'usine : produits finis pris sur les lots d'usinage, vers un acheteur ou à l'export.

Une expédition se prépare (produits, conteneur, connaissement), puis se marque « expédiée » : la référence de la
déclaration de diligence raisonnée (DDS, système TRACES de l'UE) est alors obligatoire. Chaque expédition remonte
jusqu'aux producteurs et parcelles d'origine (voir processing.trace).
"""
from collections import defaultdict
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max, Sum
from django.utils import timezone

from .models import ProcessingBatch, ProcessingOutput, Shipment, ShipmentItem
from .processing import ProcessingError, _uuid, batch_available_kg

NUMBER_ATTEMPTS = 5


def visible_shipments(user):
    if user.role == 'usine':
        return Shipment.objects.filter(plant_id=user.plant_id) if user.plant_id else Shipment.objects.none()
    if user.role == 'owner':
        return Shipment.objects.all()
    if user.role == 'super_admin':
        return Shipment.objects.filter(plant__managed_by=user)
    return Shipment.objects.none()


def product_stock(batches):
    """{(lot d'usinage, produit): (produit, déjà expédié)} pour les produits finis (hors sous-produits)."""
    ids = [b.id for b in batches]
    made = {(r['batch_id'], r['product']): r['w'] for r in ProcessingOutput.objects.filter(batch_id__in=ids)
            .exclude(product__in=ProcessingOutput.BYPRODUCTS).values('batch_id', 'product').annotate(w=Sum('weight_kg'))}
    sent = {(r['batch_id'], r['product']): r['w'] for r in ShipmentItem.objects.filter(batch_id__in=ids)
            .values('batch_id', 'product').annotate(w=Sum('weight_kg'))}
    return {k: (v, sent.get(k) or Decimal(0)) for k, v in made.items()}


def shippable(plant):
    """Produits finis encore disponibles, par lot d'usinage et par produit."""
    batches = list(ProcessingBatch.objects.filter(plant_ref=plant).prefetch_related('inputs'))
    by_id = {b.id: b for b in batches}
    left = batch_available_kg(batches)                     # après transformations et expéditions
    out = []
    for (bid, product), (made, sent) in product_stock(batches).items():
        kg = min(made - sent, left[bid][0] - left[bid][1])
        if kg > 0:
            b = by_id[bid]
            out.append({'batch': str(bid), 'batch_code': b.code, 'operation': b.operation, 'product': product,
                        'product_label': ProcessingOutput.PRODUCTS[product], 'available_kg': float(kg),
                        'compliant_share_pct': _share(b)})
    return sorted(out, key=lambda r: r['batch_code'])


def _share(batch):
    entered = sum(i.weight_kg for i in batch.inputs.all())
    if batch.compliant_kg is None or not entered:
        return None
    return round(100 * float(batch.compliant_kg) / float(entered), 1)


def _check_items(plant, items):
    if not items:
        raise ProcessingError('Ajoutez au moins un produit à expédier.')
    rows = [{**i, 'batch': _uuid(i.get('batch'))} for i in items]
    batches = {b.id: b for b in ProcessingBatch.objects.filter(plant_ref=plant, id__in=[r['batch'] for r in rows])}
    if len(batches) != len({r['batch'] for r in rows}):
        raise ProcessingError("Lot d'usinage inconnu dans cette usine.")
    wanted, per_batch = defaultdict(Decimal), defaultdict(Decimal)
    for r in rows:
        w = Decimal(str(r.get('weight_kg') or 0))
        if w <= 0 or r.get('product') not in ProcessingOutput.PRODUCTS:
            raise ProcessingError('Chaque produit expédié doit avoir un type connu et un poids positif.')
        wanted[(r['batch'], r['product'])] += w
        per_batch[r['batch']] += w
    stock, left = product_stock(batches.values()), batch_available_kg(batches.values())
    for (bid, product), w in wanted.items():
        made, sent = stock.get((bid, product), (Decimal(0), Decimal(0)))
        if w > made - sent:
            raise ProcessingError(f'{batches[bid].code} : {w} kg de « {ProcessingOutput.PRODUCTS[product]} » demandés, '
                                  f'{made - sent} kg disponibles.')
    for bid, w in per_batch.items():
        if w > left[bid][0] - left[bid][1]:
            raise ProcessingError(f'{batches[bid].code} : {w} kg demandés, {left[bid][0] - left[bid][1]} kg encore disponibles.')
    return [(batches[r['batch']], r['product'], Decimal(str(r['weight_kg'])), int(r.get('bags') or 0)) for r in rows]


def _next_number(year):
    base = f'EXP-{year}-'
    last = Shipment.objects.filter(number__startswith=base).aggregate(m=Max('number'))['m']
    return f'{base}{(int(last.rsplit("-", 1)[1]) + 1 if last else 1):04d}'


FIELDS = ('client', 'destination_country', 'container', 'seal', 'bill_of_lading', 'vessel', 'dds_reference',
          'dds_verification', 'notes')


def create(user, plant, data):
    if not (data.get('client') or '').strip():
        raise ProcessingError('Indiquez le client ou l\'acheteur.')
    if not data.get('ship_date'):
        raise ProcessingError("Indiquez la date d'expédition.")
    checked = _check_items(plant, data.get('items') or [])
    fields = {f: (data.get(f) or '').strip() for f in FIELDS}
    for attempt in range(NUMBER_ATTEMPTS):
        try:
            with transaction.atomic():
                ship = Shipment.objects.create(plant=plant, number=_next_number(str(data['ship_date'])[:4]),
                                               ship_date=data['ship_date'], created_by=user, **fields)
                ShipmentItem.objects.bulk_create([ShipmentItem(shipment=ship, batch=b, product=p, weight_kg=w, bags=n)
                                                  for b, p, w, n in checked])
                return ship
        except IntegrityError:
            if attempt == NUMBER_ATTEMPTS - 1:
                raise


def update(ship, data):
    """Mise à jour des documents ; « expédiée » exige la référence DDS et fige l'expédition."""
    if ship.status == 'expediee':
        raise ProcessingError('Expédition déjà partie : elle ne se modifie plus.')
    for f in FIELDS:
        if f in data:
            setattr(ship, f, (data.get(f) or '').strip())
    if data.get('status') == 'expediee':
        if not ship.dds_reference:
            raise ProcessingError('Indiquez la référence de la déclaration de diligence raisonnée (DDS) avant '
                                  "de marquer l'expédition comme partie.")
        ship.status, ship.shipped_at = 'expediee', timezone.now()
    ship.save()
    return ship


def serialize(ship):
    items = list(ship.items.all())
    return {
        'id': str(ship.id), 'number': ship.number, 'plant': str(ship.plant_id), 'plant_name': ship.plant.name,
        'ship_date': ship.ship_date, 'status': ship.status, 'shipped_at': ship.shipped_at,
        **{f: getattr(ship, f) for f in FIELDS},
        'items': [{'batch': str(i.batch_id), 'batch_code': i.batch.code, 'product': i.product,
                   'product_label': ProcessingOutput.PRODUCTS[i.product], 'weight_kg': float(i.weight_kg), 'bags': i.bags}
                  for i in items],
        'total_kg': round(float(sum(i.weight_kg for i in items)), 1),
        'created_by_name': ship.created_by.full_name if ship.created_by_id else '',
    }
