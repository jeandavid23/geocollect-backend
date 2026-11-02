"""
Usinage et transformation : quantités disponibles, bilan matière, visibilité, remontée de la chaîne.

Chaîne complète : producteur → pesée → lot de coopérative → fiche d'accompagnement (réception) → lot d'usinage
→ lot de transformation → produits. Chaque requête de remontée porte sur un niveau entier (jamais une requête
par objet).
"""
import uuid
from collections import defaultdict
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max, Sum

from apps.cooperatives.models import Cooperative
from apps.parcels.models import Parcel
from apps.accounts.permissions import scope_to_cooperative
from .models import Delivery, Lot, ProcessingBatch, ProcessingInput, ProcessingOutput, TransportSlip

PREFIX = {'usinage': 'USN', 'transformation': 'TRF'}
LOSS_WARNING_PCT = 15.0           # perte au-delà de laquelle le bilan matière est signalé
MAX_DEPTH = 10                    # garde-fou contre une chaîne de transformations anormalement longue


class ProcessingError(ValueError):
    """Lot d'usinage refusé (motif lisible par l'utilisateur)."""


def allowed_coop_ids(user):
    return set(scope_to_cooperative(Cooperative.objects.all(), user, field='id').values_list('id', flat=True))


def visible_batches(user):
    """Lots d'usinage dont toutes les coopératives d'origine sont gérées par l'utilisateur."""
    allowed = allowed_coop_ids(user)
    return (ProcessingBatch.objects.filter(cooperatives__in=allowed)
            .exclude(cooperatives__in=Cooperative.objects.exclude(id__in=allowed)).distinct())


def lot_available_kg(lots):
    """Poids net de chaque lot moins ce qui est déjà parti à l'usinage : {lot_id: (net, consommé)}."""
    ids = [lot.id for lot in lots]
    net = dict(Lot.objects.filter(id__in=ids).annotate(n=Sum('lines__weight_kg')).values_list('id', 'n'))
    used = dict(ProcessingInput.objects.filter(lot_id__in=ids).values('lot_id').annotate(u=Sum('weight_kg'))
                .values_list('lot_id', 'u'))
    return {i: (net.get(i) or Decimal(0), used.get(i) or Decimal(0)) for i in ids}


def batch_available_kg(batches):
    """Produits réutilisables d'un lot d'usinage (hors sous-produits) moins ce qui est déjà transformé."""
    ids = [b.id for b in batches]
    made = dict(ProcessingOutput.objects.filter(batch_id__in=ids).exclude(product__in=ProcessingOutput.BYPRODUCTS)
                .values('batch_id').annotate(m=Sum('weight_kg')).values_list('batch_id', 'm'))
    used = dict(ProcessingInput.objects.filter(source_batch_id__in=ids).values('source_batch_id')
                .annotate(u=Sum('weight_kg')).values_list('source_batch_id', 'u'))
    return {i: (made.get(i) or Decimal(0), used.get(i) or Decimal(0)) for i in ids}


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ProcessingError('Identifiant de lot invalide.') from None


def _check_inputs(user, inputs):
    """[(lot | None, lot d'usinage | None, poids)] après contrôle d'appartenance, d'état et de disponibilité."""
    inputs = [{**i, 'lot': _uuid(i['lot']) if i.get('lot') else None,
               'batch': _uuid(i['batch']) if i.get('batch') else None} for i in inputs]
    lot_ids = [i['lot'] for i in inputs if i.get('lot')]
    batch_ids = [i['batch'] for i in inputs if i.get('batch')]
    lots = {lot.id: lot for lot in scope_to_cooperative(Lot.objects.filter(id__in=lot_ids), user)}
    batches = {b.id: b for b in visible_batches(user).filter(id__in=batch_ids)}
    if len(lots) != len(set(lot_ids)) or len(batches) != len(set(batch_ids)):
        raise ProcessingError('Lot inconnu ou hors de votre périmètre.')
    for lot in lots.values():
        if lot.status != 'expedie':
            raise ProcessingError(f"Le lot {lot.code} n'est pas expédié : émettez d'abord sa fiche d'accompagnement.")
    wanted = defaultdict(Decimal)
    for i in inputs:
        w = Decimal(str(i.get('weight_kg') or 0))
        if w <= 0:
            raise ProcessingError('Chaque entrée doit avoir un poids positif.')
        wanted[i.get('lot') or i.get('batch')] += w
    stock = {**lot_available_kg(lots.values()), **batch_available_kg(batches.values())}
    for key, w in wanted.items():
        total, used = stock[key]
        if w > total - used:
            name = lots[key].code if key in lots else batches[key].code
            raise ProcessingError(f'{name} : {w} kg demandés, {total - used} kg encore disponibles.')
    return [(lots.get(i.get('lot')), batches.get(i.get('batch')), Decimal(str(i['weight_kg']))) for i in inputs]


