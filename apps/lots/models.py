import uuid

from django.db import models


class Lot(models.Model):
    """
    Lot de produit (cacao, café) constitué par une coopérative à partir des livraisons de ses producteurs.
    La fiche de lot relie chaque sac à ses producteurs et à leurs parcelles, pour la déclaration de diligence raisonnable.
    """
    PRODUCTS = {'cacao': 'Cacao (fèves)', 'cafe': 'Café (vert)', 'autre': 'Autre'}
    STATUSES = {'brouillon': 'Brouillon', 'valide': 'Validé', 'expedie': 'Expédié'}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cooperative = models.ForeignKey('cooperatives.Cooperative', on_delete=models.CASCADE, related_name='lots')
    code = models.CharField(max_length=40, unique=True, verbose_name='Numéro de lot')
    campaign = models.CharField(max_length=20, verbose_name='Campagne')                  # ex. 2026-2027
    product = models.CharField(max_length=10, choices=list(PRODUCTS.items()), default='cacao')
    lot_date = models.DateField(verbose_name='Date de constitution')
    bags = models.PositiveIntegerField(default=0, verbose_name='Nombre de sacs')
    gross_weight_kg = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True, verbose_name='Poids brut (kg)')
    quality = models.CharField(max_length=60, blank=True, verbose_name='Qualité / grade')
    warehouse = models.CharField(max_length=120, blank=True, verbose_name='Magasin')
    buyer = models.CharField(max_length=200, blank=True, verbose_name='Acheteur / exportateur')
    destination = models.CharField(max_length=200, blank=True, verbose_name='Destination')
    transport = models.CharField(max_length=120, blank=True, verbose_name='Transport (camion, immatriculation)')
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=list(STATUSES.items()), default='brouillon')
    created_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-lot_date', '-created_at']
        verbose_name = 'Lot'

    def __str__(self):
        return self.code


class LotLine(models.Model):
    """Livraison d'un producteur entrant dans le lot."""
    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name='lines')
    producer = models.ForeignKey('producers.Producer', on_delete=models.PROTECT, related_name='lot_lines')
    weight_kg = models.DecimalField(max_digits=10, decimal_places=1)
    bags = models.PositiveIntegerField(default=0)
    delivery_date = models.DateField(null=True, blank=True)
    receipt = models.CharField(max_length=60, blank=True, verbose_name='N° de bon / reçu')

    class Meta:
        ordering = ['id']
