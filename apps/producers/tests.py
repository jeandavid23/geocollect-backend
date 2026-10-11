from django.test import TestCase

from apps.cooperatives.models import Cooperative
from apps.parcels.models import LegacyParcel
from .matching import CodeIndex, clean_code, normalize_code, relink
from .models import Producer

SQ = {'type': 'Polygon', 'coordinates': [[[-5, 7], [-4.998, 7], [-4.998, 7.002], [-5, 7.002], [-5, 7]]]}


class CodeMatchingTests(TestCase):
    """Correspondance registre ↔ polygones : un même code écrit autrement doit être reconnu, sans faux rattachement."""

    def setUp(self):
        codes = {'BA-SE-354': 'A', '122-NV-331': 'B', 'CI-SBR-0003': 'C', '12': 'D', 'X9-P1': 'F', 'DUP-1': 'G', 'DUP_1': 'H'}
        self.idx = CodeIndex((normalize_code(k), v) for k, v in codes.items())

    def look(self, code):
        pid, state = self.idx.lookup(normalize_code(code))
        return pid or ('?' if state == 'ambigu' else '-')

    def test_variantes_d_ecriture(self):
        for code in ['BA_SE_354', 'ba se 354', 'BA–SE–354', "'BA-SE-354", 'BA-SE-354 ', '﻿BA-SE-354', 'BASE354']:
            self.assertEqual(self.look(code), 'A', code)
        self.assertEqual(self.look('CI-SBR-3'), 'C')
        self.assertEqual(self.look(12.0), 'D')
        self.assertEqual(self.look('12.0'), 'D')

    def test_suffixes_de_parcelle(self):
        for code in ['BA-SE-354-P1', 'BA-SE-354P2', 'BA-SE-354/PARC3', 'BA-SE-354_2']:
            self.assertEqual(self.look(code), 'A', code)
        self.assertEqual(self.look('122-NV-331P1'), 'B')
        self.assertEqual(self.look('X9-P2'), 'F')   # producteur codé lui-même avec un code de parcelle

    def test_pas_de_faux_rattachement(self):
        self.assertEqual(self.look('DUP-1'), '?')   # deux producteurs : ambigu, jamais au hasard
        for code in ['122-NV-33', 'AB-770', 'ZZ-1', None, 'NULL']:
            self.assertEqual(self.look(code), '-', code)

    def test_clean_code_garde_l_ecriture_du_registre(self):
        self.assertEqual(clean_code(354.0), '354')
        self.assertEqual(clean_code("'0012"), '0012')
        self.assertEqual(clean_code(' BA-SE-354 '), 'BA-SE-354')

    def test_relink_producteurs_a_mapper(self):
        coop = Cooperative.objects.create(name='T', rccm='T1')
        for code in ['BA-SE-354', 'NEW-01']:
            Producer.objects.create(cooperative=coop, field_id_base=code, last_name='x')
        for code in ['BA-SE-354-P1', 'ba_se_354_p2', 'INCONNU-9']:
            LegacyParcel.objects.create(cooperative=coop, geometry=SQ, properties={'CODE': code})
        s = relink(coop)
        self.assertEqual(s['code_field'], 'CODE')
        self.assertEqual((s['mapped_producers'], s['to_map'], s['linked_polygons']), (1, 1, 2))
        self.assertEqual(s['polygons_per_producer']['2'], 1)
        self.assertEqual(s['unmatched_codes'], ['INCONNU-9'])


