import uuid
from django.db import models


class Parcel(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Brouillon'
        PENDING = 'pending', 'En attente'
        VALIDATED = 'validated', 'Validée'
        REJECTED = 'rejected', 'Rejetée'

    class EUDRStatus(models.TextChoices):
        COMPLIANT = 'compliant', 'Conforme'
        NON_COMPLIANT = 'non_compliant', 'Non conforme'
        PENDING = 'pending', 'En attente'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # References
    producer = models.ForeignKey('producers.Producer', on_delete=models.CASCADE, related_name='parcels')
    cooperative = models.ForeignKey('cooperatives.Cooperative', on_delete=models.CASCADE, related_name='parcels')
    agent = models.ForeignKey('producers.Agent', null=True, on_delete=models.SET_NULL, related_name='parcels')
    # FIELD ID (ex: BEOUMI000245-M001)
    field_id = models.CharField(max_length=80, unique=True, verbose_name='FIELD ID')
    name = models.CharField(max_length=255, blank=True, verbose_name='Nom de la parcelle')
    # Geometry stored as GeoJSON (JSONField — migrable vers PostGIS)
    geometry = models.JSONField(verbose_name='Géométrie GeoJSON')
    area_hectares = models.FloatField(default=0.0, verbose_name='Superficie (ha)')
    perimeter_meters = models.FloatField(default=0.0, verbose_name='Périmètre (m)')
    vertex_count = models.PositiveIntegerField(default=0, verbose_name='Nombre de sommets')
    # Culture & Location
    culture = models.CharField(max_length=100, default='Cacao', verbose_name='Culture')
    village = models.CharField(max_length=100, verbose_name='Village')
    section = models.CharField(max_length=100, verbose_name='Section')
    region = models.CharField(max_length=100, verbose_name='Région')
    country = models.CharField(max_length=100, default="Côte d'Ivoire", verbose_name='Pays')
    # Validation
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    eudr_score = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Score EUDR (%)')
    eudr_status = models.CharField(max_length=20, choices=EUDRStatus.choices, default=EUDRStatus.PENDING)
    validation_result = models.JSONField(null=True, blank=True, verbose_name='Résultat validation EUDR')
    # GPS session metadata
    mapping_started_at = models.DateTimeField(null=True, blank=True)
    mapping_ended_at = models.DateTimeField(null=True, blank=True)
    # Sync
    is_synced = models.BooleanField(default=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Parcelle'
        verbose_name_plural = 'Parcelles'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.field_id} — {self.area_hectares:.2f} ha ({self.get_eudr_status_display()})'

    def run_eudr_validation(self):
        from utils.eudr_validator import validate_eudr
        result = validate_eudr(self.geometry, country=self.country, culture=self.culture)
        self.eudr_score = result['eudr_score']
        self.eudr_status = (
            self.EUDRStatus.COMPLIANT if result['eudr_score'] >= 80
            else self.EUDRStatus.NON_COMPLIANT if result['eudr_score'] < 60
            else self.EUDRStatus.PENDING
        )
        self.validation_result = result
        self.status = self.Status.VALIDATED if result['is_valid'] else self.Status.REJECTED
        self.save(update_fields=['eudr_score', 'eudr_status', 'validation_result', 'status', 'updated_at'])
        return result
