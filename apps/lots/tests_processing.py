"""Usinage et transformation : bilan matière, remontée du produit fini jusqu'aux producteurs et parcelles."""
from rest_framework.test import APIClient

from apps.accounts.models import User
from .models import ProcessingBatch
from .tests import H, TraceBase


class ProcessingTests(TraceBase):
    def shipped_lot(self, producer, gross, bags, received=None):
        d = self.weigh(producer, gross=gross, bags=bags).json()
        lot = self.lot_from([d['id']]).json()
        self.c.patch(f'/api/v1/lots/{lot["id"]}/', {'status': 'valide'}, format='json', **H)
        slip = self.c.post('/api/v1/lots/transport/', {'lot': lot['id'], 'destination': 'Usine San Pedro', 'vehicle_plate': 'AB',
                                                        'driver_name': 'T', 'bags': bags, 'gross_weight_kg': gross},
                           format='json', **H).json()
        if received is not None:
            self.c.patch(f'/api/v1/lots/transport/{slip["id"]}/', {'received_weight_kg': received}, format='json', **H)
        return lot

    def batch(self, inputs, outputs, operation='usinage', **extra):
        body = {'plant': 'Usine SACO San Pedro', 'operation': operation, 'process_date': '2026-11-20',
                'inputs': inputs, 'outputs': outputs, **extra}
        return self.c.post('/api/v1/lots/processing/', body, format='json', **H)

    def test_usinage_bilan_matiere_et_numero(self):
        a = self.shipped_lot(self.p1, 1010, 10, received=1005)                 # lot net 1 000 kg, reçu 1 005 kg brut
        b = self.shipped_lot(self.p2, 505, 5)
        r = self.batch([{'lot': a['id'], 'weight_kg': 1000}, {'lot': b['id'], 'weight_kg': 500}],
                       [{'product': 'feves_usinees', 'weight_kg': 1440, 'reference': 'GRADE I'},
                        {'product': 'dechets', 'weight_kg': 45}])
        self.assertEqual(r.status_code, 201, r.content)
        b_ = r.json()
        self.assertRegex(b_['code'], r'^USN-2026-\d{4}$')
        self.assertEqual(b_['input_kg'], 1500.0)
        self.assertEqual(b_['output_kg'], 1485.0)
        self.assertEqual(b_['loss_kg'], 15.0)
        self.assertEqual(b_['loss_pct'], 1.0)

    def test_sortie_superieure_a_l_entree_refusee(self):
        a = self.shipped_lot(self.p1, 1010, 10)
        r = self.batch([{'lot': a['id'], 'weight_kg': 1000}], [{'product': 'feves_usinees', 'weight_kg': 1200}])
        self.assertEqual(r.status_code, 400)

    def test_lot_non_expedie_ou_surconsomme_refuse(self):
        d = self.weigh(self.p1).json()
        draft = self.lot_from([d['id']]).json()
        self.assertEqual(self.batch([{'lot': draft['id'], 'weight_kg': 100}],
                                    [{'product': 'feves_usinees', 'weight_kg': 90}]).status_code, 400)
        a = self.shipped_lot(self.p2, 1010, 10)                               # 1 000 kg disponibles
        self.assertEqual(self.batch([{'lot': a['id'], 'weight_kg': 700}],
                                    [{'product': 'feves_usinees', 'weight_kg': 690}]).status_code, 201)
        r = self.batch([{'lot': a['id'], 'weight_kg': 400}], [{'product': 'feves_usinees', 'weight_kg': 390}])
        self.assertEqual(r.status_code, 400)                                  # 700 + 400 > 1 000
        self.assertIn('300', str(r.content.decode()))

    def test_transformation_a_partir_d_un_lot_usine(self):
        a = self.shipped_lot(self.p1, 1010, 10)
        u = self.batch([{'lot': a['id'], 'weight_kg': 1000}], [{'product': 'feves_usinees', 'weight_kg': 980}]).json()
        r = self.batch([{'batch': u['id'], 'weight_kg': 980}],
                       [{'product': 'masse', 'weight_kg': 780}, {'product': 'coques', 'weight_kg': 150}], operation='transformation')
        self.assertEqual(r.status_code, 201, r.content)
        t = r.json()
        self.assertRegex(t['code'], r'^TRF-2026-\d{4}$')
        chain = self.c.get(f'/api/v1/lots/processing/{t["id"]}/trace/', **H).json()
        self.assertEqual([x['code'] for x in chain['batches']], [t['code'], u['code']])
        self.assertEqual([l['code'] for l in chain['lots']], [a['code']])
        self.assertEqual(chain['producers'][0]['code'], 'P1')
        self.assertEqual(chain['parcels'][0]['field_id'], 'P1-1')
        self.assertEqual(chain['deliveries'][0]['number'][:4], 'PES-')
        self.assertEqual(chain['transport'][0]['destination'], 'Usine San Pedro')
        self.assertEqual(chain['eudr']['non_compliant_parcels'], 0)

    def test_suivi_aval_depuis_un_lot(self):
        a = self.shipped_lot(self.p1, 1010, 10)
        u = self.batch([{'lot': a['id'], 'weight_kg': 600}], [{'product': 'feves_usinees', 'weight_kg': 590}]).json()
        lot = self.c.get(f'/api/v1/lots/{a["id"]}/', **H).json()
        self.assertEqual([x['code'] for x in lot['downstream']['batches']], [u['code']])
        self.assertEqual(lot['downstream']['processed_kg'], 600.0)
        self.assertEqual(lot['downstream']['available_kg'], 400.0)

    def test_cloisonnement_usinage(self):
        a = self.shipped_lot(self.p1, 1010, 10)
        u = self.batch([{'lot': a['id'], 'weight_kg': 1000}], [{'product': 'feves_usinees', 'weight_kg': 990}]).json()
        u2 = User.objects.create_user(username='e', password='x-123456789', role='cooperative', cooperative=self.other)
        c2 = APIClient()
        c2.force_authenticate(u2)
        self.assertEqual(c2.get(f'/api/v1/lots/processing/{u["id"]}/trace/', **H).status_code, 404)
        self.assertEqual(c2.get('/api/v1/lots/processing/', **H).json(), [])
        self.assertEqual(ProcessingBatch.objects.count(), 1)

    def test_purge_avec_usinage_et_transformation(self):
        from apps.accounts.models import Trash
        from apps.accounts.purge import purge, restore
        a = self.shipped_lot(self.p1, 1010, 10)
        u = self.batch([{'lot': a['id'], 'weight_kg': 1000}], [{'product': 'feves_usinees', 'weight_kg': 980}]).json()
        self.batch([{'batch': u['id'], 'weight_kg': 980}], [{'product': 'masse', 'weight_kg': 780}], operation='transformation')
        purge(self.user)
        self.assertEqual(ProcessingBatch.objects.count(), 0)
        restore(Trash.objects.get())
        self.assertEqual(ProcessingBatch.objects.count(), 2)

    def test_usine_obligatoire(self):
        a = self.shipped_lot(self.p1, 1010, 10)
        r = self.batch([{'lot': a['id'], 'weight_kg': 100}], [{'product': 'feves_usinees', 'weight_kg': 90}], plant=' ')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ProcessingBatch.objects.count(), 0)
