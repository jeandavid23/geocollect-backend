"""Registre une ligne par parcelle : code producteur + code parcelle, rattachement des polygones, export normalisé."""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.cooperatives.models import Cooperative
from apps.parcels.models import LegacyParcel
from .models import Producer, RegistryParcel

H = {'HTTP_HOST': 'localhost'}
SQ = {'type': 'Polygon', 'coordinates': [[[-5, 7], [-4.998, 7], [-4.998, 7.002], [-5, 7.002], [-5, 7]]]}
EST = 'Total estimate production (current year)'


def row(code, parcel, area=None, estimate=None, **extra):
    data = {'code': code, 'parcel_code': parcel, 'last_name': 'KONAN', 'village': 'Akakro', **extra}
    if area is not None:
        data['total_area_ha'] = area
    data['extra_data'] = {'Code producteur': code, 'Code parcelle': parcel, **({EST: estimate} if estimate is not None else {})}
    return data


class RegistryParcelTests(TestCase):
    def setUp(self):
        self.coop = Cooperative.objects.create(name='Coop A', rccm='R1')
        self.other = Cooperative.objects.create(name='Coop B', rccm='R2')
        self.c = APIClient()
        self.c.force_authenticate(User.objects.create_user(username='c', password='x-123456789', role='cooperative',
                                                           cooperative=self.coop))

    def import_rows(self, rows, **body):
        r = self.c.post('/api/v1/producers/bulk/', {'use_codes': True, 'producers': rows, **body}, format='json', **H)
        assert r.status_code == 201, r.content
        return r.json()

    def import_polygons(self, features, **fields):
        r = self.c.post('/api/v1/parcels/legacy/import/', {'source_file': 'polygones.geojson', 'features': features, **fields},
                        format='json', **H)
        assert r.status_code == 201, r.content
        return r.json()

    def test_une_ligne_par_parcelle(self):
        d = self.import_rows([row('BEO-001', 'BEO-001-P1', 2.1, 1.2), row('BEO-001', 'BEO-001-P2', 1.4, 0.8),
                              row('BEO-002', 'BEO-002-P1', 3.0, 1.9)])
        self.assertEqual(len(d['created']), 2)                                         # deux producteurs
        p = Producer.objects.get(field_id_base='BEO-001')
        self.assertEqual(float(p.total_area_ha), 3.5)                                  # somme des parcelles
        self.assertEqual(float(p.estimated_production_kg), 2000.0)                     # 1,2 t + 0,8 t
        self.assertEqual(sorted(p.registry_parcels.values_list('parcel_code', flat=True)), ['BEO-001-P1', 'BEO-001-P2'])
        self.assertEqual(float(RegistryParcel.objects.get(parcel_code='BEO-001-P2').area_ha), 1.4)
        listed = {x['field_id_base']: x['registry_parcel_count'] for x in self.c.get('/api/v1/producers/', **H).json()['results']}
        self.assertEqual(listed, {'BEO-001': 2, 'BEO-002': 1})

    def test_total_producteur_repete_sur_chaque_ligne(self):
        self.import_rows([row('BEO-001', 'BEO-001-P1', 2, 3.5), row('BEO-001', 'BEO-001-P2', 1, 3.5)])
        self.assertEqual(float(Producer.objects.get(field_id_base='BEO-001').estimated_production_kg), 3500.0)

    def test_doublons_signales(self):
        d = self.import_rows([row('BEO-001', 'BEO-001-P1'), row('BEO-002', 'BEO-001-P1'), row('BEO-003', ''), row('BEO-003', '')])
        msgs = [list(e['errors'].values())[0] for e in d['errors']]
        self.assertTrue(any('Code parcelle BEO-001-P1 en double' in m for m in msgs), msgs)
        self.assertTrue(any('Code BEO-003 en double' in m for m in msgs), msgs)
        self.assertEqual(RegistryParcel.objects.count(), 1)

    def test_reimport_met_a_jour_la_parcelle(self):
        self.import_rows([row('BEO-001', 'BEO-001-P1', 2)])
        self.import_rows([row('BEO-001', 'BEO-001-P1', 2.6)])
        self.assertEqual((RegistryParcel.objects.count(), float(RegistryParcel.objects.get().area_ha)), (1, 2.6))

    def test_polygones_rattaches_par_code_parcelle(self):
        self.import_rows([row('BEO-001', 'BEO-001-P1'), row('BEO-001', 'BEO-001-P2'), row('BEO-002', 'BEO-002-P1')])
        feats = [{'geometry': SQ, 'properties': {'CODE_PROD': 'BEO-001', 'CODE_PARC': 'BEO-001-P2'}},
                 {'geometry': SQ, 'properties': {'CODE_PROD': 'BEO-002', 'CODE_PARC': 'INCONNU-9'}},   # parcelle absente
                 {'geometry': SQ, 'properties': {'CODE_PROD': '', 'CODE_PARC': 'beo_001_p1'}},          # écrit autrement
                 {'geometry': SQ, 'properties': {'CODE_PROD': 'XXX', 'CODE_PARC': ''}}]
        d = self.import_polygons(feats, code_field='CODE_PROD', parcel_field='CODE_PARC')
        self.assertEqual((d['linked'], d['linked_to_parcel']), (3, 2))
        p2 = LegacyParcel.objects.get(parcel_code='BEO-001-P2')
        self.assertEqual((p2.registry_parcel.parcel_code, p2.producer.field_id_base), ('BEO-001-P2', 'BEO-001'))
        orphan = LegacyParcel.objects.get(parcel_code='INCONNU-9')                       # rattaché au producteur seul
        self.assertEqual((orphan.registry_parcel, orphan.producer.field_id_base), (None, 'BEO-002'))
        detail = self.c.get(f'/api/v1/producers/{Producer.objects.get(field_id_base="BEO-002").id}/parcels/', **H).json()
        self.assertEqual(detail['registry_parcels'][0]['polygons'], 0)
        self.assertEqual([u['parcel_code'] for u in detail['unassigned_polygons']], ['INCONNU-9'])

    def test_polygones_importes_avant_le_registre(self):
        self.import_polygons([{'geometry': SQ, 'properties': {'CODE_PARC': 'BEO-001-P2'}}], parcel_field='CODE_PARC')
        self.assertIsNone(LegacyParcel.objects.get().producer)
        self.import_rows([row('BEO-001', 'BEO-001-P1'), row('BEO-001', 'BEO-001-P2')])       # l'import relie ensuite
        lp = LegacyParcel.objects.get()
        self.assertEqual((lp.registry_parcel.parcel_code, lp.producer.field_id_base), ('BEO-001-P2', 'BEO-001'))

    def test_export_du_registre_normalise(self):
        self.import_rows([row('BEO-001', 'BEO-001-P1', 2.1, 1.2), row('BEO-001', 'BEO-001-P2', 1.4, 0.8),
                          {'code': 'BEO-009', 'last_name': 'SANS', 'extra_data': {}}])
        self.import_polygons([{'geometry': SQ, 'properties': {'P': 'BEO-001-P1'}},
                              {'geometry': SQ, 'properties': {'P': 'ZZZ-1'}}], parcel_field='P')
        Producer.objects.create(cooperative=self.other, field_id_base='AUTRE-1', last_name='X')
        d = self.c.get('/api/v1/producers/registry-export/', **H).json()
        rows = [(r['code_producteur'], r['code_parcelle'], r['polygone']) for r in d['rows']]
        self.assertEqual(rows, [('BEO-001', 'BEO-001-P1', 'Oui'), ('BEO-001', 'BEO-001-P2', 'Non'), ('BEO-009', '', 'Non')])
        self.assertEqual(d['rows'][0]['production_estimee_kg'], 1200.0)
        self.assertEqual([u['code_parcelle_lu'] for u in d['unlinked_polygons']], ['ZZZ-1'])
