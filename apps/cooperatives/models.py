import uuid
from django.db import models


class Cooperative(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, verbose_name='Nom')
    rccm = models.CharField(max_length=100, unique=True, verbose_name='RCCM')
    agrement = models.CharField(max_length=100, blank=True, verbose_name='Agrément')
    logo = models.ImageField(upload_to='cooperative_logos/', blank=True, null=True)
    # Dirigeants
    pca = models.CharField(max_length=255, blank=True, verbose_name='PCA')
    adg = models.CharField(max_length=255, blank=True, verbose_name='ADG')
    director = models.CharField(max_length=255, blank=True, verbose_name='Directeur')
    sig_manager = models.CharField(max_length=255, blank=True, verbose_name='Responsable SIG')
    # Contact
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True, verbose_name='Adresse')
    region = models.CharField(max_length=100, blank=True, verbose_name='Région')
    country = models.CharField(max_length=100, default="Côte d'Ivoire", verbose_name='Pays')
    is_active = models.BooleanField(default=True, verbose_name='Active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Coopérative'
        verbose_name_plural = 'Coopératives'
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def producer_count(self):
        return self.producers.count()

    @property
    def parcel_count(self):
        return self.parcels.count()

    @property
    def total_hectares(self):
        result = self.parcels.aggregate(total=models.Sum('area_hectares'))
        return round(result['total'] or 0, 2)

    @property
    def agent_count(self):
        return self.agents.count()


class CooperativeDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cooperative = models.ForeignKey(Cooperative, on_delete=models.CASCADE, related_name='documents')
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to='cooperative_documents/')
    doc_type = models.CharField(max_length=50, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.cooperative.name} — {self.name}'
