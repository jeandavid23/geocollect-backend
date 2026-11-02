"""Traçabilité des volumes : pesées, lots constitués à partir des pesées, volumes par producteur, fiches d'accompagnement."""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.cooperatives.models import Cooperative
from apps.parcels.models import Parcel
from apps.producers.models import Producer
from .models import Delivery, Lot, TransportSlip

H = {'HTTP_HOST': 'localhost'}
SQ = {'type': 'Polygon', 'coordinates': [[[-5, 7], [-4.99, 7], [-4.99, 7.01], [-5, 7.01], [-5, 7]]]}


class TraceBase(TestCase):
    """Coopérative, deux producteurs avec une parcelle conforme de 2 ha chacun, et les raccourcis pesée / lot."""

    def setUp(self):
        self.coop = Cooperative.objects.create(name='Coop Akonan', rccm='RC1')
        self.other = Cooperative.objects.create(name='Autre', rccm='RC2')
        self.user = User.objects.create_user(username='c', password='x-123456789', role='cooperative', cooperative=self.coop)
        self.c = APIClient()
        self.c.force_authenticate(self.user)
        self.p1 = Producer.objects.create(cooperative=self.coop, field_id_base='P1', last_name='KONAN', first_name='Ama', village='Niamoue')
        self.p2 = Producer.objects.create(cooperative=self.coop, field_id_base='P2', last_name='YAO', village='Bonoua')
        self.foreign = Producer.objects.create(cooperative=self.other, field_id_base='X1', last_name='Z')
        for p in (self.p1, self.p2):
            Parcel.objects.create(producer=p, cooperative=self.coop, field_id=f'{p.field_id_base}-1', geometry=SQ,
                                  village=p.village, area_hectares=2.0, eudr_status='compliant')

    def weigh(self, producer, gross=520, bags=8, tare=None, deduction=0, **extra):
        body = {'producer': str(producer.id), 'delivery_date': '2026-11-02', 'campaign': '2026-2027', 'product': 'cacao',
                'bags': bags, 'gross_kg': gross, 'deduction_kg': deduction, 'unit_price': 2800, 'weigher': 'Kouadio', **extra}
        if tare is not None:
            body['tare_kg'] = tare
        return self.c.post('/api/v1/lots/deliveries/', body, format='json', **H)

    def lot_from(self, ids, **extra):
        body = {'campaign': '2026-2027', 'product': 'cacao', 'lot_date': '2026-11-03', 'deliveries': ids, **extra}
        return self.c.post('/api/v1/lots/', body, format='json', **H)


