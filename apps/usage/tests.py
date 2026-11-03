"""Statistiques d'utilisation : enregistrement des pages vues, cloisonnement, chiffres par compte et par organisation."""
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import ActivityLog, User
from apps.cooperatives.models import Cooperative
from .models import PageView
from .tracking import normalize

H = {'HTTP_HOST': 'localhost'}
OVERVIEW = '/api/v1/usage/overview/?days=30'


def client(user, ua='Mozilla/5.0 (Windows NT 10.0) Chrome/120'):
    c = APIClient(HTTP_USER_AGENT=ua)
    c.force_authenticate(user)
    return c


class UsageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='own', password='x-123456789', role='owner')
        self.sa = User.objects.create_user(username='sa', password='x-123456789', role='super_admin', full_name='Client A')
        self.sa2 = User.objects.create_user(username='sa2', password='x-123456789', role='super_admin', full_name='Client B')
        self.coop = Cooperative.objects.create(name='Coop A', rccm='R1', managed_by=self.sa)
        self.coop2 = Cooperative.objects.create(name='Coop B', rccm='R2', managed_by=self.sa2)
        self.cu = User.objects.create_user(username='c', password='x-123456789', role='cooperative', cooperative=self.coop)
        self.agent = User.objects.create_user(username='ag', password='x-123456789', role='agent', cooperative=self.coop)
        self.cu2 = User.objects.create_user(username='c2', password='x-123456789', role='cooperative', cooperative=self.coop2)

    def track(self, user, paths, session='sess-0001', ua='Mozilla/5.0 (Windows NT 10.0) Chrome/120'):
        now = timezone.now().isoformat()
        return client(user, ua).post('/api/v1/usage/track/', {'events': [{'path': p, 'session': session, 'at': now} for p in paths]},
                                     format='json', **H)

    def test_adresse_normalisee_sans_identifiant(self):
        self.assertEqual(normalize('/coop/lots/3f0c1a2b-1111-2222-3333-444455556666?tab=x'), '/coop/lots/:id')
        self.assertEqual(normalize('/admin/plants/12'), '/admin/plants/:id')
        self.assertEqual(normalize('/Coop/Parcels#map'), '/coop/parcels')

    def test_enregistrement_et_appareil(self):
        r = self.track(self.cu, ['/coop', '/coop/lots'], ua='Mozilla/5.0 (iPhone) Mobile Safari')
        self.assertEqual(r.json()['recorded'], 2)
        self.assertEqual(set(PageView.objects.values_list('device', flat=True)), {'mobile'})
        bad = client(self.cu).post('/api/v1/usage/track/', {'events': [{'path': '/x', 'session': '!'},
                                                                        {'path': '/x', 'session': 'sess-0002', 'at': '2001-01-01T00:00:00Z'}]},
                                   format='json', **H)
        self.assertEqual(bad.json()['recorded'], 0)                       # session invalide, date trop ancienne
        self.assertEqual(client(self.cu).post('/api/v1/usage/track/', {}, format='json', **H).status_code, 400)

    def test_voir_comme_non_compte(self):
        from rest_framework_simplejwt.tokens import AccessToken
        token = AccessToken.for_user(self.cu)
        token['imp_by'] = str(self.sa.pk)
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        c.post('/api/v1/usage/track/', {'events': [{'path': '/coop', 'session': 'sess-0001'}]}, format='json', **H)
        self.assertEqual(PageView.objects.count(), 0)

    def test_chiffres_et_cloisonnement(self):
        self.track(self.cu, ['/coop', '/coop/lots', '/coop/lots'])
        self.track(self.agent, ['/agent'], session='sess-0002')
        self.track(self.cu2, ['/coop'], session='sess-0003')
        ActivityLog.objects.create(user=self.agent, action='login', resource='auth')
        ActivityLog.objects.create(user=self.agent, action='create_parcel', resource='parcel')
        d = client(self.sa).get(OVERVIEW, **H).json()
        names = {a['username'] for a in d['accounts']}
        self.assertEqual(names, {'sa', 'c', 'ag'})                         # jamais les comptes d'un autre client
        t = d['totals']
        self.assertEqual((t['page_views'], t['sessions'], t['logins'], t['actions'], t['active_today']), (4, 2, 1, 1, 2))
        self.assertEqual(d['top_pages'][0], {'path': '/coop/lots', 'views': 2, 'users': 1})
        self.assertEqual(d['by_cooperative'][0]['name'], 'Coop A')
        self.assertEqual(d['by_cooperative'][0]['active'], 2)
        self.assertEqual(d['daily'][-1]['active_users'], 2)                # c (pages) et ag (pages, connexion)
        self.assertEqual(len(d['daily']), 30)
        # propriétaire : tous les clients, regroupés par super admin ; filtres
        o = client(self.owner).get(OVERVIEW, **H).json()
        self.assertEqual({g['name'] for g in o['by_admin']} >= {'Client A', 'Client B'}, True)
        self.assertEqual({a['username'] for a in client(self.owner).get(OVERVIEW + f'&admin={self.sa2.id}', **H).json()['accounts']},
                         {'sa2', 'c2'})
        # coopérative : ses comptes seulement ; agent : refusé
        self.assertEqual({a['username'] for a in client(self.cu).get(OVERVIEW, **H).json()['accounts']}, {'c', 'ag'})
        self.assertEqual(client(self.agent).get(OVERVIEW, **H).status_code, 403)
        self.assertEqual(client(self.sa).get('/api/v1/usage/overview/?days=12', **H).status_code, 400)
        self.assertEqual(client(self.sa).get(OVERVIEW + '&cooperative=abc', **H).status_code, 400)

    def test_statut_d_activite(self):
        User.objects.filter(pk=self.agent.pk).update(last_login=timezone.now() - timedelta(days=45))
        rows = {a['username']: a['status'] for a in client(self.cu).get(OVERVIEW, **H).json()['accounts']}
        self.assertEqual(rows, {'c': 'jamais', 'ag': 'inactif'})

    def test_requetes_constantes(self):
        def count():
            with CaptureQueriesContext(connection) as q:
                client(self.owner).get(OVERVIEW, **H)
            return len(q)
        self.track(self.cu, ['/coop'])
        few = count()
        for i in range(8):
            u = User.objects.create_user(username=f'a{i}', password='x-123456789', role='agent', cooperative=self.coop)
            self.track(u, ['/agent', '/agent/parcels'], session=f'sess-1{i:03d}')
            ActivityLog.objects.create(user=u, action='login', resource='auth')
        self.assertEqual(count(), few)

    def test_purge_du_client(self):
        from apps.accounts.purge import purge
        self.track(self.cu, ['/coop'])
        purge(self.sa)
        self.assertEqual(PageView.objects.count(), 0)
