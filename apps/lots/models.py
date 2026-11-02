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


class Delivery(models.Model):
    """
    Pesée : livraison d'un producteur, enregistrée au point de collecte (fiche de pesée remise au producteur).
    Elle n'est jamais supprimée : une erreur se corrige par une annulation motivée, puis une nouvelle pesée.
    Le poids net (brut − tare des sacs − réfaction) et le montant sont calculés par le serveur.
    """
    STATUSES = {'valide': 'Valide', 'annulee': 'Annulée'}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cooperative = models.ForeignKey('cooperatives.Cooperative', on_delete=models.CASCADE, related_name='deliveries')
    producer = models.ForeignKey('producers.Producer', on_delete=models.PROTECT, related_name='deliveries')
    lot = models.ForeignKey(Lot, null=True, blank=True, on_delete=models.SET_NULL, related_name='deliveries')
    number = models.CharField(max_length=40, unique=True, verbose_name='N° de fiche de pesée')
    campaign = models.CharField(max_length=20, verbose_name='Campagne')
    product = models.CharField(max_length=10, choices=list(Lot.PRODUCTS.items()), default='cacao')
    delivery_date = models.DateField(verbose_name='Date de pesée')
    collection_point = models.CharField(max_length=120, blank=True, verbose_name='Point de collecte / magasin')
    bags = models.PositiveIntegerField(default=0, verbose_name='Nombre de sacs')
    gross_kg = models.DecimalField(max_digits=10, decimal_places=1, verbose_name='Poids brut (kg)')
    tare_kg = models.DecimalField(max_digits=10, decimal_places=1, default=0, verbose_name='Tare des sacs (kg)')
    deduction_kg = models.DecimalField(max_digits=10, decimal_places=1, default=0,
                                       verbose_name='Réfaction (humidité, déchets, kg)')
    net_kg = models.DecimalField(max_digits=10, decimal_places=1, verbose_name='Poids net (kg)')
    moisture_pct = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, verbose_name='Humidité (%)')
    quality = models.CharField(max_length=60, blank=True, verbose_name='Qualité / grade')
    unit_price = models.DecimalField(max_digits=10, decimal_places=0, null=True, blank=True, verbose_name='Prix (FCFA/kg)')
    amount = models.DecimalField(max_digits=14, decimal_places=0, null=True, blank=True, verbose_name='Montant (FCFA)')
    weigher = models.CharField(max_length=120, blank=True, verbose_name='Peseur')
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=list(STATUSES.items()), default='valide')
    cancel_reason = models.CharField(max_length=255, blank=True)
    cancelled_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-delivery_date', '-created_at']
        verbose_name = 'Pesée'
        indexes = [models.Index(fields=['cooperative', 'campaign', 'status'])]

    def __str__(self):
        return self.number


class TransportSlip(models.Model):
    """
    Fiche d'accompagnement : document qui suit un lot (ou une partie, un camion) de la coopérative jusqu'à
    l'acheteur. À l'arrivée, le poids reçu est saisi : l'écart avec le poids au départ est tracé.
    """
    STATUSES = {'emise': 'Émise (en route)', 'livree': 'Livrée', 'annulee': 'Annulée'}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cooperative = models.ForeignKey('cooperatives.Cooperative', on_delete=models.CASCADE, related_name='transport_slips')
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name='transport_slips')
    number = models.CharField(max_length=40, unique=True, verbose_name="N° de fiche d'accompagnement")
    issued_at = models.DateTimeField(auto_now_add=True)
    origin = models.CharField(max_length=160, blank=True, verbose_name='Lieu de départ (magasin)')
    destination = models.CharField(max_length=200, verbose_name='Destination')
    consignee = models.CharField(max_length=200, blank=True, verbose_name='Destinataire (acheteur / exportateur)')
    carrier = models.CharField(max_length=160, blank=True, verbose_name='Transporteur')
    vehicle_plate = models.CharField(max_length=40, verbose_name='Immatriculation du camion')
    trailer_plate = models.CharField(max_length=40, blank=True, verbose_name='Immatriculation de la remorque')
    driver_name = models.CharField(max_length=120, verbose_name='Chauffeur')
    driver_phone = models.CharField(max_length=40, blank=True)
    driver_licence = models.CharField(max_length=60, blank=True, verbose_name='N° de permis')
    bags = models.PositiveIntegerField(default=0)
    gross_weight_kg = models.DecimalField(max_digits=12, decimal_places=1, verbose_name='Poids au départ (kg)')
    seals = models.CharField(max_length=200, blank=True, verbose_name='N° de plombs / scellés')
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=list(STATUSES.items()), default='emise')
    received_at = models.DateTimeField(null=True, blank=True)
    received_weight_kg = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True,
                                             verbose_name="Poids à l'arrivée (kg)")
    received_by = models.CharField(max_length=160, blank=True)
    reception_notes = models.TextField(blank=True)
    created_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-issued_at']
        verbose_name = "Fiche d'accompagnement"

    def __str__(self):
        return self.number