def _next_code(operation, year):
    base = f'{PREFIX[operation]}-{year}-'
    last = ProcessingBatch.objects.filter(code__startswith=base).aggregate(m=Max('code'))['m']
    return f'{base}{(int(last.rsplit("-", 1)[1]) + 1 if last else 1):04d}'


def create_batch(user, operation, plant, process_date, inputs, outputs, notes=''):
    if operation not in PREFIX:
        raise ProcessingError('Opération inconnue (usinage ou transformation).')
    if not (plant or '').strip():
        raise ProcessingError("Indiquez l'usine.")
    if not process_date:
        raise ProcessingError("Indiquez la date de l'opération.")
    if not inputs or not outputs:
        raise ProcessingError('Indiquez au moins une entrée et un produit obtenu.')
    checked = _check_inputs(user, inputs)
    total_in = sum(w for _, _, w in checked)
    total_out = sum(Decimal(str(o.get('weight_kg') or 0)) for o in outputs)
    if any(Decimal(str(o.get('weight_kg') or 0)) <= 0 or o.get('product') not in ProcessingOutput.PRODUCTS for o in outputs):
        raise ProcessingError('Chaque produit obtenu doit avoir un type connu et un poids positif.')
    if total_out > total_in:
        raise ProcessingError(f'Bilan matière impossible : {total_out} kg obtenus pour {total_in} kg entrés.')
    coops = {lot.cooperative_id for lot, _, _ in checked if lot} | {
        c for _, b, _ in checked if b for c in b.cooperatives.values_list('id', flat=True)}
    for attempt in range(5):
        try:
            with transaction.atomic():
                batch = ProcessingBatch.objects.create(code=_next_code(operation, str(process_date)[:4]), operation=operation,
                                                       plant=plant, process_date=process_date, notes=notes, created_by=user)
                batch.cooperatives.set(coops)
                ProcessingInput.objects.bulk_create([ProcessingInput(batch=batch, lot=lot, source_batch=b, weight_kg=w)
                                                     for lot, b, w in checked])
                ProcessingOutput.objects.bulk_create([ProcessingOutput(batch=batch, product=o['product'],
                                                                       weight_kg=Decimal(str(o['weight_kg'])),
                                                                       reference=(o.get('reference') or '')[:80])
                                                      for o in outputs])
                return batch
        except IntegrityError:
            if attempt == 4:
                raise


def balance(batch):
    total_in = float(sum(i.weight_kg for i in batch.inputs.all()))
    total_out = float(sum(o.weight_kg for o in batch.outputs.all()))
    loss = total_in - total_out
    pct = round(100 * loss / total_in, 1) if total_in else 0.0
    return {'input_kg': round(total_in, 1), 'output_kg': round(total_out, 1), 'loss_kg': round(loss, 1), 'loss_pct': pct,
            'loss_warning': pct > LOSS_WARNING_PCT}


# ── Remontée et suivi aval ─────────────────────────────────────────────────────────────────────────────