class OwnerDeleteTests(TestCase):
    """Le propriétaire supprime n'importe quel compte, sauf le sien et le dernier propriétaire."""

    def setUp(self):
        from rest_framework.test import APIClient
        from apps.accounts.models import User
        self.owner = User.objects.create_user(username='own', password='x-123456789', role='owner')
        self.owner2 = User.objects.create_user(username='own2', password='x-123456789', role='owner')
        self.sa = User.objects.create_user(username='sa', password='x-123456789', role='super_admin')
        self.coop = Cooperative.objects.create(name='C', rccm='RC', managed_by=self.sa)
        self.c = APIClient()
        self.c.force_authenticate(self.owner)

    def test_super_admin_supprime_avec_ses_cooperatives(self):
        from apps.accounts.models import User
        Producer.objects.create(cooperative=self.coop, field_id_base='X1', last_name='K')
        User.objects.create_user(username='coopacc', password='x-123456789', role='cooperative', cooperative=self.coop)
        r = self.c.post('/api/v1/auth/users/delete-preview/', {'ids': [str(self.sa.id)]}, format='json', HTTP_HOST='localhost')
        self.assertEqual((r.json()['cooperatives'], r.json()['producers'], r.json()['accounts']), (1, 1, 2))
        r = self.c.delete(f'/api/v1/auth/users/{self.sa.id}/', HTTP_HOST='localhost')
        self.assertEqual(r.status_code, 204, r.content)
        self.assertFalse(Cooperative.objects.filter(pk=self.coop.pk).exists())
        self.assertFalse(Producer.objects.filter(field_id_base='X1').exists())
        self.assertFalse(User.objects.filter(username__in=['sa', 'coopacc']).exists())

    def test_autre_proprietaire_mais_pas_soi_meme(self):
        r = self.c.post('/api/v1/auth/users/bulk-delete/', {'ids': [str(self.owner2.id), str(self.owner.id)]}, format='json', HTTP_HOST='localhost')
        self.assertEqual(r.json()['deleted'], 1)
        self.assertEqual([x['username'] for x in r.json()['refused']], ['own'])

    def test_super_admin_ne_supprime_pas_un_proprietaire(self):
        self.c.force_authenticate(self.sa)
        r = self.c.delete(f'/api/v1/auth/users/{self.owner2.id}/', HTTP_HOST='localhost')
        self.assertIn(r.status_code, (403, 404))


class PurgeTests(TestCase):
    """Un compte supprimé ne laisse aucune donnée en base."""

    def setUp(self):
        import datetime
        from apps.accounts.models import ActivityLog, User
        from apps.lots.models import Lot, LotLine
        from apps.parcels.models import Parcel
        from apps.producers.models import Agent
        self.coop = Cooperative.objects.create(name='C', rccm='RC')
        self.keep = Cooperative.objects.create(name='K', rccm='RK')
        self.coop_user = User.objects.create_user(username='c', password='x-123456789', role='cooperative', cooperative=self.coop)
        self.agent_user = User.objects.create_user(username='a', password='x-123456789', role='agent', cooperative=self.coop)
        self.agent = Agent.objects.create(user=self.agent_user, cooperative=self.coop, code='AG1')
        p = Producer.objects.create(cooperative=self.coop, field_id_base='P1', last_name='K')
        self.parcel = Parcel.objects.create(producer=p, cooperative=self.coop, agent=self.agent, field_id='P1', geometry=SQ, village='V', area_hectares=1)
        Parcel.objects.create(producer=p, cooperative=self.coop, field_id='P1-M002', geometry=SQ, village='V', area_hectares=1)
        lot = Lot.objects.create(cooperative=self.coop, code='LOT-1', campaign='2026', lot_date=datetime.date(2026, 10, 1))
        LotLine.objects.create(lot=lot, producer=p, weight_kg=100)
        ActivityLog.objects.create(user=self.agent_user, action='x', resource='y')
        Producer.objects.create(cooperative=self.keep, field_id_base='K1', last_name='Z')

    def test_agent_avec_ses_parcelles(self):
        from apps.accounts.purge import purge
        from apps.accounts.models import ActivityLog, User
        from apps.parcels.models import Parcel
        done = purge(self.agent_user)
        self.assertEqual(done['parcels'], 1)
        self.assertEqual(list(Parcel.objects.values_list('field_id', flat=True)), ['P1-M002'])   # parcelle d'un autre auteur gardée
        self.assertFalse(User.objects.filter(username='a').exists())
        self.assertFalse(ActivityLog.objects.filter(action='x').exists())

    def test_dernier_compte_cooperative_efface_toute_la_cooperative(self):
        from apps.accounts.purge import purge
        from apps.accounts.models import User
        from apps.lots.models import Lot
        from apps.parcels.models import Parcel
        purge(self.coop_user)
        self.assertFalse(Cooperative.objects.filter(pk=self.coop.pk).exists())
        self.assertFalse(Parcel.objects.exists() or Lot.objects.exists())
        self.assertFalse(User.objects.filter(username__in=['a', 'c']).exists())
        self.assertTrue(Producer.objects.filter(field_id_base='K1').exists())                      # autre coopérative intacte