class ProcessingBatch(models.Model):
    """
    Lot d'usinage (nettoyage, tri, calibrage, ensachage export) ou de transformation (broyage, pressage, poudre),
    à l'usine. Ses entrées sont des lots de coopérative expédiés, ou les produits d'un usinage précédent ; ses sorties
    sont les produits obtenus. Le bilan matière (sorties ≤ entrées) est contrôlé, et chaque produit fini remonte
    jusqu'aux producteurs, pesées et parcelles d'origine.
    """
    OPERATIONS = {'usinage': 'Usinage', 'transformation': 'Transformation'}
    STATUSES = {'en_cours': 'En cours', 'cloture': 'Clôturé'}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=40, unique=True)
    operation = models.CharField(max_length=15, choices=list(OPERATIONS.items()), default='usinage')
    plant = models.CharField(max_length=200, verbose_name='Usine')
    process_date = models.DateField()
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=list(STATUSES.items()), default='en_cours')
    # coopératives d'origine (déduites des entrées) : un lot d'usinage n'est visible que de qui les gère toutes
    cooperatives = models.ManyToManyField('cooperatives.Cooperative', related_name='processing_batches')
    created_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-process_date', '-created_at']
        verbose_name = "Lot d'usinage / transformation"

    def __str__(self):
        return self.code


class ProcessingInput(models.Model):
    """Entrée : une quantité prise sur un lot de coopérative ou sur les produits d'un lot d'usinage précédent."""
    batch = models.ForeignKey(ProcessingBatch, on_delete=models.CASCADE, related_name='inputs')
    lot = models.ForeignKey(Lot, null=True, blank=True, on_delete=models.PROTECT, related_name='processing_inputs')
    source_batch = models.ForeignKey(ProcessingBatch, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name='consumed_by')
    weight_kg = models.DecimalField(max_digits=12, decimal_places=1)

    class Meta:
        ordering = ['id']


class ProcessingOutput(models.Model):
    """Produit obtenu (fèves usinées, masse, beurre, tourteau, poudre…) ou sous-produit (déchets, coques)."""
    PRODUCTS = {'feves_usinees': 'Fèves usinées (export)', 'masse': 'Masse / liqueur de cacao', 'beurre': 'Beurre de cacao',
                'tourteau': 'Tourteau', 'poudre': 'Poudre de cacao', 'coques': 'Coques', 'dechets': 'Déchets, poussières',
                'autre': 'Autre'}
    BYPRODUCTS = ('coques', 'dechets')            # ne peuvent pas entrer dans une transformation

    batch = models.ForeignKey(ProcessingBatch, on_delete=models.CASCADE, related_name='outputs')
    product = models.CharField(max_length=20, choices=list(PRODUCTS.items()))
    weight_kg = models.DecimalField(max_digits=12, decimal_places=1)
    reference = models.CharField(max_length=80, blank=True, verbose_name='Référence (grade, n° de lot client…)')

    class Meta:
        ordering = ['id']
