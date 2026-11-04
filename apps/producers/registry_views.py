"""
Registre de la coopérative (une ligne par parcelle) — /api/v1/producers/
  <id>/parcels/        GET : parcelles du registre d'un producteur, leurs polygones, et ses polygones sans code parcelle connu
  registry-export/     GET ?cooperative= : registre normalisé (code producteur, code parcelle, polygones, statut EUDR)
                       et polygones non rattachés — en un nombre fixe de requêtes
"""
from collections import defaultdict

from django.db.models import Count, Sum
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsAgentOrAbove, IsCooperativeOrAdmin, resolve_cooperative, scope_to_cooperative
from apps.parcels.models import LegacyParcel, Parcel
from .models import Producer, RegistryParcel

def _num(v, digits=2):
    return round(float(v), digits) if v is not None else None


class ProducerParcelsView(APIView):
    permission_classes = [IsAgentOrAbove]

    def get(self, request, pk):
        producer = scope_to_cooperative(Producer.objects.all(), request.user).filter(pk=pk).first()
        if producer is None:
            return Response({'detail': 'Producteur introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        polygons = list(LegacyParcel.objects.filter(producer=producer)
                        .values('id', 'code', 'parcel_code', 'registry_parcel_id', 'area_hectares', 'source_file'))
        by_parcel = defaultdict(list)
        for p in polygons:
            by_parcel[p['registry_parcel_id']].append(p)
        parcels = [{'id': str(rp.id), 'parcel_code': rp.parcel_code, 'area_ha': _num(rp.area_ha),
                    'estimated_production_kg': _num(rp.estimated_production_kg, 1),
                    'polygons': len(by_parcel.get(rp.id, [])),
                    'polygon_area_ha': round(sum(p['area_hectares'] or 0 for p in by_parcel.get(rp.id, [])), 2)}
                   for rp in producer.registry_parcels.all()]
        mapped = list(Parcel.objects.filter(producer=producer).values('field_id', 'area_hectares', 'eudr_status'))
        return Response({
            'registry_parcels': parcels,
            'unassigned_polygons': [{'id': str(p['id']), 'code': p['code'], 'parcel_code': p['parcel_code'],
                                     'area_ha': _num(p['area_hectares']), 'source_file': p['source_file']}
                                    for p in by_parcel.get(None, [])],
            'mapped_parcels': [{'field_id': m['field_id'], 'area_ha': _num(m['area_hectares']),
                                'eudr_status': m['eudr_status']} for m in mapped],
        })


def _producer_cells(p):
    return {'code_producteur': p['field_id_base'], 'nom': p['last_name'], 'prenoms': p['first_name'],
            'genre': 'Femme' if p['gender'] == 'F' else 'Homme', 'telephone': p['phone'], 'village': p['village'],
            'section': p['section'], 'region': p['region']}


def _key_cells(p, parcel_code):
    """Code producteur et code parcelle en tête de ligne, puis l'identité du producteur."""
    return {'code_producteur': p['field_id_base'], 'code_parcelle': parcel_code, **_producer_cells(p)}


def _eudr(statuses):
    if not statuses:
        return ''
    if 'non_compliant' in statuses:
        return 'Non conforme'
    return 'Conforme' if all(s == 'compliant' for s in statuses) else 'En attente'


class RegistryExportView(APIView):
    permission_classes = [IsCooperativeOrAdmin]

    def get(self, request):
        coop = resolve_cooperative(request)
        if coop is None:
            return Response({'detail': 'Coopérative requise.'}, status=status.HTTP_400_BAD_REQUEST)
        producers = {p['id']: p for p in Producer.objects.filter(cooperative=coop).values(
            'id', 'field_id_base', 'last_name', 'first_name', 'gender', 'phone', 'village', 'section', 'region',
            'total_area_ha', 'estimated_production_kg')}
        poly_by_parcel = {r['registry_parcel_id']: r for r in LegacyParcel.objects.filter(
            cooperative=coop, registry_parcel__isnull=False).values('registry_parcel_id')
            .annotate(n=Count('id'), ha=Sum('area_hectares'))}
        poly_by_producer = {r['producer_id']: r for r in LegacyParcel.objects.filter(
            cooperative=coop, producer__isnull=False).values('producer_id').annotate(n=Count('id'), ha=Sum('area_hectares'))}
        eudr = defaultdict(list)
        for pid, st in Parcel.objects.filter(cooperative=coop).values_list('producer_id', 'eudr_status'):
            eudr[pid].append(st)
        rows, with_parcels = [], set()
        for rp in RegistryParcel.objects.filter(cooperative=coop).values(
                'id', 'producer_id', 'parcel_code', 'area_ha', 'estimated_production_kg').order_by('producer__field_id_base', 'parcel_code'):
            p = producers[rp['producer_id']]
            poly = poly_by_parcel.get(rp['id']) or {}
            with_parcels.add(rp['producer_id'])
            rows.append({**_key_cells(p, rp['parcel_code']), 'superficie_ha': _num(rp['area_ha']),
                         'production_estimee_kg': _num(rp['estimated_production_kg'], 1),
                         'polygone': 'Oui' if poly.get('n') else 'Non', 'nb_polygones': poly.get('n', 0),
                         'surface_polygones_ha': _num(poly.get('ha')), 'statut_eudr': _eudr(eudr.get(rp['producer_id']))})
        for pid, p in producers.items():
            if pid in with_parcels:
                continue
            poly = poly_by_producer.get(pid) or {}
            rows.append({**_key_cells(p, ''), 'superficie_ha': _num(p['total_area_ha']),
                         'production_estimee_kg': _num(p['estimated_production_kg'], 1),
                         'polygone': 'Oui' if poly.get('n') or eudr.get(pid) else 'Non', 'nb_polygones': poly.get('n', 0),
                         'surface_polygones_ha': _num(poly.get('ha')), 'statut_eudr': _eudr(eudr.get(pid))})
        rows.sort(key=lambda r: (r['code_producteur'] or '', r['code_parcelle'] or ''))
        unlinked = [{'code_lu': u['code'], 'code_parcelle_lu': u['parcel_code'], 'nom': u['name'],
                     'surface_ha': _num(u['area_hectares']), 'fichier': u['source_file']}
                    for u in LegacyParcel.objects.filter(cooperative=coop, producer__isnull=True)
                    .values('code', 'parcel_code', 'name', 'area_hectares', 'source_file')[:20000]]
        return Response({'cooperative': coop.name, 'rows': rows, 'unlinked_polygons': unlinked})
