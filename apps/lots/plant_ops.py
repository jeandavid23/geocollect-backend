"""
Exploitation d'une usine : contrôle qualité à la réception, origine vérifiée des lots, alertes et notifications,
stock et tableau de bord. Toutes les sommes sont faites par la base, en un nombre fixe de requêtes.

Qualité (norme ICCO / Conseil Café-Cacao, épreuve à la coupe) :
  Grade I  : moisies ≤ 3 %, ardoisées ≤ 3 %, mitées + germées ≤ 3 %
  Grade II : moisies ≤ 4 %, ardoisées ≤ 8 %, mitées + germées ≤ 6 %
  au-delà  : hors standard. Humidité > 8 % ou grainage > 120 fèves / 100 g : hors norme commerciale.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Exists, F, OuterRef, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.accounts.notify import notify_cooperative, notify_users
from apps.parcels.models import LegacyParcel, Parcel
from .models import LotLine, ProcessingBatch, ProcessingInput, ProcessingOutput, Shipment, ShipmentItem, TransportSlip

MAX_MOISTURE_PCT = 8.0
MAX_BEAN_COUNT = 120
GRADES = ((1, 3, 3, 3), (2, 4, 8, 6))           # (grade, moisies, ardoisées, mitées + germées) maximum
DASHBOARD_MONTHS = 12


def is_operator(user):
    """Opérateur de pesée : réceptionne les camions, rien d'autre."""
    return user.role == 'usine' and user.plant_role == 'pesee'


def _num(v):
    return float(v) if v is not None else None


def quality(slip):
    """Grade et non-conformités d'une réception, d'après le contrôle qualité saisi (None si rien n'est saisi)."""
    mouldy, slaty = _num(slip.mouldy_pct), _num(slip.slaty_pct)
    defects = (_num(slip.insect_pct) or 0) + (_num(slip.germinated_pct) or 0)
    if mouldy is None and slaty is None and slip.moisture_pct is None and slip.bean_count is None:
        return None
    grade = None
    if mouldy is not None and slaty is not None:
        grade = next((f'Grade {"I" * g}' for g, m, s, d in GRADES if mouldy <= m and slaty <= s and defects <= d),
                     'Hors standard')
    issues = []
    if slip.moisture_pct is not None and float(slip.moisture_pct) > MAX_MOISTURE_PCT:
        issues.append(f'Humidité {float(slip.moisture_pct):g} % (maximum {MAX_MOISTURE_PCT:g} %)')
    if slip.bean_count is not None and slip.bean_count > MAX_BEAN_COUNT:
        issues.append(f'Grainage {slip.bean_count} fèves / 100 g (maximum {MAX_BEAN_COUNT})')
    if grade == 'Hors standard':
        issues.append('Épreuve à la coupe hors standard')
    return {'grade': grade, 'issues': issues}


def gap_pct(slip):
    if slip.received_weight_kg is None or not slip.gross_weight_kg:
        return None
    return round(100 * float(slip.received_weight_kg - slip.gross_weight_kg) / float(slip.gross_weight_kg), 2)


# ── Origine vérifiée (mode ségrégé / bilan de masse) ─────────────────────────────────────────────────────

def verified_lots(lot_ids):
    """Lots dont toute l'origine est vérifiée : chaque producteur a une parcelle, aucune n'est non conforme ni en attente."""
    unverified = Parcel.objects.filter(producer=OuterRef('producer_id')).exclude(eudr_status='compliant')
    has_parcel = Exists(Parcel.objects.filter(producer=OuterRef('producer_id'))) | Exists(
        LegacyParcel.objects.filter(producer=OuterRef('producer_id')))
    bad = set(LotLine.objects.filter(lot_id__in=lot_ids).filter(Exists(unverified) | ~has_parcel)
              .values_list('lot_id', flat=True))
    return {i for i in lot_ids if i not in bad}


