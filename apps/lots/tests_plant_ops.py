"""Usine : contrôle qualité à la réception, alertes, opérateur de pesée, mode ségrégé, stock, expéditions avec DDS."""
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import Notification, User
from apps.parcels.models import Parcel
from .models import Lot
from .tests import H
from .tests_plant import PlantBase

SLIP = '/api/v1/lots/transport/{}/'
BATCH = '/api/v1/lots/processing/'
SHIP = '/api/v1/lots/plant/shipments/'


class PlantOpsTests(PlantBase):
    def receive(self, slip, client=None, **body):
        return (client or self.u).patch(SLIP.format(slip['id']), {'received_weight_kg': 1006, **body}, format='json', **H)

    def received_lot(self):
        lot, slip = self.ship()
        r = self.receive(slip)
        assert r.status_code == 200, r.content
        return lot, slip

    def process(self, lot, out=985, weight=1000, client=None):
        return (client or self.u).post(BATCH, {'operation': 'usinage', 'process_date': '2026-11-20',
                                              'inputs': [{'lot': lot['id'], 'weight_kg': weight}],
                                              'outputs': [{'product': 'feves_usinees', 'weight_kg': out},
                                                          {'product': 'dechets', 'weight_kg': 10}]}, format='json', **H)

    def titles(self, user):
        return list(Notification.objects.filter(recipient=user).values_list('title', flat=True))

    # ── Réception ──────────────────────────────────────────────────────────────────────────────────────
    def test_controle_qualite_et_grade(self):
        _, slip = self.ship()
        r = self.receive(slip, moisture_pct=7.5, bean_count=98, mouldy_pct=2, slaty_pct=4, insect_pct=1, germinated_pct=1)
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d['quality'], {'grade': 'Grade II', 'issues': []})        # ardoisées 4 % > 3 % : grade II
        self.assertEqual((d['decision'], d['status']), ('accepte', 'livree'))
        self.assertAlmostEqual(d['weight_gap_pct'], -0.4)

    def test_hors_norme_signale(self):
        _, slip = self.ship()
        q = self.receive(slip, moisture_pct=9, mouldy_pct=5, slaty_pct=1, decision='reserve',
                         decision_reason='Humidité élevée').json()['quality']
        self.assertEqual(q['grade'], 'Hors standard')
        self.assertEqual(len(q['issues']), 2)

    def test_reserves_et_refus_exigent_un_motif(self):
        _, slip = self.ship()
        r = self.receive(slip, decision='reserve')
        self.assertEqual(r.status_code, 400)
        self.assertIn('motif', r.json()['detail'])

    def test_refus_renvoie_le_lot_a_la_cooperative(self):
        lot, slip = self.ship()
        r = self.u.patch(SLIP.format(slip['id']), {'decision': 'refuse', 'decision_reason': 'Fèves moisies'},
                         format='json', **H)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], 'refusee')
        self.assertEqual(Lot.objects.get(pk=lot['id']).status, 'valide')           # réexpédiable
        self.assertIn(f'Lot {lot["code"]} refusé à Usine SACO San Pedro', self.titles(self.user))
        self.assertEqual(self.u.get('/api/v1/lots/processing/available/', **H).json()['lots'], [])

    def test_refus_impossible_apres_usinage(self):
        lot, slip = self.ship()
        self.receive(slip)
        self.assertEqual(self.process(lot).status_code, 201)
        _, second = self.ship()
        Lot.objects.filter(pk=second['lot']).update(status='expedie')
        # la première fiche est close ; on vérifie la règle sur une fiche du même lot
        slip2 = self.c.post('/api/v1/lots/transport/', {'lot': lot['id'], 'vehicle_plate': 'CD', 'driver_name': 'T',
                                                       'gross_weight_kg': 10, 'plant': str(self.plant.id)},
                            format='json', **H).json()
        r = self.u.patch(SLIP.format(slip2['id']), {'decision': 'refuse', 'decision_reason': 'x'}, format='json', **H)
        self.assertEqual(r.status_code, 400)

    def test_la_cooperative_ne_saisit_pas_la_qualite(self):
        d = self.weigh(self.p1, gross=1010, bags=10).json()
        lot = self.lot_from([d['id']]).json()
        self.c.patch(f'/api/v1/lots/{lot["id"]}/', {'status': 'valide'}, format='json', **H)
        slip = self.c.post('/api/v1/lots/transport/', {'lot': lot['id'], 'vehicle_plate': 'AB', 'driver_name': 'T',
                                                      'gross_weight_kg': 1010, 'plant': str(self.plant.id),
                                                      'decision': 'accepte', 'mouldy_pct': 1}, format='json', **H).json()
        self.assertEqual((slip['decision'], slip['mouldy_pct']), ('', None))

    # ── Alertes et notifications ───────────────────────────────────────────────────────────────────────
    def test_camion_annonce_a_l_usine(self):
        _, slip = self.ship()
        self.assertIn(f'Camion en route : {slip["number"]}', self.titles(self.factory_user))

    def test_alerte_ecart_de_poids(self):
        _, slip = self.ship()
        self.receive(slip, received_weight_kg=980)                                  # -2,97 % > 1 %
        self.assertTrue(any(t.startswith('Écart de poids') for t in self.titles(self.factory_user)))
        self.assertTrue(any('reçu' in t for t in self.titles(self.user)))

    def test_alerte_perte_selon_le_seuil_de_l_usine(self):
        self.sa.patch(f'/api/v1/lots/plants/{self.plant.id}/', {'loss_alert_pct': 5}, format='json', **H)
        lot, _ = self.received_lot()
        r = self.process(lot, out=900)                                              # 9 % de perte
        self.assertTrue(r.json()['loss_warning'])
        self.assertTrue(any(t.startswith('Perte anormale') for t in self.titles(self.factory_user)))

    def test_reglages_valides(self):
        r = self.sa.patch(f'/api/v1/lots/plants/{self.plant.id}/', {'gap_alert_pct': 80}, format='json', **H)
        self.assertEqual(r.status_code, 400)
        r = self.sa.patch(f'/api/v1/lots/plants/{self.plant.id}/', {'traceability_mode': 'segregue', 'gap_alert_pct': 0.5},
                          format='json', **H)
        self.assertEqual((r.json()['traceability_mode'], r.json()['gap_alert_pct']), ('segregue', 0.5))

    # ── Comptes de l'usine ─────────────────────────────────────────────────────────────────────────────
    def test_operateur_de_pesee(self):
        r = self.sa.post(f'/api/v1/lots/plants/{self.plant.id}/account/', {'username': 'peseur', 'plant_role': 'pesee'},
                         format='json', **H)
        self.assertEqual(r.json()['plant_role'], 'pesee')
        op = APIClient()
        op.force_authenticate(User.objects.get(username='peseur'))
        lot, slip = self.ship()
        self.assertEqual(self.receive(slip, client=op).status_code, 200)
        self.assertEqual(self.process(lot, client=op).status_code, 403)
        self.assertEqual(op.post(SHIP, {}, format='json', **H).status_code, 403)
        r = self.sa.patch(f'/api/v1/lots/plants/{self.plant.id}/account/', {'user': r.json()['id'], 'plant_role': 'responsable'},
                          format='json', **H)
        self.assertEqual(r.json()['plant_role'], 'responsable')

    # ── Mode de traçabilité ────────────────────────────────────────────────────────────────────────────
    def test_bilan_de_masse_compte_l_origine_verifiee(self):
        lot, _ = self.received_lot()
        self.assertEqual(self.process(lot).json()['compliant_share_pct'], 100.0)

    def test_mode_segregue_refuse_l_origine_non_verifiee(self):
        self.sa.patch(f'/api/v1/lots/plants/{self.plant.id}/', {'traceability_mode': 'segregue'}, format='json', **H)
        lot, _ = self.received_lot()
        Parcel.objects.filter(producer=self.p1).update(eudr_status='pending')    # réévaluée après la validation du lot
        r = self.process(lot)
        self.assertEqual(r.status_code, 400)
        self.assertIn('ségrégé', r.json()['detail'])
        Parcel.objects.filter(producer=self.p1).update(eudr_status='compliant')
        self.assertEqual(self.process(lot).status_code, 201)

    # ── Stock et expéditions ───────────────────────────────────────────────────────────────────────────
    def test_stock(self):
        self.ship()                                                                 # en route
        lot, _ = self.received_lot()
        self.process(lot, weight=600, out=590)
        s = self.u.get('/api/v1/lots/plant/stock/', **H).json()
        self.assertEqual(s['totals'], {'on_the_way_kg': 1010.0, 'raw_kg': 400.0, 'finished_kg': 590.0})
        self.assertEqual(self.sa.get(f'/api/v1/lots/plant/stock/?plant={self.plant.id}', **H).json()['totals'], s['totals'])
        self.assertEqual(self.c.get('/api/v1/lots/plant/stock/', **H).status_code, 404)

    def test_expedition_avec_dds(self):
        lot, _ = self.received_lot()
        batch = self.process(lot).json()
        avail = self.u.get(SHIP + 'shippable/', **H).json()
        self.assertEqual([(a['product'], a['available_kg']) for a in avail], [('feves_usinees', 985.0)])
        body = {'ship_date': '2026-12-01', 'client': 'Cargill', 'destination_country': 'Pays-Bas', 'container': 'MSKU1234567',
                'items': [{'batch': batch['id'], 'product': 'feves_usinees', 'weight_kg': 1000}]}
        self.assertEqual(self.u.post(SHIP, body, format='json', **H).status_code, 400)       # plus que le stock
        body['items'][0]['weight_kg'] = 900
        ship = self.u.post(SHIP, body, format='json', **H).json()
        self.assertRegex(ship['number'], r'^EXP-2026-0001$')
        self.assertEqual(self.u.get(SHIP + 'shippable/', **H).json()[0]['available_kg'], 85.0)
        r = self.u.patch(f'{SHIP}{ship["id"]}/', {'status': 'expediee'}, format='json', **H)
        self.assertEqual(r.status_code, 400)
        self.assertIn('DDS', r.json()['detail'])
        r = self.u.patch(f'{SHIP}{ship["id"]}/', {'status': 'expediee', 'dds_reference': '26CIABC123456'}, format='json', **H)
        self.assertEqual(r.json()['status'], 'expediee')
        self.assertEqual(self.u.patch(f'{SHIP}{ship["id"]}/', {'client': 'X'}, format='json', **H).status_code, 400)
        trace = self.u.get(f'{SHIP}{ship["id"]}/trace/', **H).json()
        self.assertEqual(trace['producers'][0]['code'], 'P1')
        # un produit expédié ne peut plus être transformé
        r = self.u.post(BATCH, {'operation': 'transformation', 'process_date': '2026-12-02',
                                'inputs': [{'batch': batch['id'], 'weight_kg': 100}],
                                'outputs': [{'product': 'masse', 'weight_kg': 80}]}, format='json', **H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.sa.get(SHIP, **H).json()[0]['number'], ship['number'])
        self.assertEqual([x['number'] for x in self.c.get(SHIP, **H).json()], [ship['number']])     # la coopérative la voit

    def test_tableau_de_bord(self):
        lot, _ = self.received_lot()
        _, slip = self.ship()
        self.receive(slip, decision='refuse', decision_reason='Moisies', received_weight_kg='')
        self.process(lot)
        d = self.u.get('/api/v1/lots/plant/dashboard/', **H).json()
        r = d['receptions']
        self.assertEqual((r['trucks'], r['refused'], r['received_kg']), (2, 1, 1006.0))
        self.assertAlmostEqual(r['gap_pct'], -0.4)                                # le camion refusé non pesé est exclu
        self.assertEqual(d['by_cooperative'], [{'name': 'Coop Akonan', 'received_kg': 1006.0, 'trucks': 1}])
        self.assertEqual(d['processing']['usinage']['loss_pct'], 0.5)
        self.assertEqual(len(d['monthly']), 12)

    def test_requetes_constantes(self):
        def count(url):
            with CaptureQueriesContext(connection) as q:
                self.u.get(url, **H)
            return len(q)
        lot, _ = self.received_lot()
        self.process(lot, weight=500, out=490)
        urls = ('/api/v1/lots/plant/stock/', '/api/v1/lots/plant/dashboard/', SHIP + 'shippable/')
        few = [count(u) for u in urls]
        for _ in range(3):
            lot, _ = self.received_lot()
            self.process(lot, weight=500, out=490)
        self.assertEqual([count(u) for u in urls], few)

    def test_suppression_du_client_avec_expeditions(self):
        from apps.accounts.purge import purge
        from .models import Plant, Shipment
        lot, _ = self.received_lot()
        batch = self.process(lot).json()
        self.u.post(SHIP, {'ship_date': '2026-12-01', 'client': 'C', 'items': [
            {'batch': batch['id'], 'product': 'feves_usinees', 'weight_kg': 500}]}, format='json', **H)
        purge(self.admin)
        self.assertFalse(Plant.objects.exists())
        self.assertFalse(Shipment.objects.exists())


