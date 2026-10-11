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