def compliant_kg(checked):
    """Poids d'origine vérifiée parmi les entrées [(lot, lot d'usinage, poids)] d'un usinage."""
    ok = verified_lots([lot.id for lot, _, _ in checked if lot])
    total = Decimal(0)
    for lot, batch, w in checked:
        if lot is not None:
            total += w if lot.id in ok else 0
        elif batch is not None and batch.compliant_kg is not None:
            entered = batch.inputs.aggregate(t=Sum('weight_kg'))['t'] or 0
            total += w * batch.compliant_kg / entered if entered else 0
    return total.quantize(Decimal('0.1'))


# ── Notifications ────────────────────────────────────────────────────────────────────────────────────────

def plant_recipients(plant, managers_only=False):
    qs = plant.accounts.filter(is_active=True)
    if managers_only:
        qs = qs.exclude(plant_role='pesee')
    ids = list(qs.values_list('id', flat=True))
    return ids + ([plant.managed_by_id] if plant.managed_by_id else [])


def notify_truck_sent(slip):
    if slip.plant_id:
        notify_users(plant_recipients(slip.plant), title=f'Camion en route : {slip.number}',
                     message=f'{slip.cooperative.name} — lot {slip.lot.code}, {float(slip.gross_weight_kg):g} kg, '
                             f'camion {slip.vehicle_plate}.')


def notify_reception(slip):
    """La coopérative est prévenue de la réception, de l'écart de poids et de la décision qualité."""
    gap = gap_pct(slip)
    limit = float(slip.plant.gap_alert_pct) if slip.plant_id else 1.0
    alert = gap is not None and abs(gap) > limit
    label = {'refuse': 'refusé', 'reserve': 'accepté avec réserves'}.get(slip.decision, 'reçu')
    parts = [f"{float(slip.received_weight_kg):g} kg à l'arrivée ({gap:+g} %)" if gap is not None else '']
    if slip.decision_reason:
        parts.append(f'Motif : {slip.decision_reason}')
    ntype = 'error' if slip.decision == 'refuse' else 'warning' if alert or slip.decision == 'reserve' else 'success'
    title = f'Lot {slip.lot.code} {label} à {slip.plant.name if slip.plant_id else slip.destination}'
    notify_cooperative(slip.cooperative, title=title, message=' — '.join(p for p in parts if p), ntype=ntype)
    if alert and slip.plant_id:
        notify_users(plant_recipients(slip.plant, managers_only=True), ntype='warning',
                     title=f'Écart de poids {gap:+g} % sur {slip.number}',
                     message=f'Seuil d\'alerte : {limit:g} %. Vérifiez les plombs et la pesée.')


def notify_loss(batch, info):
    if info['loss_warning'] and batch.plant_ref_id:
        notify_users(plant_recipients(batch.plant_ref, managers_only=True), ntype='warning',
                     title=f'Perte anormale sur {batch.code} : {info["loss_pct"]:g} %',
                     message=f'{info["input_kg"]:g} kg entrés, {info["output_kg"]:g} kg obtenus '
                             f'(seuil {float(batch.plant_ref.loss_alert_pct):g} %).')


# ── Périmètre ────────────────────────────────────────────────────────────────────────────────────────

def scope(plant, user=None):
    """
    (fiches, lots d'usinage, expéditions) de l'usine visibles par l'utilisateur. Usine, super admin, propriétaire :
    tout. Coopérative : ses fiches, les lots d'usinage faits uniquement de ses lots, et les expéditions qui n'en
    contiennent pas d'autres (jamais les données d'une autre coopérative).
    """
    slips = TransportSlip.objects.filter(plant=plant)
    batches = ProcessingBatch.objects.filter(plant_ref=plant)
    shipments = Shipment.objects.filter(plant=plant)
    if user is not None and user.role == 'cooperative':
        from .processing import visible_batches
        slips = slips.filter(cooperative_id=user.cooperative_id)
        batches = visible_batches(user).filter(plant_ref=plant)
        hidden = ProcessingBatch.objects.filter(plant_ref=plant).exclude(id__in=batches.values('id'))
        shipments = shipments.filter(items__batch__in=batches.values('id')).exclude(items__batch__in=hidden).distinct()
    return slips, batches, shipments


