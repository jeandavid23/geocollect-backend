"""Analyse d'activité : toutes les parcelles (cartographiées + anciens polygones), volumes, chaîne jusqu'à l'usine."""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.cooperatives.models import Cooperative
from apps.lots.tests import SQ
from apps.parcels.models import LegacyParcel, Parcel
from apps.producers.models import Producer

H = {'HTTP_HOST': 'localhost'}
URL = '/api/v1/dashboard/analytics/?campaign=2026-2027'


class AnalyticsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='sa', password='x-123456789', role='super_admin')
        self.coop = Cooperative.objects.create(name='Coop A', rccm='R1', managed_by=self.admin)
        self.other = Cooperative.objects.create(name='Coop B', rccm='R2', managed_by=self.admin)
        self.stranger = Cooperative.objects.create(name='Hors client', rccm='R3')
        self.user = User.objects.create_user(username='c', password='x-123456789', role='cooperative', cooperative=self.coop)
        self.c = APIClient()
        self.c.force_authenticate(self.user)
        self.p1 = Producer.objects.create(cooperative=self.coop, field_id_base='P1', last_name='KONAN', village='Akakro')
        self.p2 = Producer.objects.create(cooperative=self.coop, field_id_base='P2', last_name='YAO', village='Broukro')
        self.p3 = Producer.objects.create(cooperative=self.coop, field_id_base='P3', last_name='SANS')     # aucune parcelle
        Parcel.objects.create(producer=self.p1, cooperative=self.coop, field_id='P1-1', geometry=SQ, village='Akakro',
                              area_hectares=2.0, eudr_status='compliant')
        for i, area in enumerate((1.5, 2.5, 4.0)):                       # 3 anciens polygones, dont 2 pour P2
            LegacyParcel.objects.create(cooperative=self.coop, producer=self.p2 if i < 2 else None, code=f'L{i}',
                                        geometry=SQ, area_hectares=area)
        LegacyParcel.objects.create(cooperative=self.other, code='X', geometry=SQ, area_hectares=9)
        LegacyParcel.objects.create(cooperative=self.stranger, code='Y', geometry=SQ, area_hectares=99)

    def weigh(self, producer, gross, bags=10, date='2026-11-02', campaign='2026-2027', price=2800):
        r = self.c.post('/api/v1/lots/deliveries/', {'producer': str(producer.id), 'delivery_date': date, 'campaign': campaign,
                                                      'product': 'cacao', 'bags': bags, 'gross_kg': gross, 'unit_price': price},
                        format='json', **H)
        assert r.status_code == 201, r.content
        return r.json()

    def test_anciens_polygones_comptes_comme_parcelles(self):
        d = self.c.get(URL, **H).json()
        self.assertEqual(d['parcels']['total'], 4)                      # 1 cartographiée + 3 anciennes
        self.assertEqual((d['parcels']['mapped'], d['parcels']['legacy']), (1, 3))
        self.assertEqual(d['parcels']['hectares'], 10.0)
        self.assertEqual(d['producers']['total'], 3)
        self.assertEqual(d['producers']['with_parcels'], 2)             # P2 n'a que des anciens polygones : couvert
        self.assertEqual(d['producers']['coverage_pct'], 66.7)

    def test_volumes_et_chaine_jusqu_a_l_usine(self):
        a = self.weigh(self.p1, 1010)                                    # 1 000 kg net (novembre)
        self.weigh(self.p2, 505, bags=5, date='2026-12-03')             # 500 kg net (décembre)
        self.weigh(self.p1, 202, bags=2, date='2025-11-10', campaign='2025-2026')   # campagne précédente
        lot = self.c.post('/api/v1/lots/', {'campaign': '2026-2027', 'product': 'cacao', 'lot_date': '2026-11-05',
                                            'deliveries': [a['id']]}, format='json', **H).json()
        self.c.patch(f'/api/v1/lots/{lot["id"]}/', {'status': 'valide'}, format='json', **H)
        slip = self.c.post('/api/v1/lots/transport/', {'lot': lot['id'], 'destination': 'Usine', 'vehicle_plate': 'AB',
                                                        'driver_name': 'T', 'gross_weight_kg': 1010}, format='json', **H).json()
        self.c.patch(f'/api/v1/lots/transport/{slip["id"]}/', {'received_weight_kg': 1005}, format='json', **H)
        self.c.post('/api/v1/lots/processing/', {'operation': 'usinage', 'plant': 'Usine', 'process_date': '2026-11-20',
                                                 'inputs': [{'lot': lot['id'], 'weight_kg': 1000}],
                                                 'outputs': [{'product': 'feves_usinees', 'weight_kg': 985}]}, format='json', **H)
        d = self.c.get(URL, **H).json()
        v = d['volumes']
        self.assertEqual((v['net_kg'], v['deliveries'], v['producers_delivering']), (1500.0, 2, 2))
        self.assertEqual(v['amount_fcfa'], 4200000.0)
        self.assertEqual(v['avg_price'], 2800.0)
        self.assertEqual(v['previous_net_kg'], 200.0)
        self.assertEqual(v['growth_pct'], 650.0)
        self.assertEqual([(m['month'], m['net_kg']) for m in d['monthly']], [('2026-11', 1000.0), ('2026-12', 500.0)])
        p = d['pipeline']
        self.assertEqual((p['weighed_kg'], p['in_lots_kg'], p['shipped_kg'], p['received_kg'], p['processed_kg']),
                         (1500.0, 1000.0, 1000.0, 1005.0, 1000.0))
        self.assertEqual(d['top_producers'][0]['code'], 'P1')
        self.assertEqual(d['lots']['expedie'], 1)

    def test_cloisonnement_et_vue_client(self):
        sa = APIClient()
        sa.force_authenticate(self.admin)
        d = sa.get(URL, **H).json()
        self.assertEqual(d['parcels']['total'], 5)                      # ses 2 coopératives, pas la coopérative étrangère
        self.assertEqual(sorted(r['name'] for r in d['per_cooperative']), ['Coop A', 'Coop B'])
        one = sa.get(URL + f'&cooperative={self.other.id}', **H).json()
        self.assertEqual(one['parcels']['total'], 1)
        self.assertNotIn('per_cooperative', self.c.get(URL, **H).json())

    def test_nombre_de_requetes_constant(self):
        def count():
            with CaptureQueriesContext(connection) as q:
                self.c.get(URL, **H)
            return len(q)
        self.weigh(self.p1, 1010)
        few = count()
        for i in range(10):
            p = Producer.objects.create(cooperative=self.coop, field_id_base=f'N{i}', last_name='N')
            LegacyParcel.objects.create(cooperative=self.coop, producer=p, code=f'N{i}', geometry=SQ, area_hectares=1)
            self.weigh(p, 505, bags=5)
        self.assertEqual(count(), few)

    def test_tableau_de_bord_existant_compte_aussi_les_anciens(self):
        d = self.c.get('/api/v1/dashboard/coop/', **H).json()
        self.assertEqual(d['total_parcels'], 4)
        self.assertEqual(d['total_hectares'], 10.0)

    def test_parametres_invalides(self):
        self.assertEqual(self.c.get('/api/v1/dashboard/analytics/?campaign=abc', **H).status_code, 400)
        sa = APIClient()
        sa.force_authenticate(self.admin)
        self.assertEqual(sa.get(URL + '&cooperative=pas-un-uuid', **H).status_code, 400)
