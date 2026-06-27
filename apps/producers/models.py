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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cooperative = models.ForeignKey('cooperatives.Cooperative', on_delete=models.CASCADE, related_name='producers')
    assigned_agent = models.ForeignKey(Agent, null=True, blank=True, on_delete=models.SET_NULL, related_name='producers')
    # FIELD ID Base (ex: BEOUMI000245)
    field_id_base = models.CharField(max_length=50, unique=True, verbose_name='FIELD ID Base')
    # Identity
    first_name = models.CharField(max_length=100, verbose_name='Prénom')
    last_name = models.CharField(max_length=100, verbose_name='Nom')
    phone = models.CharField(max_length=20, blank=True)
    national_id = models.CharField(max_length=50, blank=True, verbose_name='CNI')
    gender = models.CharField(max_length=1, choices=Gender.choices, default=Gender.MALE)
    birth_year = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Année de naissance')
    # Location
    village = models.CharField(max_length=100, verbose_name='Village')
    section = models.CharField(max_length=100, verbose_name='Section')
    region = models.CharField(max_length=100, verbose_name='Région')
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