# ── Stock ────────────────────────────────────────────────────────────────────────────────────────────────

def stock(plant, user=None, today=None):
    """En route, cacao reçu non usiné, produits finis non expédiés, avec l'ancienneté en magasin."""
    from .processing import batch_available_kg, lot_available_kg
    today = today or timezone.now().date()
    slip_qs, batch_qs, _ = scope(plant, user)
    slips = list(slip_qs.exclude(status__in=('annulee', 'refusee'))
                 .select_related('lot', 'cooperative').order_by('issued_at'))
    on_way = [s for s in slips if s.status == 'emise']
    received = {}
    for s in slips:
        if s.status == 'livree':
            received.setdefault(s.lot_id, s)                       # première réception du lot
    lots = [s.lot for s in received.values()]
    lot_stock = lot_available_kg(lots)
    raw = [{'lot': s.lot.code, 'lot_id': str(s.lot_id), 'cooperative': s.cooperative.name, 'slip': s.number,
            'received_at': s.received_at, 'days': (today - s.received_at.date()).days if s.received_at else None,
            'available_kg': float(lot_stock[s.lot_id][0] - lot_stock[s.lot_id][1])}
           for s in received.values() if lot_stock[s.lot_id][0] > lot_stock[s.lot_id][1]]
    batches = list(batch_qs)
    b_stock = batch_available_kg(batches)
    products = {}
    for o in (ProcessingBatch.objects.filter(id__in=[b.id for b in batches]).exclude(outputs__product__in=ProcessingOutput.BYPRODUCTS)
              .values('id', 'outputs__product')
              .annotate(w=Sum('outputs__weight_kg'))):
        products.setdefault(o['id'], []).append(o['outputs__product'])
    finished = [{'batch': b.code, 'batch_id': str(b.id), 'operation': b.operation, 'process_date': b.process_date,
                 'days': (today - b.process_date).days, 'products': products.get(b.id, []),
                 'available_kg': float(b_stock[b.id][0] - b_stock[b.id][1])}
                for b in batches if b_stock[b.id][0] > b_stock[b.id][1]]
    return {
        'on_the_way': [{'slip': s.number, 'lot': s.lot.code, 'cooperative': s.cooperative.name, 'issued_at': s.issued_at,
                        'vehicle_plate': s.vehicle_plate, 'weight_kg': float(s.gross_weight_kg)} for s in on_way],
        'raw': sorted(raw, key=lambda r: -(r['days'] or 0)),
        'finished': sorted(finished, key=lambda r: -r['days']),
        'totals': {'on_the_way_kg': round(sum(float(s.gross_weight_kg) for s in on_way), 1),
                   'raw_kg': round(sum(r['available_kg'] for r in raw), 1),
                   'finished_kg': round(sum(r['available_kg'] for r in finished), 1)},
    }


# ── Tableau de bord ──────────────────────────────────────────────────────────────────────────────────────

def _month(d):
    return d.strftime('%Y-%m') if d else None