class PlantVisibilityTests(PlantOpsTests.__bases__[0]):
    """Propriétaire, super admin et coopérative voient les données de l'usine ; la coopérative, seulement les siennes."""

    def setUp(self):
        super().setUp()
        from apps.parcels.models import Parcel as P
        from .tests import SQ
        self.other.managed_by = self.admin
        self.other.save()
        self.plant.cooperatives.add(self.other)
        P.objects.create(producer=self.foreign, cooperative=self.other, field_id='X1-1', geometry=SQ, area_hectares=2,
                         eudr_status='compliant')
        self.other_user = User.objects.create_user(username='c2', password='x-123456789', role='cooperative', cooperative=self.other)
        self.c2 = APIClient()
        self.c2.force_authenticate(self.other_user)
        self.owner = APIClient()
        self.owner.force_authenticate(User.objects.create_user(username='own', password='x-123456789', role='owner'))

    def ship_from(self, client, producer):
        d = client.post('/api/v1/lots/deliveries/', {'producer': str(producer.id), 'delivery_date': '2026-11-02', 'campaign': '2026-2027',
                                                      'product': 'cacao', 'bags': 10, 'gross_kg': 1010}, format='json', **H).json()
        lot = client.post('/api/v1/lots/', {'campaign': '2026-2027', 'product': 'cacao', 'lot_date': '2026-11-03',
                                            'deliveries': [d['id']]}, format='json', **H).json()
        client.patch(f'/api/v1/lots/{lot["id"]}/', {'status': 'valide'}, format='json', **H)
        slip = client.post('/api/v1/lots/transport/', {'lot': lot['id'], 'vehicle_plate': 'AB', 'driver_name': 'T', 'bags': 10,
                                                      'gross_weight_kg': 1010, 'plant': str(self.plant.id)}, format='json', **H).json()
        r = self.u.patch(SLIP.format(slip['id']), {'received_weight_kg': 1006}, format='json', **H)
        assert r.status_code == 200, r.content
        return lot

    def test_chaque_role_voit_les_donnees_de_l_usine(self):
        mine, theirs = self.ship_from(self.c, self.p1), self.ship_from(self.c2, self.foreign)
        for lot in (mine, theirs):
            b = self.u.post(BATCH, {'operation': 'usinage', 'process_date': '2026-11-20', 'inputs': [{'lot': lot['id'], 'weight_kg': 1000}],
                                    'outputs': [{'product': 'feves_usinees', 'weight_kg': 980}]}, format='json', **H).json()
            self.u.post(SHIP, {'ship_date': '2026-12-01', 'client': lot['code'], 'items': [
                {'batch': b['id'], 'product': 'feves_usinees', 'weight_kg': 500}]}, format='json', **H)
        q = f'?plant={self.plant.id}'
        full = self.owner.get('/api/v1/lots/plant/dashboard/' + q, **H).json()
        self.assertEqual(full['receptions']['received_kg'], 2012.0)
        self.assertEqual(self.sa.get('/api/v1/lots/plant/dashboard/' + q, **H).json()['receptions'], full['receptions'])
        self.assertEqual(len(self.owner.get(SHIP, **H).json()), 2)
        # la coopérative : ses réceptions, son usinage, ses expéditions — rien de l'autre coopérative
        d = self.c.get('/api/v1/lots/plant/dashboard/' + q, **H).json()
        self.assertEqual((d['receptions']['received_kg'], [c['name'] for c in d['by_cooperative']]), (1006.0, ['Coop Akonan']))
        self.assertEqual(d['processing']['usinage']['batches'], 1)
        self.assertEqual(self.c.get('/api/v1/lots/plant/stock/' + q, **H).json()['totals']['finished_kg'], 480.0)
        self.assertEqual([s['client'] for s in self.c.get(SHIP, **H).json()], [mine['code']])
        self.assertEqual([s['lot_code'] for s in self.c.get('/api/v1/lots/transport/' + q, **H).json()], [mine['code']])
        self.assertEqual(len(self.c.get(BATCH + q, **H).json()), 1)
        self.assertEqual(len(self.sa.get(BATCH + q, **H).json()), 2)
        # consultation seulement
        self.assertEqual(self.c.post(SHIP, {'plant': str(self.plant.id)}, format='json', **H).status_code, 403)
        self.assertEqual(self.c.get(SHIP + 'shippable/' + q, **H).status_code, 403)
        self.assertEqual(self.c.get('/api/v1/lots/transport/?plant=xx', **H).json(), [])

    def test_cooperative_hors_usine(self):
        self.plant.cooperatives.remove(self.other)
        r = self.c2.get(f'/api/v1/lots/plant/dashboard/?plant={self.plant.id}', **H)
        self.assertEqual(r.status_code, 404)
