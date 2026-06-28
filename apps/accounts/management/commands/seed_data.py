"""
Commande Django : python manage.py seed_data
Crée les données initiales de démonstration pour GeoCollect EUDR.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Initialise la base de données avec des données de démonstration.'

    def handle(self, *args, **options):
        self.stdout.write('🌱 Initialisation des données GeoCollect EUDR...')
        with transaction.atomic():
            self._create_cooperatives()
            self._create_users()
            self._create_producers()
            self._create_parcels()
        self.stdout.write(self.style.SUCCESS('✅ Données créées avec succès !'))
        self.stdout.write('')
        self.stdout.write('Comptes de connexion :')
        self.stdout.write('  Super Admin  → admin / admin123')
        self.stdout.write('  Coopérative  → coop  / coop123')
        self.stdout.write('  Agent        → agent / agent123')

    def _create_cooperatives(self):
        from apps.cooperatives.models import Cooperative
        coops = [
            dict(name='COOPACI BEOUMI', rccm='CI-ABJ-2018-B-12345', agrement='MINADER/2018/001',
                 pca='KOUAME Thierry', adg='YAO Jean-Baptiste', director='KONAN Marcellin',
                 sig_manager='BAMBA Issouf', phone='+225 27 31 63 00 00',
                 email='coopaci@beoumi.ci', address='Beoumi, Côte d\'Ivoire',
                 region='Bélier', country='Côte d\'Ivoire'),
            dict(name='COOP CAFÉ DALOA', rccm='CI-ABJ-2019-B-67890', agrement='MINADER/2019/002',
                 pca='OUATTARA Seydou', adg='COULIBALY Abdoulaye', director='BERTE Mamadou',
                 sig_manager='KONE Aminata', phone='+225 27 32 00 00 00',
                 email='coopcafe@daloa.ci', address='Daloa, Côte d\'Ivoire',
                 region='Haut-Sassandra', country='Côte d\'Ivoire'),
            dict(name='ANACACI SAN PEDRO', rccm='CI-ABJ-2020-B-11223', agrement='MINADER/2020/003',
                 pca='DIALLO Ibrahima', adg='TRAORE Fatoumata', director='CAMARA Lansana',
                 sig_manager='FOFANA Salimata', phone='+225 27 34 00 00 00',
                 email='anacaci@sanpedro.ci', address='San Pédro, Côte d\'Ivoire',
                 region='San Pédro', country='Côte d\'Ivoire'),
        ]
        self.coops = {}
        for data in coops:
            coop, _ = Cooperative.objects.get_or_create(rccm=data['rccm'], defaults=data)
            self.coops[data['name']] = coop
        self.stdout.write(f'  ✓ {len(self.coops)} coopératives')

    def _create_users(self):
        from apps.accounts.models import User
        from apps.producers.models import Agent

        coop = self.coops['COOPACI BEOUMI']

        # Super Admin — toujours (ré)initialisé pour garantir l'accès
        admin, _ = User.objects.get_or_create(
            username='admin',
            defaults=dict(full_name='Super Administrateur', role='super_admin',
                          email='admin@geocollect.ci', is_staff=True, is_superuser=True)
        )
        admin.role = 'super_admin'
        admin.is_staff = True
        admin.is_superuser = True
        admin.is_active = True
        admin.set_password('admin123')
        admin.save()

        # Cooperative user — toujours réinitialisé
        coop_user, _ = User.objects.get_or_create(
            username='coop',
            defaults=dict(full_name='COOP CACAO BEOUMI', role='cooperative',
                          email='coop@geocollect.ci', cooperative=coop)
        )
        coop_user.role = 'cooperative'
        coop_user.cooperative = coop
        coop_user.is_active = True
        coop_user.set_password('coop123')
        coop_user.save()

        # Agent — toujours réinitialisé
        agent_user, _ = User.objects.get_or_create(
            username='agent',
            defaults=dict(full_name='Kouassi Bernard', role='agent',
                          email='agent@geocollect.ci', cooperative=coop)
        )
        agent_user.role = 'agent'
        agent_user.cooperative = coop
        agent_user.is_active = True
        agent_user.set_password('agent123')
        agent_user.save()

        # Agent profile
        self.agent, _ = Agent.objects.get_or_create(
            user=agent_user,
            defaults=dict(cooperative=coop, code='AG-BEOU-001', zone='Zone Nord-Beoumi')
        )
        self.coop = coop
        self.stdout.write('  ✓ 3 utilisateurs (admin, coop, agent)')

    def _create_producers(self):
        from apps.producers.models import Producer

        producers_data = [
            dict(first_name='Jean', last_name='KONAN', phone='+225 07 11 22 33',
                 village='Akakro', section='BEOUMI', region='Bélier', gender='M', birth_year=1975,
                 field_id_base='BEOUMI000001'),
            dict(first_name='Marie', last_name='KOUASSI', phone='+225 07 22 33 44',
                 village='Kpouebo', section='BEOUMI', region='Bélier', gender='F', birth_year=1982,
                 field_id_base='BEOUMI000002'),
            dict(first_name='Yao', last_name='ATTA', phone='+225 07 33 44 55',
                 village='Broukro', section='BODOKRO', region='Bélier', gender='M', birth_year=1968,
                 field_id_base='BEOUMI000003'),
            dict(first_name='Fatou', last_name='COULIBALY', phone='+225 07 44 55 66',
                 village='Diabo', section='BODOKRO', region='Bélier', gender='F', birth_year=1979,
                 field_id_base='BEOUMI000004'),
            dict(first_name='Kofi', last_name='ASSI', phone='+225 07 55 66 77',
                 village='Niamoue', section='SAKASSOU', region='Bélier', gender='M', birth_year=1971,
                 field_id_base='BEOUMI000005'),
        ]
        self.producers = []
        for data in producers_data:
            p, _ = Producer.objects.get_or_create(
                field_id_base=data['field_id_base'],
                defaults=dict(**data, cooperative=self.coop, assigned_agent=self.agent,
                              country="Côte d'Ivoire")
            )
            self.producers.append(p)
        self.stdout.write(f'  ✓ {len(self.producers)} producteurs')

    def _create_parcels(self):
        from apps.parcels.models import Parcel
        from utils.eudr_validator import validate_eudr

        def make_poly(lat, lng, size=0.003):
            return {
                'type': 'Polygon',
                'coordinates': [[
                    [lng, lat], [lng + size, lat], [lng + size, lat + size],
                    [lng, lat + size * 0.8], [lng - size * 0.1, lat + size * 0.4],
                    [lng, lat],
                ]]
            }

        parcels_data = [
            dict(field_id='BEOUMI000001-M001', producer=self.producers[0], name='Parcelle Nord',
                 geometry=make_poly(7.672, -5.681), area_hectares=2.8, perimeter_meters=712,
                 vertex_count=6, culture='Cacao', village='Akakro', section='BEOUMI'),
            dict(field_id='BEOUMI000001-M002', producer=self.producers[0], name='Parcelle Sud',
                 geometry=make_poly(7.668, -5.678), area_hectares=3.1, perimeter_meters=798,
                 vertex_count=5, culture='Cacao', village='Akakro', section='BEOUMI'),
            dict(field_id='BEOUMI000002-M001', producer=self.producers[1], name='Parcelle Principale',
                 geometry=make_poly(7.662, -5.690, 0.004), area_hectares=4.2, perimeter_meters=920,
                 vertex_count=7, culture='Cacao', village='Kpouebo', section='BEOUMI'),
            dict(field_id='BEOUMI000003-M001', producer=self.producers[2], name='Grande Parcelle',
                 geometry=make_poly(7.680, -5.665, 0.005), area_hectares=5.6, perimeter_meters=1100,
                 vertex_count=8, culture='Café', village='Broukro', section='BODOKRO'),
            dict(field_id='BEOUMI000005-M001', producer=self.producers[4], name='Parcelle Familiale',
                 geometry=make_poly(7.658, -5.695, 0.0025), area_hectares=1.9, perimeter_meters=560,
                 vertex_count=5, culture='Cacao', village='Niamoue', section='SAKASSOU'),
        ]

        for data in parcels_data:
            if Parcel.objects.filter(field_id=data['field_id']).exists():
                continue
            result = validate_eudr(data['geometry'])
            eudr_score = result['eudr_score']
            eudr_status = ('compliant' if eudr_score >= 80 else
                           'non_compliant' if eudr_score < 60 else 'pending')
            Parcel.objects.create(
                **data,
                cooperative=self.coop,
                agent=self.agent,
                region='Bélier',
                country="Côte d'Ivoire",
                status='validated' if result['is_valid'] else 'rejected',
                eudr_score=eudr_score,
                eudr_status=eudr_status,
                validation_result=result,
                is_synced=True,
            )
        self.stdout.write(f'  ✓ {len(parcels_data)} parcelles avec validation EUDR')
