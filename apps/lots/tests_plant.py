"""Rôle « usine » : réception des fiches d'accompagnement qui lui sont adressées, usinage, cloisonnement."""
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.cooperatives.models import Cooperative
from .models import Plant, ProcessingBatch
from .tests import H, TraceBase


class PlantBase(TraceBase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(username='sa', password='x-123456789', role='super_admin')
        self.coop.managed_by = self.admin
        self.coop.save()
        self.sa = APIClient()
        self.sa.force_authenticate(self.admin)
        r = self.sa.post('/api/v1/lots/plants/', {'name': 'Usine SACO San Pedro', 'location': 'San Pedro',
                                                    'cooperatives': [str(self.coop.id)]}, format='json', **H)
        assert r.status_code == 201, r.content
        self.plant = Plant.objects.get(pk=r.json()['id'])
        r = self.sa.post(f'/api/v1/lots/plants/{self.plant.id}/account/', {'username': 'usine1', 'full_name': 'Chef usine'},
                         format='json', **H)
        assert r.status_code == 201, r.content
        self.temp_password = r.json()['temporary_password']
        self.factory_user = User.objects.get(username='usine1')
        self.u = APIClient()
        self.u.force_authenticate(self.factory_user)

    def ship(self, plant=True, gross=1010, bags=10):
        d = self.weigh(self.p1, gross=gross, bags=bags).json()
        lot = self.lot_from([d['id']]).json()
        self.c.patch(f'/api/v1/lots/{lot["id"]}/', {'status': 'valide'}, format='json', **H)
        body = {'lot': lot['id'], 'destination': 'x', 'vehicle_plate': 'AB', 'driver_name': 'T', 'bags': bags,
                'gross_weight_kg': gross, **({'plant': str(self.plant.id)} if plant else {})}
        slip = self.c.post('/api/v1/lots/transport/', body, format='json', **H)
        assert slip.status_code == 201, slip.content
        return lot, slip.json()


class PlantTests(PlantBase):
    def test_compte_usine(self):
        self.assertEqual(self.factory_user.role, 'usine')
        self.assertEqual(self.factory_user.plant_id, self.plant.id)
        self.assertTrue(self.factory_user.must_change_password)
        self.assertGreaterEqual(len(self.temp_password), 10)

    def test_la_cooperative_voit_les_usines_qui_la_servent(self):
        names = [p['name'] for p in self.c.get('/api/v1/lots/plants/', **H).json()]
        self.assertEqual(names, ['Usine SACO San Pedro'])

    def test_usine_recoit_seulement_ses_fiches(self):
        lot, slip = self.ship()
        _, elsewhere = self.ship(plant=False)
        listed = [s['number'] for s in self.u.get('/api/v1/lots/transport/', **H).json()]
        self.assertEqual(listed, [slip['number']])
        self.assertEqual(slip['destination'], 'Usine SACO San Pedro')                # destination = l'usine choisie
        r = self.u.patch(f'/api/v1/lots/transport/{slip["id"]}/', {'received_weight_kg': 1006}, format='json', **H)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['received_by'], 'Chef usine')
        self.assertEqual(self.u.patch(f'/api/v1/lots/transport/{elsewhere["id"]}/', {'received_weight_kg': 1},
                                      format='json', **H).status_code, 404)
        self.assertEqual(self.u.post('/api/v1/lots/transport/', {}, format='json', **H).status_code, 403)

    def test_usine_usine_les_lots_recus(self):
        lot, slip = self.ship()
        avail = self.u.get('/api/v1/lots/processing/available/', **H).json()
        self.assertEqual(avail['lots'], [])                                      # en route : pas encore usinable
        self.u.patch(f'/api/v1/lots/transport/{slip["id"]}/', {'received_weight_kg': 1006}, format='json', **H)
        avail = self.u.get('/api/v1/lots/processing/available/', **H).json()
        self.assertEqual([(a['code'], a['available_kg']) for a in avail['lots']], [(lot['code'], 1000.0)])
        r = self.u.post('/api/v1/lots/processing/', {'operation': 'usinage', 'process_date': '2026-11-20',
                                                     'inputs': [{'lot': lot['id'], 'weight_kg': 1000}],
                                                     'outputs': [{'product': 'feves_usinees', 'weight_kg': 985}]},
                        format='json', **H)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['plant'], 'Usine SACO San Pedro')
        trace = self.u.get(f'/api/v1/lots/processing/{r.json()["id"]}/trace/', **H).json()
        self.assertEqual(trace['producers'][0]['code'], 'P1')
        # la coopérative voit ce que son lot est devenu
        self.assertEqual([b['code'] for b in self.c.get('/api/v1/lots/processing/', **H).json()], [r.json()['code']])

    def test_usine_ne_peut_pas_usiner_un_lot_qui_ne_lui_est_pas_adresse(self):
        lot, _ = self.ship(plant=False)
        r = self.u.post('/api/v1/lots/processing/', {'operation': 'usinage', 'process_date': '2026-11-20',
                                                     'inputs': [{'lot': lot['id'], 'weight_kg': 100}],
                                                     'outputs': [{'product': 'feves_usinees', 'weight_kg': 90}]},
                        format='json', **H)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ProcessingBatch.objects.count(), 0)

    def test_usine_sans_acces_aux_donnees_de_la_cooperative(self):
        for url in ('/api/v1/lots/', '/api/v1/lots/deliveries/', '/api/v1/lots/volumes/?campaign=2026-2027',
                    '/api/v1/producers/', '/api/v1/parcels/', '/api/v1/parcels/legacy/', '/api/v1/lots/plants/'):
            r = self.u.get(url, **H)
            self.assertIn(r.status_code, (403, 200), url)
            if r.status_code == 200:
                data = r.json()
                rows = data.get('results', data) if isinstance(data, dict) else data
                if url.endswith('plants/'):
                    self.assertEqual([p['name'] for p in rows], ['Usine SACO San Pedro'])   # seulement sa propre usine
                else:
                    self.assertEqual(rows, [], url)                                    # aucune donnée de coopérative

    def test_usine_d_un_autre_client_refusee(self):
        other_admin = User.objects.create_user(username='sb', password='x-123456789', role='super_admin')
        other = Cooperative.objects.create(name='Hors', rccm='R9', managed_by=other_admin)
        r = self.sa.post('/api/v1/lots/plants/', {'name': 'X', 'cooperatives': [str(other.id)]}, format='json', **H)
        self.assertEqual(r.status_code, 400)                                       # coopérative d'un autre client
        sb = APIClient()
        sb.force_authenticate(other_admin)
        self.assertEqual(sb.get('/api/v1/lots/plants/', **H).json(), [])
        self.assertEqual(sb.post(f'/api/v1/lots/plants/{self.plant.id}/account/', {'username': 'pirate'},
                                 format='json', **H).status_code, 404)

    def test_usine_desactivee_bloquee(self):
        self.sa.patch(f'/api/v1/lots/plants/{self.plant.id}/', {'is_active': False}, format='json', **H)
        from apps.accounts.tenancy import invalidate_tenant_cache, tenant_status
        invalidate_tenant_cache()
        self.factory_user.refresh_from_db()
        self.assertFalse(tenant_status(self.factory_user)['active'])

    def test_le_client_gere_les_comptes_de_ses_usines(self):
        users = self.sa.get('/api/v1/auth/users/', **H).json()
        rows = users.get('results', users) if isinstance(users, dict) else users
        self.assertIn('usine1', [u['username'] for u in rows])

    def test_suppression_du_client_supprime_ses_usines(self):
        from apps.accounts.purge import purge
        lot, _ = self.ship()
        purge(self.admin)
        self.assertFalse(Plant.objects.exists())
        self.assertFalse(User.objects.filter(username='usine1').exists())