def trace(batch):
    """Toute la chaîne en amont d'un lot d'usinage : lots d'usinage, lots, transport, pesées, producteurs, parcelles."""
    batches, frontier, lot_ids = [batch], [batch.id], set()
    for _ in range(MAX_DEPTH):
        rows = list(ProcessingInput.objects.filter(batch_id__in=frontier).values('lot_id', 'source_batch_id'))
        lot_ids |= {r['lot_id'] for r in rows if r['lot_id']}
        frontier = [r['source_batch_id'] for r in rows if r['source_batch_id']]
        if not frontier:
            break
        batches += list(ProcessingBatch.objects.filter(id__in=frontier))
    lots = list(Lot.objects.filter(id__in=lot_ids).select_related('cooperative'))
    lines = list(Lot.objects.filter(id__in=lot_ids).values('lines__producer_id', 'lines__producer__last_name',
                                                           'lines__producer__first_name', 'lines__producer__field_id_base',
                                                           'lines__producer__village', 'lines__weight_kg', 'code'))
    producers = {}
    for l in lines:
        pid = l['lines__producer_id']
        if pid is None:
            continue
        p = producers.setdefault(pid, {'producer': str(pid), 'code': l['lines__producer__field_id_base'],
                                       'name': f"{l['lines__producer__last_name'] or ''} {l['lines__producer__first_name'] or ''}".strip(),
                                       'village': l['lines__producer__village'], 'weight_kg': 0.0, 'lots': []})
        p['weight_kg'] = round(p['weight_kg'] + float(l['lines__weight_kg'] or 0), 1)
        p['lots'].append(l['code'])
    parcels = list(Parcel.objects.filter(producer_id__in=list(producers))
                   .values('field_id', 'producer_id', 'village', 'area_hectares', 'eudr_status'))
    deliveries = list(Delivery.objects.filter(lot_id__in=lot_ids, status='valide')
                      .values('number', 'delivery_date', 'net_kg', 'producer__field_id_base', 'lot__code'))
    slips = list(TransportSlip.objects.filter(lot_id__in=lot_ids).exclude(status='annulee')
                 .values('number', 'lot__code', 'destination', 'vehicle_plate', 'issued_at', 'status', 'gross_weight_kg',
                         'received_weight_kg', 'received_at'))
    return {
        'batches': [{'id': str(b.id), 'code': b.code, 'operation': b.operation, 'plant': b.plant,
                     'process_date': b.process_date} for b in batches],
        'lots': [{'id': str(lot.id), 'code': lot.code, 'cooperative': lot.cooperative.name, 'campaign': lot.campaign,
                  'lot_date': lot.lot_date} for lot in lots],
        'transport': [{**s, 'gross_weight_kg': float(s['gross_weight_kg']),
                       'received_weight_kg': float(s['received_weight_kg']) if s['received_weight_kg'] is not None else None}
                      for s in slips],
        'deliveries': [{'number': d['number'], 'date': d['delivery_date'], 'net_kg': float(d['net_kg']),
                        'producer_code': d['producer__field_id_base'], 'lot': d['lot__code']} for d in deliveries],
        'producers': sorted(producers.values(), key=lambda p: -p['weight_kg']),
        'parcels': [{**p, 'producer_id': str(p['producer_id'])} for p in parcels],
        'eudr': {'parcels': len(parcels), 'area_ha': round(sum(p['area_hectares'] or 0 for p in parcels), 2),
                 'compliant_parcels': sum(1 for p in parcels if p['eudr_status'] == 'compliant'),
                 'non_compliant_parcels': sum(1 for p in parcels if p['eudr_status'] == 'non_compliant')},
    }


def downstream(lot):
    """Suivi aval d'un lot de coopérative : lots d'usinage qui l'ont utilisé, quantité transformée et restante."""
    rows = list(ProcessingInput.objects.filter(lot=lot).values('batch_id', 'batch__code', 'batch__operation',
                                                               'batch__plant', 'batch__process_date', 'weight_kg'))
    net, used = lot_available_kg([lot])[lot.id]
    return {'batches': [{'id': str(r['batch_id']), 'code': r['batch__code'], 'operation': r['batch__operation'],
                         'plant': r['batch__plant'], 'process_date': r['batch__process_date'],
                         'weight_kg': float(r['weight_kg'])} for r in rows],
            'processed_kg': float(used), 'available_kg': float(net - used)}
