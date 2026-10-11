from django.test import TestCase

from apps.cooperatives.models import Cooperative
from apps.producers.models import Producer
from . import rdue, spatial
from .models import LandZone, LegacyParcel, Parcel


def sq(x, y, d=0.002):
    return {'type': 'Polygon', 'coordinates': [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}


class SpatialTests(TestCase):
    """Contrôles spatiaux (moteur Python en test : SQLite ; PostGIS donne les mêmes résultats en production)."""

    def setUp(self):
        self.a = Cooperative.objects.create(name='A', rccm='RA')
        self.b = Cooperative.objects.create(name='B', rccm='RB')
        p1 = Producer.objects.create(cooperative=self.a, field_id_base='A-1', last_name='K')
        p2 = Producer.objects.create(cooperative=self.a, field_id_base='A-2', last_name='Y')
        p3 = Producer.objects.create(cooperative=self.b, field_id_base='B-1', last_name='T')
        Parcel.objects.create(producer=p1, cooperative=self.a, field_id='A-1-P1', geometry=sq(-5.0, 7.0), village='V', area_hectares=4.9)
        Parcel.objects.create(producer=p2, cooperative=self.a, field_id='A-2-P1', geometry=sq(-4.999, 7.0), village='V', area_hectares=4.9)
        LegacyParcel.objects.create(cooperative=self.a, producer=p1, code='A-1-P1', geometry=sq(-5.0, 7.0))
        LegacyParcel.objects.create(cooperative=self.a, code='LOIN', geometry=sq(-4.9, 7.0))
        Parcel.objects.create(producer=p3, cooperative=self.b, field_id='B-1-P1', geometry=sq(-4.9995, 7.0015), village='V', area_hectares=4.9)
        LandZone.objects.create(category='Foret classee', name='FC', geometry=sq(-4.9005, 6.9995, 0.01))
        LandZone.objects.create(category='Enclave', name='ENC', geometry=sq(-4.9002, 6.9998, 0.001))

    def test_chevauchements(self):
        s, rows = spatial.overlaps(self.a)
        self.assertEqual(s['engine'], 'shapely')
        self.assertEqual((s['meme_producteur'], s['autres_producteurs'], s['autre_cooperative'], s['doublons']), (1, 2, 3, 1))
        other = [r for r in rows if r['type'] == 'autre_cooperative']
        self.assertTrue(all(r['b']['id'] is None and r['b']['producer'] is None for r in other))   # anonymat

    def test_parcelle_debordant_d_une_enclave(self):
        s, rows = spatial.zones(self.a)
        self.assertEqual(s['illegal'], 1)
        self.assertEqual((rows[0]['label'], rows[0]['category'], rows[0]['legalite']), ('LOIN', 'Foret classee', 'Non conforme'))
        self.assertEqual(rdue.category_of(sq(-4.9, 7.0))[0], 'Foret classee')
        self.assertEqual(rdue.category_of(sq(-4.9001, 6.9999, 0.0005))[0], 'Enclave')
        self.assertEqual(rdue.category_of(sq(-4.5, 7.0))[0], 'Domaine rural')


class ParcelCodeTests(TestCase):
    """Un producteur du registre sans polygone : sa 1re parcelle porte EXACTEMENT son code, sans ajout."""

    def setUp(self):
        from django.test import override_settings  # noqa: F401
        from rest_framework.test import APIClient
        from apps.accounts.models import User
        self.coop = Cooperative.objects.create(name='C', rccm='RC')
        self.other = Cooperative.objects.create(name='D', rccm='RD')
        self.user = User.objects.create_user(username='coopc', password='x-123456789', role='cooperative', cooperative=self.coop)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def post(self, producer, x=-5.0):
        return self.client.post('/api/v1/parcels/', {
            'producer': str(producer.id), 'name': 'P', 'village': 'V', 'culture': 'Cacao', 'area_hectares': 1.2,
            'geometry': sq(x, 7.0)}, format='json', HTTP_HOST='localhost')

    def test_code_du_registre_exact(self):
        p = Producer.objects.create(cooperative=self.coop, field_id_base='122-NV-331', last_name='K')
        r = self.post(p)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['field_id'], '122-NV-331')
        # 2e parcelle du même producteur (déjà cartographié) : suffixe
        r = self.post(p, -4.9)
        self.assertEqual(r.json()['field_id'], '122-NV-331-M002')

    def test_producteur_avec_ancien_polygone(self):
        p = Producer.objects.create(cooperative=self.coop, field_id_base='BA-SE-354', last_name='Y')
        LegacyParcel.objects.create(cooperative=self.coop, producer=p, code='BA-SE-354', geometry=sq(-5, 7))
        self.assertEqual(self.post(p).json()['field_id'], 'BA-SE-354-M001')

    def test_meme_code_dans_deux_cooperatives(self):
        other_p = Producer.objects.create(cooperative=self.other, field_id_base='12', last_name='Z')
        Parcel.objects.create(producer=other_p, cooperative=self.other, field_id='12', geometry=sq(-6, 7), village='V', area_hectares=1)
        p = Producer.objects.create(cooperative=self.coop, field_id_base='12', last_name='K')
        self.assertEqual(self.post(p).json()['field_id'], '12')
