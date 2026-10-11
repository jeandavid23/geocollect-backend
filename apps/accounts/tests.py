import time

from django.test import TestCase
from rest_framework.test import APIClient

from . import mfa
from .models import User

H = {'HTTP_HOST': 'localhost'}


class SecurityTests(TestCase):
    """Mot de passe provisoire, double authentification obligatoire pour les administrateurs."""

    def login(self, username, password):
        return APIClient().post('/api/v1/auth/login/', {'username': username, 'password': password}, format='json', **H)

    def client_for(self, access):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        return c

    def test_mot_de_passe_provisoire(self):
        u = User.objects.create_user(username='coopx', password='Provisoire-123', role='cooperative', must_change_password=True)
        r = self.login('coopx', 'Provisoire-123')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['user']['must_change_password'])
        c = self.client_for(r.json()['access'])
        self.assertEqual(c.get('/api/v1/producers/', **H).json().get('code'), 'password_change_required')
        self.assertEqual(c.get('/api/v1/auth/me/', **H).status_code, 200)
        r = c.post('/api/v1/auth/change-password/', {'old_password': 'Provisoire-123', 'new_password': 'Mon-Nouveau-Mdp-2026'}, format='json', **H)
        self.assertEqual(r.status_code, 200, r.content)
        u.refresh_from_db()
        self.assertFalse(u.must_change_password)
        self.assertNotEqual(c.get('/api/v1/producers/', **H).status_code, 403)

    def test_double_authentification_proprietaire(self):
        User.objects.create_user(username='own', password='Owner-Secret-2026', role='owner')
        r = self.login('own', 'Owner-Secret-2026')
        c = self.client_for(r.json()['access'])
        self.assertEqual(c.get('/api/v1/auth/users/', **H).json().get('code'), 'mfa_setup_required')
        s = c.get('/api/v1/auth/mfa/setup/', **H).json()
        self.assertIn('otpauth://totp/', s['uri'])
        self.assertEqual(c.post('/api/v1/auth/mfa/setup/', {'code': '000000'}, format='json', **H).status_code, 400)
        r = c.post('/api/v1/auth/mfa/setup/', {'code': mfa._code(s['secret'], int(time.time() // 30))}, format='json', **H)
        codes = r.json()['recovery_codes']
        self.assertEqual(len(codes), 8)
        self.assertEqual(c.get('/api/v1/auth/users/', **H).status_code, 200)

        # connexion suivante : pas de jeton avant le code
        r = self.login('own', 'Owner-Secret-2026').json()
        self.assertTrue(r['mfa_required'])
        self.assertNotIn('access', r)
        v = APIClient().post('/api/v1/auth/mfa/verify/', {'mfa_token': r['mfa_token'], 'code': '123456'}, format='json', **H)
        self.assertEqual(v.status_code, 400)
        # code de secours : valable une seule fois
        v = APIClient().post('/api/v1/auth/mfa/verify/', {'mfa_token': r['mfa_token'], 'code': codes[0]}, format='json', **H)
        self.assertEqual(v.status_code, 200)
        self.assertIn('access', v.json())
        r = self.login('own', 'Owner-Secret-2026').json()
        v = APIClient().post('/api/v1/auth/mfa/verify/', {'mfa_token': r['mfa_token'], 'code': codes[0]}, format='json', **H)
        self.assertEqual(v.status_code, 400)

    def test_jeton_mfa_falsifie(self):
        v = APIClient().post('/api/v1/auth/mfa/verify/', {'mfa_token': 'faux', 'code': '123456'}, format='json', **H)
        self.assertEqual(v.status_code, 401)


class ImpersonationTests(TestCase):
    """« Voir comme » : lecture seule, limité aux comptes que l'on gère."""

    def setUp(self):
        from apps.cooperatives.models import Cooperative
        from rest_framework_simplejwt.tokens import AccessToken
        self.sa = User.objects.create_user(username='sa', password='Sa-Secret-2026', role='super_admin', totp_enabled=True)
        self.other_sa = User.objects.create_user(username='sa2', password='Sa-Secret-2026', role='super_admin', totp_enabled=True)
        mine = Cooperative.objects.create(name='Mine', rccm='R1', managed_by=self.sa)
        other = Cooperative.objects.create(name='Other', rccm='R2', managed_by=self.other_sa)
        self.coop_user = User.objects.create_user(username='c1', password='x-123456789', role='cooperative', cooperative=mine)
        self.other_user = User.objects.create_user(username='c2', password='x-123456789', role='cooperative', cooperative=other)
        self.c = APIClient()
        self.c.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.sa)}')

    def test_lecture_seule(self):
        r = self.c.post('/api/v1/auth/impersonate/', {'user': str(self.coop_user.id)}, format='json', **H)
        self.assertEqual(r.status_code, 200, r.content)
        v = APIClient()
        v.credentials(HTTP_AUTHORIZATION=f'Bearer {r.json()["access"]}')
        self.assertEqual(v.get('/api/v1/auth/me/', **H).json()['username'], 'c1')
        self.assertEqual(v.post('/api/v1/producers/', {'last_name': 'X'}, format='json', **H).json().get('code'), 'read_only')

    def test_pas_les_comptes_d_un_autre_client(self):
        r = self.c.post('/api/v1/auth/impersonate/', {'user': str(self.other_user.id)}, format='json', **H)
        self.assertEqual(r.status_code, 403)

    def test_comparatif_limite_a_ses_cooperatives(self):
        r = self.c.get('/api/v1/dashboard/admin/compare/', **H)
        self.assertEqual([x['name'] for x in r.json()['cooperatives']], ['Mine'])
