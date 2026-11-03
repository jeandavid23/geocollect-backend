"""
Analyse d'activité des coopératives (tableaux de bord) — calculée par la base, en un nombre FIXE de requêtes,
quel que soit le nombre de producteurs ou de parcelles (la base de production est distante).

Parcelles : les parcelles cartographiées ET les anciens polygones importés comptent comme parcelles de la
coopérative (nombre et surface).
Campagne cacao : du 1er octobre au 30 septembre (« 2026-2027 »).
"""
from datetime import timedelta

from django.db.models import Count, Exists, OuterRef, Q, Subquery, Sum
from django.db.models.functions import TruncMonth, TruncWeek
from django.utils import timezone

from apps.cooperatives.models import Cooperative
from apps.duediligence.models import DDAnalysis
from apps.lots import traceability as tr
from apps.lots.models import Delivery, Lot, ProcessingInput, TransportSlip
from apps.parcels.models import LegacyParcel, Parcel
from apps.producers.models import Producer

ACTIVITY_WEEKS = 12
AGENT_WINDOW_DAYS = 30
TOP = 5


def current_campaign(today=None):
    d = today or timezone.now().date()
    y = d.year if d.month >= 10 else d.year - 1
    return f'{y}-{y + 1}'


def previous_campaign(campaign):
    y = int(campaign[:4])
    return f'{y - 1}-{y}'


def _f(v, digits=1):
    return round(float(v or 0), digits)


def _by_coop(qs, **aggs):
    return {r['cooperative_id']: r for r in qs.values('cooperative_id').annotate(**aggs)}


def parcels_block(ids):
    mapped = Parcel.objects.filter(cooperative_id__in=ids).aggregate(n=Count('id'), ha=Sum('area_hectares'))
    legacy = LegacyParcel.objects.filter(cooperative_id__in=ids).aggregate(n=Count('id'), ha=Sum('area_hectares'))
    eudr = dict(Parcel.objects.filter(cooperative_id__in=ids).values('eudr_status').annotate(n=Count('id'))
                .values_list('eudr_status', 'n'))
    total = (mapped['n'] or 0) + (legacy['n'] or 0)
    ha = float(mapped['ha'] or 0) + float(legacy['ha'] or 0)
    return {'total': total, 'mapped': mapped['n'] or 0, 'legacy': legacy['n'] or 0, 'hectares': round(ha, 2),
            'mapped_ha': _f(mapped['ha'], 2), 'legacy_ha': _f(legacy['ha'], 2),
            'avg_ha': round(ha / total, 2) if total else 0,
            'eudr': {'compliant': eudr.get('compliant', 0), 'non_compliant': eudr.get('non_compliant', 0),
                     'pending': sum(n for k, n in eudr.items() if k not in ('compliant', 'non_compliant'))}}


def producers_block(ids):
    has_parcel = Exists(Parcel.objects.filter(producer=OuterRef('pk'))) | Exists(
        LegacyParcel.objects.filter(producer=OuterRef('pk')))
    r = Producer.objects.filter(cooperative_id__in=ids).aggregate(
        total=Count('id'), covered=Count('id', filter=Q(has_parcel)),
        villages=Count('village', distinct=True, filter=~Q(village='')))
    total = r['total'] or 0
    return {'total': total, 'with_parcels': r['covered'] or 0, 'to_map': total - (r['covered'] or 0),
            'coverage_pct': round(100 * (r['covered'] or 0) / total, 1) if total else 0.0, 'villages': r['villages'] or 0}


