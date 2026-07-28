import uuid
from django.db import models


class Agent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField('accounts.User', on_delete=models.CASCADE, related_name='agent_profile')
    cooperative = models.ForeignKey('cooperatives.Cooperative', on_delete=models.CASCADE, related_name='agents')
    code = models.CharField(max_length=50, unique=True, verbose_name='Code agent')
    zone = models.CharField(max_length=255, blank=True, verbose_name='Zone de travail')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Agent Mappeur'
        verbose_name_plural = 'Agents Mappeurs'

    def __str__(self):
        return f'{self.user.full_name} ({self.code})'

    @property
    def parcel_count(self):
        return self.parcels.count()

    @property
    def total_hectares(self):
        result = self.parcels.aggregate(total=models.Sum('area_hectares'))
        return round(result['total'] or 0, 2)


class Producer(models.Model):
    class Gender(models.TextChoices):
        MALE = 'M', 'Homme'
        FEMALE = 'F', 'Femme'

    class FarmType(models.TextChoices):
        SMALL = 'small', 'Petite'
        LARGE = 'large', 'Grande'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cooperative = models.ForeignKey('cooperatives.Cooperative', on_delete=models.CASCADE, related_name='producers')
    assigned_agent = models.ForeignKey(Agent, null=True, blank=True, on_delete=models.SET_NULL, related_name='producers')

    # ══ EXPLOITATION AGRICOLE (modèle GMR EUDR) ══
    # 1. Identifiant interne unique d'exploitation (ex: BEOUMI000245)
    field_id_base = models.CharField(max_length=50, unique=True, verbose_name='Identifiant interne unique')
    # 2. Identifiant national d'exploitation (obligatoire Brésil)
    national_farm_id = models.CharField(max_length=100, blank=True, verbose_name="Identifiant national d'exploitation")
    # 3. Village / Ville
    village = models.CharField(max_length=100, verbose_name='Village / Ville')
    # 4. District / État / Province
    district = models.CharField(max_length=100, blank=True, verbose_name='District / État / Province')
    # 5. Région d'inspection interne
    region = models.CharField(max_length=100, verbose_name="Région d'inspection interne")
    # 6. Superficie totale de l'exploitation (ha)
    total_area_ha = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='Superficie totale (ha)')
    # 7. Type d'exploitation (petite/grande)
    farm_type = models.CharField(max_length=10, choices=FarmType.choices, default=FarmType.SMALL, verbose_name="Type d'exploitation")
    # 8. Nombre d'unités agricoles
    num_units = models.PositiveSmallIntegerField(default=1, verbose_name="Nombre d'unités agricoles")
    # 9. Année / nombre de cultures certifiées
    certification_year = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Année de certification')

    # ══ OPÉRATEUR DE L'EXPLOITATION ══
    # 10-11. Prénom / Nom
    first_name = models.CharField(max_length=100, verbose_name='Prénom (opérateur)')
    last_name = models.CharField(max_length=100, verbose_name='Nom (opérateur)')
    # 12. Numéro de téléphone
    phone = models.CharField(max_length=20, blank=True, verbose_name='Téléphone (opérateur)')
    # 13. Numéro d'identification national (CNI)
    national_id = models.CharField(max_length=50, blank=True, verbose_name="N° identification national")
    # 14. Genre
    gender = models.CharField(max_length=1, choices=Gender.choices, default=Gender.MALE, verbose_name='Genre (opérateur)')
    # 15. Année de naissance
    birth_year = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Année de naissance (opérateur)')

    # ══ PROPRIÉTAIRE DE L'EXPLOITATION (si différent de l'opérateur) ══
    owner_first_name = models.CharField(max_length=100, blank=True, verbose_name='Prénom (propriétaire)')
    owner_last_name = models.CharField(max_length=100, blank=True, verbose_name='Nom (propriétaire)')
    owner_phone = models.CharField(max_length=20, blank=True, verbose_name='Téléphone (propriétaire)')
    owner_national_id = models.CharField(max_length=50, blank=True, verbose_name="N° identification (propriétaire)")
    owner_gender = models.CharField(max_length=1, choices=Gender.choices, blank=True, verbose_name='Genre (propriétaire)')

    # ══ TRAVAILLEURS ══
    permanent_workers = models.PositiveSmallIntegerField(default=0, verbose_name='Travailleurs permanents')
    temporary_workers = models.PositiveSmallIntegerField(default=0, verbose_name='Travailleurs temporaires / an')

    # ══ DONNÉES D'INSPECTION INTERNE ══
    inspector_name = models.CharField(max_length=200, blank=True, verbose_name="Nom de l'inspecteur interne")
    inspection_year = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Année d'inspection")
    inspection_month = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Mois d'inspection")
    inspection_day = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Jour d'inspection")

    # Champs internes conservés
    section = models.CharField(max_length=100, verbose_name='Section')
    country = models.CharField(max_length=100, default="Côte d'Ivoire", verbose_name='Pays')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Producteur'
        verbose_name_plural = 'Producteurs'
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f'{self.last_name} {self.first_name} ({self.field_id_base})'

    @property
    def full_name(self):
        return f'{self.last_name} {self.first_name}'

    @property
    def parcel_count(self):
        return self.parcels.count()

    @property
    def total_hectares(self):
        result = self.parcels.aggregate(total=models.Sum('area_hectares'))
        return round(result['total'] or 0, 2)