def _months_before(first_day, n):
    index = first_day.year * 12 + first_day.month - 1 - n
    return first_day.replace(year=index // 12, month=index % 12 + 1)


def dashboard(plant, user=None, today=None):
    today = today or timezone.now().date()
    start = _months_before(today.replace(day=1), DASHBOARD_MONTHS - 1)
    slip_qs, batch_qs, ship_qs = scope(plant, user)
    slips = slip_qs.filter(issued_at__date__gte=start)
    done = slips.filter(status__in=('livree', 'refusee'))
    limit = float(plant.gap_alert_pct)
    gap = (F('received_weight_kg') - F('gross_weight_kg')) * 100 / F('gross_weight_kg')
    weighed = Q(received_weight_kg__isnull=False)          # écart : seulement les camions pesés à l'arrivée
    s = done.aggregate(n=Count('id'), sent=Sum('gross_weight_kg', filter=weighed), got=Sum('received_weight_kg'),
                       refused=Count('id', filter=Q(status='refusee')), reserve=Count('id', filter=Q(decision='reserve')),
                       moisture=Avg('moisture_pct'), mouldy=Avg('mouldy_pct'), slaty=Avg('slaty_pct'))
    over = done.annotate(g=gap).filter(Q(g__gt=limit) | Q(g__lt=-limit)).count()
    received = {_month(r['m']): r for r in done.filter(status='livree').annotate(m=TruncMonth('received_at'))
                .values('m').annotate(kg=Sum('received_weight_kg'), n=Count('id'))}
    by_coop = list(done.filter(status='livree').values('cooperative__name')
                   .annotate(kg=Sum('received_weight_kg'), n=Count('id')).order_by('-kg'))
    batches = batch_qs.filter(process_date__gte=start)
    ops = {}
    for op in ('usinage', 'transformation'):
        sub = ProcessingBatch.objects.filter(id__in=batches.filter(operation=op).values('id'))   # sans DISTINCT (sommes)
        i = ProcessingInput.objects.filter(batch__in=sub).aggregate(t=Sum('weight_kg'))['t'] or 0
        o = sub.aggregate(t=Sum('outputs__weight_kg'))['t'] or 0
        ops[op] = {'batches': sub.count(), 'input_kg': float(i), 'output_kg': float(o),
                   'loss_pct': round(100 * float(i - o) / float(i), 1) if i else None}
    shipped = ShipmentItem.objects.filter(shipment__in=ship_qs.filter(ship_date__gte=start).values('id'), batch__in=batch_qs.values('id'))
    ship_month = {_month(r['m']): float(r['kg']) for r in shipped.filter(shipment__status='expediee')
                  .annotate(m=TruncMonth('shipment__ship_date')).values('m').annotate(kg=Sum('weight_kg'))}
    shipments = Shipment.objects.filter(id__in=ship_qs.filter(ship_date__gte=start).values('id')).aggregate(
        n=Count('id'), sent=Count('id', filter=Q(status='expediee')),
        dds=Count('id', filter=~Q(dds_reference='')))
    months, m = [], start
    while m <= today:
        key = _month(m)
        months.append({'month': key, 'received_kg': float((received.get(key) or {}).get('kg') or 0),
                       'trucks': (received.get(key) or {}).get('n', 0), 'shipped_kg': ship_month.get(key, 0.0)})
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    sent, got = float(s['sent'] or 0), float(s['got'] or 0)
    return {
        'plant': {'id': str(plant.id), 'name': plant.name, 'traceability_mode': plant.traceability_mode,
                  'gap_alert_pct': limit, 'loss_alert_pct': float(plant.loss_alert_pct)},
        'receptions': {'trucks': s['n'], 'sent_kg': round(sent, 1), 'received_kg': round(got, 1),
                       'gap_pct': round(100 * (got - sent) / sent, 2) if sent else None, 'over_threshold': over,
                       'refused': s['refused'], 'with_reserves': s['reserve'],
                       'avg_moisture_pct': _round(s['moisture']), 'avg_mouldy_pct': _round(s['mouldy']),
                       'avg_slaty_pct': _round(s['slaty'])},
        'by_cooperative': [{'name': r['cooperative__name'], 'received_kg': float(r['kg'] or 0), 'trucks': r['n']}
                           for r in by_coop],
        'processing': ops,
        'shipments': {'count': shipments['n'], 'shipped': shipments['sent'], 'with_dds': shipments['dds']},
        'monthly': months,
    }


def _round(v):
    return round(float(v), 1) if v is not None else None