def risk_block(ids):
    """Dernière diligence raisonnée terminée de chaque coopérative (anciens polygones compris)."""
    latest = DDAnalysis.objects.filter(cooperative=OuterRef('pk'), status='done').order_by('-created_at').values('id')[:1]
    last_ids = [i for i in Cooperative.objects.filter(id__in=ids).annotate(l=Subquery(latest)).values_list('l', flat=True) if i]
    counts, area, dates, per = {'faible': 0, 'a_verifier': 0, 'eleve': 0}, {'faible': 0.0, 'a_verifier': 0.0, 'eleve': 0.0}, [], {}
    for a in DDAnalysis.objects.filter(id__in=last_ids).values('cooperative_id', 'summary', 'created_at'):
        s = a['summary'] or {}
        for k in counts:
            counts[k] += (s.get('counts') or {}).get(k, 0)
            area[k] += float((s.get('area_ha') or {}).get(k, 0))
        per[a['cooperative_id']] = (s.get('counts') or {}).get('eleve', 0)
        dates.append(a['created_at'])
    return {'analysed': sum(counts.values()), 'counts': counts, 'area_ha': {k: round(v, 2) for k, v in area.items()},
            'last_analysis': max(dates) if dates else None}, per


def volumes_block(ids, campaign):
    qs = Delivery.objects.filter(cooperative_id__in=ids, campaign=campaign, product='cacao', status='valide')
    r = qs.aggregate(net=Sum('net_kg'), n=Count('id'), producers=Count('producer', distinct=True), paid=Sum('amount'),
                     priced=Sum('net_kg', filter=Q(amount__isnull=False)))
    prev = Delivery.objects.filter(cooperative_id__in=ids, campaign=previous_campaign(campaign), product='cacao',
                                   status='valide').aggregate(net=Sum('net_kg'))['net']
    net, producers = float(r['net'] or 0), r['producers'] or 0
    delivering = list(qs.values_list('producer_id', flat=True).distinct())
    areas = tr._areas(None, delivering) if delivering else {}
    area = sum(a for a, _ in areas.values())
    return {
        'campaign': campaign, 'net_kg': round(net, 1), 'deliveries': r['n'] or 0, 'producers_delivering': producers,
        'amount_fcfa': _f(r['paid'], 0), 'avg_price': round(float(r['paid'] or 0) / float(r['priced']), 0) if r['priced'] else None,
        'kg_per_producer': round(net / producers, 1) if producers else 0, 'yield_kg_ha': round(net / area) if area else None,
        'previous_campaign': previous_campaign(campaign), 'previous_net_kg': _f(prev),
        'growth_pct': round(100 * (net - float(prev)) / float(prev), 1) if prev else None,
    }, qs


def monthly_block(qs):
    rows = qs.annotate(m=TruncMonth('delivery_date')).values('m').annotate(net=Sum('net_kg'), paid=Sum('amount'),
                                                                          n=Count('id')).order_by('m')
    return [{'month': r['m'].strftime('%Y-%m'), 'net_kg': _f(r['net']), 'amount_fcfa': _f(r['paid'], 0),
             'deliveries': r['n']} for r in rows]


def pipeline_block(ids, campaign, deliveries):
    lots = Lot.objects.filter(cooperative_id__in=ids, campaign=campaign)
    d = deliveries.aggregate(weighed=Sum('net_kg'), in_lots=Sum('net_kg', filter=Q(lot__isnull=False)),
                             shipped=Sum('net_kg', filter=Q(lot__status='expedie')))
    received = TransportSlip.objects.filter(lot__in=lots, status='livree').aggregate(r=Sum('received_weight_kg'))['r']
    processed = ProcessingInput.objects.filter(lot__in=lots).aggregate(p=Sum('weight_kg'))['p']
    status = dict(lots.values('status').annotate(n=Count('id')).values_list('status', 'n'))
    return ({'weighed_kg': _f(d['weighed']), 'in_lots_kg': _f(d['in_lots']), 'shipped_kg': _f(d['shipped']),
             'received_kg': _f(received), 'processed_kg': _f(processed)},
            {k: status.get(k, 0) for k in Lot.STATUSES})