class TraceabilityTests(TraceBase):
    # ── Pesées ─────────────────────────────────────────────────────────────────────────────────────
    def test_pesee_poids_net_montant_et_numero(self):
        r = self.weigh(self.p1, gross=520, bags=8, tare=8, deduction=12)
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual(float(d['net_kg']), 500.0)                       # 520 - 8 (sacs) - 12 (humidité, déchets)
        self.assertEqual(float(d['amount']), 1400000.0)                   # 500 kg × 2 800 FCFA
        self.assertRegex(d['number'], r'^PES-COOPAK-2026-\d{5}$')
        self.assertEqual(d['producer_name'], 'KONAN Ama')
        second = self.weigh(self.p1).json()['number']
        self.assertEqual(int(second[-5:]), int(d['number'][-5:]) + 1)

    def test_tare_par_defaut_un_kg_par_sac(self):
        d = self.weigh(self.p1, gross=520, bags=8).json()
        self.assertEqual(float(d['tare_kg']), 8.0)
        self.assertEqual(float(d['net_kg']), 512.0)

    def test_pesee_refusee(self):
        self.assertEqual(self.weigh(self.foreign).status_code, 400)       # producteur d'une autre coopérative
        self.assertEqual(self.weigh(self.p1, gross=10, bags=8, tare=8, deduction=5).status_code, 400)   # net ≤ 0

    def test_annulation_tracee_jamais_de_suppression(self):
        d = self.weigh(self.p1).json()
        url = f'/api/v1/lots/deliveries/{d["id"]}/'
        self.assertEqual(self.c.delete(url, **H).status_code, 405)
        self.assertEqual(self.c.post(url + 'cancel/', {}, format='json', **H).status_code, 400)   # motif obligatoire
        r = self.c.post(url + 'cancel/', {'reason': 'Erreur de producteur'}, format='json', **H)
        self.assertEqual(r.json()['status'], 'annulee')
        self.assertEqual(r.json()['cancel_reason'], 'Erreur de producteur')
        self.assertTrue(Delivery.objects.filter(pk=d['id']).exists())

    def test_cloisonnement(self):
        d = self.weigh(self.p1).json()
        u2 = User.objects.create_user(username='d', password='x-123456789', role='cooperative', cooperative=self.other)
        c2 = APIClient()
        c2.force_authenticate(u2)
        self.assertEqual(c2.get(f'/api/v1/lots/deliveries/{d["id"]}/', **H).status_code, 404)
        self.assertEqual(c2.get('/api/v1/lots/deliveries/', **H).json()['count'], 0)

    # ── Lots constitués à partir des pesées ────────────────────────────────────────────────────────
    def test_lot_a_partir_des_pesees(self):
        a = self.weigh(self.p1, gross=520, bags=8).json()
        b = self.weigh(self.p1, gross=310, bags=5).json()
        c = self.weigh(self.p2, gross=205, bags=3).json()
        r = self.lot_from([a['id'], b['id'], c['id']])
        self.assertEqual(r.status_code, 201, r.content)
        lot = r.json()
        lines = {l['producer_code']: l for l in lot['lines']}
        self.assertEqual(float(lines['P1']['weight_kg']), 512 + 305)          # pesées regroupées par producteur
        self.assertEqual(lines['P1']['bags'], 13)
        self.assertIn(a['number'], lines['P1']['receipt'])
        self.assertEqual(lot['bags'], 16)
        self.assertEqual(Delivery.objects.filter(lot_id=lot['id']).count(), 3)
        self.assertEqual(lot['delivery_count'], 3)
        listed = self.c.get(f'/api/v1/lots/deliveries/?lot={lot["id"]}', **H).json()
        self.assertEqual(listed['count'], 3)
        self.assertEqual(self.c.get('/api/v1/lots/deliveries/?free=1', **H).json()['count'], 0)

    def test_pesee_deja_dans_un_lot_ou_annulee_refusee(self):
        a = self.weigh(self.p1).json()
        self.assertEqual(self.lot_from([a['id']]).status_code, 201)
        self.assertEqual(self.lot_from([a['id']]).status_code, 400)            # déjà dans un lot
        b = self.weigh(self.p2).json()
        self.c.post(f'/api/v1/lots/deliveries/{b["id"]}/cancel/', {'reason': 'doublon'}, format='json', **H)
        self.assertEqual(self.lot_from([b['id']]).status_code, 400)            # annulée
        cafe = self.weigh(self.p2, product='cafe').json()
        self.assertEqual(self.lot_from([cafe['id']]).status_code, 400)         # autre produit que le lot

    def test_pesee_dans_un_lot_non_annulable_et_retrait(self):
        a = self.weigh(self.p1).json()
        b = self.weigh(self.p2).json()
        lot = self.lot_from([a['id'], b['id']]).json()
        self.assertEqual(self.c.post(f'/api/v1/lots/deliveries/{a["id"]}/cancel/', {'reason': 'x'}, format='json', **H)
                         .status_code, 400)
        r = self.c.patch(f'/api/v1/lots/{lot["id"]}/', {'deliveries': [b['id']]}, format='json', **H)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([l['producer_code'] for l in r.json()['lines']], ['P2'])
        self.assertIsNone(Delivery.objects.get(pk=a['id']).lot_id)            # libérée, réutilisable

    def test_lot_manuel_toujours_possible(self):
        r = self.c.post('/api/v1/lots/', {'campaign': '2026-2027', 'product': 'cacao', 'lot_date': '2026-11-03',
                                          'lines': [{'producer': str(self.p1.id), 'weight_kg': 100, 'bags': 2}]},
                        format='json', **H)
        self.assertEqual(r.status_code, 201, r.content)

    def test_expedition_seulement_par_fiche_d_accompagnement(self):
        a = self.weigh(self.p1).json()
        lot = self.lot_from([a['id']]).json()
        self.c.patch(f'/api/v1/lots/{lot["id"]}/', {'status': 'valide'}, format='json', **H)
        r = self.c.patch(f'/api/v1/lots/{lot["id"]}/', {'status': 'expedie'}, format='json', **H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Lot.objects.get(pk=lot['id']).status, 'valide')

    # ── Volumes par producteur ─────────────────────────────────────────────────────────────────────
    def test_volumes_et_depassement_de_capacite(self):
        self.weigh(self.p1, gross=3600, bags=50, tare=50, deduction=50)       # 3 500 kg sur 2 ha = 1 750 kg/ha
        a = self.weigh(self.p2, gross=505, bags=5).json()                     # 500 kg sur 2 ha
        self.lot_from([a['id']])
        r = self.c.get('/api/v1/lots/volumes/?campaign=2026-2027', **H).json()
        by = {p['code']: p for p in r['producers']}
        self.assertEqual(by['P1']['net_kg'], 3500.0)
        self.assertEqual(by['P1']['yield_kg_ha'], 1750)
        self.assertEqual(by['P1']['status'], 'depassement')
        self.assertEqual(by['P2']['status'], 'ok')
        self.assertEqual(by['P2']['in_lots_kg'], 500.0)
        self.assertEqual(by['P1']['not_in_lot_kg'], 3500.0)
        self.assertEqual(r['totals']['net_kg'], 4000.0)
        self.assertEqual(r['totals']['producers_over_capacity'], 1)

    def test_controle_du_lot_sur_le_cumul_de_la_campagne(self):
        """Volumes répartis sur plusieurs lots : le contrôle porte sur le cumul de la campagne, pas sur un seul lot."""
        first = self.weigh(self.p1, gross=1810, bags=10).json()               # 1 800 kg
        self.lot_from([first['id']])
        second = self.weigh(self.p1, gross=1310, bags=10).json()              # +1 300 kg → 3 100 kg sur 2 ha
        lot = self.lot_from([second['id']]).json()
        detail = self.c.get(f'/api/v1/lots/{lot["id"]}/', **H).json()
        messages = ' '.join(i['message'] for i in detail['checks']['issues'])
        self.assertIn('campagne 2026-2027', messages)
        self.assertIn('3 100', messages)

    def test_volumes_nombre_de_requetes_constant(self):
        def count():
            with CaptureQueriesContext(connection) as q:
                self.c.get('/api/v1/lots/volumes/?campaign=2026-2027', **H)
            return len(q)
        self.weigh(self.p1)
        few = count()
        for i in range(15):
            p = Producer.objects.create(cooperative=self.coop, field_id_base=f'N{i}', last_name='N')
            self.weigh(p)
        self.assertEqual(count(), few)                                       # jamais une requête par producteur

    # ── Fiches d'accompagnement ────────────────────────────────────────────────────────────────────
    def test_fiche_accompagnement(self):
        a = self.weigh(self.p1, gross=1010, bags=10).json()
        lot = self.lot_from([a['id']]).json()
        slip = {'lot': lot['id'], 'destination': 'Port de San Pedro', 'consignee': 'Export SA', 'vehicle_plate': '1234 AB 01',
                'driver_name': 'Traoré', 'bags': 10, 'gross_weight_kg': 1010, 'seals': 'S-001, S-002'}
        r = self.c.post('/api/v1/lots/transport/', slip, format='json', **H)
        self.assertEqual(r.status_code, 400)                                  # lot encore en brouillon
        self.assertEqual(self.c.patch(f'/api/v1/lots/{lot["id"]}/', {'status': 'valide'}, format='json', **H).status_code, 200)
        r = self.c.post('/api/v1/lots/transport/', slip, format='json', **H)
        self.assertEqual(r.status_code, 201, r.content)
        s = r.json()
        self.assertRegex(s['number'], r'^FA-COOPAK-2026-\d{4}$')
        self.assertEqual(s['lot_code'], lot['code'])
        self.assertEqual(Lot.objects.get(pk=lot['id']).status, 'expedie')
        r = self.c.patch(f'/api/v1/lots/transport/{s["id"]}/', {'received_weight_kg': 1003, 'received_by': 'Magasin port'},
                         format='json', **H)
        self.assertEqual(r.json()['status'], 'livree')
        self.assertEqual(r.json()['weight_gap_kg'], -7.0)
        self.assertEqual(TransportSlip.objects.count(), 1)

    # ── Suppressions ───────────────────────────────────────────────────────────────────────────────
    def test_producteur_avec_pesees_protege(self):
        lone = Producer.objects.create(cooperative=self.coop, field_id_base='P9', last_name='SEUL')
        self.weigh(lone)
        r = self.c.post('/api/v1/producers/bulk-delete/', {'ids': [str(lone.id)]}, format='json', **H)
        self.assertEqual(r.json()['deleted'], 0)
        self.assertTrue(Producer.objects.filter(pk=lone.pk).exists())

    def test_purge_de_la_cooperative_avec_pesees_et_fiches(self):
        from apps.accounts.purge import purge, restore
        from apps.accounts.models import Trash
        a = self.weigh(self.p1, gross=1010, bags=10).json()
        lot = self.lot_from([a['id']]).json()
        self.c.patch(f'/api/v1/lots/{lot["id"]}/', {'status': 'valide'}, format='json', **H)
        self.c.post('/api/v1/lots/transport/', {'lot': lot['id'], 'destination': 'San Pedro', 'vehicle_plate': 'AB',
                                                 'driver_name': 'T', 'gross_weight_kg': 1010}, format='json', **H)
        purge(self.user)                                                      # aucune erreur de protection
        self.assertEqual((Delivery.objects.count(), TransportSlip.objects.count(), Lot.objects.count()), (0, 0, 0))
        restore(Trash.objects.get())                                          # tout revient depuis la corbeille
        self.assertEqual((Delivery.objects.count(), TransportSlip.objects.count(), Lot.objects.count()), (1, 1, 1))
        self.assertEqual(str(Delivery.objects.get().lot_id), lot['id'])
