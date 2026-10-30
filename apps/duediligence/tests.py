"""Diligence raisonnée : API, cloisonnement, exécution (moteur simulé, sans réseau), décisions, import WHISP."""
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.cooperatives.models import Cooperative
from apps.parcels.models import LandZone, LegacyParcel, Parcel
from apps.producers.models import Producer
from .models import DDAnalysis, DDDecision, ProtectedArea
from . import services

H = {'HTTP_HOST': 'localhost'}


def sq(x, y, d=0.002):
    return {'type': 'Polygon', 'coordinates': [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}


def fake_analyze(features, *a, **kw):
    """Remplace la lecture des rasters : tout est « forêt 2020 », perte sur la 1re parcelle (2 sources)."""
    from eudr_evidence.classify import classify
    from eudr_evidence.config import thresholds
    from eudr_evidence.geometry import validate
    t = kw.get('t') or thresholds()
    results = []
    for i, f in enumerate(features):
        geom, rep = validate(f['geometry'])
        item = {'index': i, 'id': f['id'], 'geometry': geom, 'report': rep}
        loss = 0.5 if i == 0 else 0.0
        ev = {'area_ha': rep['area_ha'], 'pixels': 100,
              'forest2020': {s: {'pct': 90.0, 'ha': 1.0} for s in ('jrc_gfc2020', 'tmf', 'worldcover2020', 'hansen')},
              'forest2020_union_pct': 90.0,
              'loss_post2020': {s: {'ha': loss, 'pct': 10.0 if loss else 0.0, 'first_year': 2022 if loss else None,
                                    'last_year': 2022 if loss else None, 'outside_forest_ha': 0.0}
                                for s in ('hansen', 'tmf_deforestation')},
              'loss_post2020_ha': loss, 'loss_post2020_pct': 10.0 if loss else 0.0, 'predisturbance_pct': {}, 'commodity2020': {}}
        ctx = kw['context_fn'](item) if kw.get('context_fn') else {}
        results.append({'index': i, 'id': f['id'], 'rejected': False, 'geometry_report': rep, 'evidence': ev,
                        'context': ctx, 'classification': classify(ev, ctx, t)})
        if kw.get('progress'):
            kw['progress'](i + 1, len(features))
    return {'results': results, 'rejected': 0, 'analysed': len(results), 'layers': [], 'layers_unavailable': []}


class DueDiligenceTests(TestCase):
    def setUp(self):
        from apps.parcels import rdue
        rdue.ZONES.signature, rdue.ZONES.checked_at = None, 0      # cache des zones propre à chaque test
        self.coop = Cooperative.objects.create(name='C', rccm='RC')
        self.other = Cooperative.objects.create(name='D', rccm='RD')
        self.user = User.objects.create_user(username='c', password='x-123456789', role='cooperative', cooperative=self.coop)
        self.c = APIClient()
        self.c.force_authenticate(self.user)
        p = Producer.objects.create(cooperative=self.coop, field_id_base='P1', last_name='K')
        Parcel.objects.create(producer=p, cooperative=self.coop, field_id='P1', geometry=sq(-5.0, 7.0), village='V', area_hectares=4.9)
        LegacyParcel.objects.create(cooperative=self.coop, producer=p, code='P1-P2', geometry=sq(-4.9, 7.0))
        op = Producer.objects.create(cooperative=self.other, field_id_base='O1', last_name='Z')
        Parcel.objects.create(producer=op, cooperative=self.other, field_id='O1', geometry=sq(-4.0, 7.0), village='V', area_hectares=1)

    def run_analysis(self, **body):
        with mock.patch('eudr_evidence.analyze.analyze', side_effect=fake_analyze), \
                mock.patch.object(services, 'start', side_effect=lambda pk: services.run(pk)), \
                mock.patch.object(services.connection, 'close'):
            with self.captureOnCommitCallbacks(execute=True):
                r = self.c.post('/api/v1/dd/analyses/', {'source': 'both', **body}, format='json', **H)
        self.assertEqual(r.status_code, 201, r.content)
        return DDAnalysis.objects.get(pk=r.json()['id'])

    def test_analyse_complete_et_cloisonnement(self):
        a = self.run_analysis()
        self.assertEqual(a.status, 'done', a.error)
        self.assertEqual(a.total, 2)                                       # pas la parcelle de l'autre coopérative
        d = self.c.get(f'/api/v1/dd/analyses/{a.id}/', **H).json()
        risks = {r['properties']['label']: r['classification']['risk'] for r in d['results']}
        self.assertEqual(risks, {'P1': 'eleve', 'P1-P2': 'a_verifier'})
        self.assertEqual(d['summary']['conclusion'], 'non_negligeable')
        self.assertEqual(d['manifest']['whisp']['executed'], False)
        self.assertEqual(len(d['manifest']['input']['sha256']), 64)
        self.assertGreater(a.keep_until.year - a.created_at.year, 4)        # conservation 5 ans
        u2 = User.objects.create_user(username='d', password='x-123456789', role='cooperative', cooperative=self.other)
        c2 = APIClient(); c2.force_authenticate(u2)
        self.assertEqual(c2.get(f'/api/v1/dd/analyses/{a.id}/', **H).status_code, 404)

    def test_decisions_en_ajout_seulement(self):
        a = self.run_analysis()
        key = a.get_results()[0]['id']
        for dec in ('visite', 'lever'):
            r = self.c.post(f'/api/v1/dd/analyses/{a.id}/decisions/', {'parcel_key': key, 'decision': dec, 'comment': 'ok'},
                            format='json', **H)
            self.assertEqual(r.status_code, 201)
        self.assertEqual(DDDecision.objects.filter(analysis=a).count(), 2)
        d = self.c.get(f'/api/v1/dd/analyses/{a.id}/', **H).json()
        self.assertEqual(d['results'][0]['classification']['risk'], 'eleve')  # le résultat automatique reste intact

    def test_import_whisp_concordance(self):
        a = self.run_analysis()
        ids = [r['id'] for r in a.get_results()]
        csv = f'external_id,risk_pcrop\n{ids[0]},high\n{ids[1]},low\n'
        r = self.c.post(f'/api/v1/dd/analyses/{a.id}/whisp-import/', {'csv': csv}, format='json', **H)
        self.assertEqual(r.json()['matched'], 2)
        self.assertEqual(r.json()['summary']['whisp_concordance_rate'], 50.0)
        a.refresh_from_db()
        self.assertEqual(a.whisp_status, 'importe')

    def test_foret_classee_wdpa_et_enclave(self):
        """Parcelle en enclave (RDUE) dans une forêt classée WDPA : le contour WDPA ne la pénalise pas."""
        ProtectedArea.objects.create(source='WDPA', name='FC test', designation='Classified Forest', geometry=sq(-5.1, 6.9, 0.3))
        LandZone.objects.create(category='Foret classee', name='FC test', geometry=sq(-5.1, 6.9, 0.3))
        LandZone.objects.create(category='Enclave', name='Enclave test', geometry=sq(-5.01, 6.99, 0.03))
        from apps.parcels import rdue
        rdue.ZONES.signature, rdue.ZONES.checked_at = None, 0
        a = self.run_analysis()
        by = {r['properties']['label']: r for r in a.get_results()}
        self.assertEqual(by['P1']['context']['rdue']['category'], 'Enclave')
        self.assertEqual(by['P1']['context']['protected'], [])
        self.assertTrue(by['P1-P2']['classification']['rdue_illegal'])     # hors enclave : forêt classée → élevé
        self.assertEqual(by['P1-P2']['classification']['risk'], 'eleve')

    def test_module_requis(self):
        from apps.accounts.models import AdminLicense
        sa = User.objects.create_user(username='sa', password='x-123456789', role='super_admin')
        AdminLicense.objects.create(user=sa, modules=['lots'])
        self.coop.managed_by = sa; self.coop.save()
        from apps.accounts.tenancy import invalidate_tenant_cache
        invalidate_tenant_cache()
        self.assertEqual(self.c.get('/api/v1/dd/catalog/', **H).status_code, 403)