def top_producers(deliveries):
    rows = deliveries.values('producer_id', 'producer__last_name', 'producer__first_name', 'producer__field_id_base',
                             'producer__village').annotate(net=Sum('net_kg'), paid=Sum('amount'), n=Count('id')) \
        .order_by('-net')[:TOP]
    return [{'code': r['producer__field_id_base'], 'village': r['producer__village'], 'deliveries': r['n'],
             'name': f"{r['producer__last_name'] or ''} {r['producer__first_name'] or ''}".strip(),
             'net_kg': _f(r['net']), 'amount_fcfa': _f(r['paid'], 0)} for r in rows]


def activity_block(ids, today):
    start = today - timedelta(weeks=ACTIVITY_WEEKS)
    weeks = {}
    for model, key in ((Parcel, 'mapped'), (LegacyParcel, 'legacy')):
        for r in model.objects.filter(cooperative_id__in=ids, created_at__date__gte=start) \
                .annotate(w=TruncWeek('created_at')).values('w').annotate(n=Count('id')):
            wk = r['w'].date() if hasattr(r['w'], 'date') else r['w']
            weeks.setdefault(wk, {'mapped': 0, 'legacy': 0})[key] = r['n']
    out, monday = [], start - timedelta(days=start.weekday())
    while monday <= today:
        w = weeks.get(monday, {'mapped': 0, 'legacy': 0})
        out.append({'week': monday.isoformat(), **w})
        monday += timedelta(weeks=1)
    agents = Parcel.objects.filter(cooperative_id__in=ids, created_at__date__gte=today - timedelta(days=AGENT_WINDOW_DAYS),
                                   agent__isnull=False).values('agent_id', 'agent__user__full_name') \
        .annotate(n=Count('id'), ha=Sum('area_hectares')).order_by('-n')[:TOP]
    return out, [{'name': a['agent__user__full_name'], 'parcels': a['n'], 'hectares': _f(a['ha'], 2)} for a in agents]


def per_cooperative(ids, campaign, high_risk):
    coops = dict(Cooperative.objects.filter(id__in=ids).values_list('id', 'name'))
    mapped = _by_coop(Parcel.objects.filter(cooperative_id__in=ids), n=Count('id'), ha=Sum('area_hectares'))
    legacy = _by_coop(LegacyParcel.objects.filter(cooperative_id__in=ids), n=Count('id'), ha=Sum('area_hectares'))
    prods = _by_coop(Producer.objects.filter(cooperative_id__in=ids), n=Count('id'))
    vols = _by_coop(Delivery.objects.filter(cooperative_id__in=ids, campaign=campaign, status='valide', product='cacao'),
                    net=Sum('net_kg'), paid=Sum('amount'))
    rows = []
    for cid, name in coops.items():
        m, lg = mapped.get(cid, {}), legacy.get(cid, {})
        rows.append({'id': str(cid), 'name': name, 'parcels': (m.get('n') or 0) + (lg.get('n') or 0),
                     'hectares': round(float(m.get('ha') or 0) + float(lg.get('ha') or 0), 2),
                     'producers': (prods.get(cid) or {}).get('n', 0), 'net_kg': _f((vols.get(cid) or {}).get('net')),
                     'amount_fcfa': _f((vols.get(cid) or {}).get('paid'), 0), 'high_risk': high_risk.get(cid, 0)})
    return sorted(rows, key=lambda r: -r['parcels'])


def analytics(coop_ids, campaign=None, with_breakdown=False, today=None):
    ids = list(coop_ids)
    today = today or timezone.now().date()
    campaign = campaign or current_campaign(today)
    risk, high = risk_block(ids)
    volumes, deliveries = volumes_block(ids, campaign)
    pipeline, lots = pipeline_block(ids, campaign, deliveries)
    activity, agents = activity_block(ids, today)
    out = {'campaign': campaign, 'cooperatives': len(ids), 'parcels': parcels_block(ids), 'producers': producers_block(ids),
           'risk': risk, 'volumes': volumes, 'monthly': monthly_block(deliveries), 'pipeline': pipeline, 'lots': lots,
           'top_producers': top_producers(deliveries), 'activity': activity, 'top_agents': agents}
    if with_breakdown:
        out['per_cooperative'] = per_cooperative(ids, campaign, high)
    return out
