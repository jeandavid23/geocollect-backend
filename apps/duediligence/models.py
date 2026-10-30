"""
Diligence raisonnée EUDR (art. 9 à 12) : analyses par convergence de preuves, décisions humaines, aires protégées.

Une analyse est conservée au moins 5 ans (art. 12) : les résultats par parcelle (preuves de chaque source, classement,
sortie WHISP brute) sont stockés compressés ; les décisions humaines s'ajoutent sans jamais remplacer le résultat
automatique (historique complet, rien n'est modifié).
"""
import gzip
import json
import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone


def _five_years():
    return timezone.now() + timedelta(days=5 * 366)


class ProtectedArea(models.Model):
    """Aires protégées et zones clés de biodiversité (WDPA, KBA) importées en base (commande import_protected_areas)."""
    SOURCES = {'WDPA': 'WDPA (UNEP-WCMC / UICN)', 'KBA': 'Key Biodiversity Area (BirdLife)'}

    id = models.BigAutoField(primary_key=True)
    source = models.CharField(max_length=10, choices=list(SOURCES.items()), db_index=True)
    source_id = models.CharField(max_length=40, blank=True)
    name = models.CharField(max_length=255, blank=True)
    designation = models.CharField(max_length=255, blank=True)
    iucn_category = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=60, blank=True)
    iso3 = models.CharField(max_length=20, blank=True)
    version = models.CharField(max_length=40, blank=True)
    geometry = models.JSONField()

    class Meta:
        ordering = ['source', 'name']

    def __str__(self):
        return f'{self.source} — {self.name}'


class DDSettings(models.Model):
    """Seuils de l'analyse propres à une coopérative (vide = valeurs par défaut du moteur)."""
    cooperative = models.OneToOneField('cooperatives.Cooperative', on_delete=models.CASCADE, related_name='dd_settings')
    thresholds = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class DDAnalysis(models.Model):
    STATUSES = {'pending': 'En attente', 'running': 'En cours', 'done': 'Terminée', 'error': 'Erreur'}
    WHISP = {'non_execute': 'WHISP non exécuté', 'en_cours': 'WHISP en cours', 'execute': 'WHISP exécuté',
             'importe': 'Résultats WHISP importés', 'erreur': 'WHISP en erreur'}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cooperative = models.ForeignKey('cooperatives.Cooperative', on_delete=models.CASCADE, related_name='dd_analyses')
    lot = models.ForeignKey('lots.Lot', null=True, blank=True, on_delete=models.SET_NULL, related_name='dd_analyses')
    created_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=10, choices=list(STATUSES.items()), default='pending', db_index=True)
    progress = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    scope = models.JSONField(default=dict)               # {"source": "parcels|legacy|both", "lot": id, "label": …}
    thresholds = models.JSONField(default=dict)
    report_meta = models.JSONField(default=dict, blank=True)   # page de garde : produit, code SH, quantité, période…
    summary = models.JSONField(default=dict, blank=True)
    manifest = models.JSONField(default=dict, blank=True)
    results_gz = models.BinaryField(null=True, blank=True)
    whisp_status = models.CharField(max_length=12, choices=list(WHISP.items()), default='non_execute')
    whisp_version = models.CharField(max_length=40, blank=True)
    whisp_message = models.TextField(blank=True)
    error = models.TextField(blank=True)
    keep_until = models.DateTimeField(default=_five_years)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['cooperative', '-created_at'])]

    def set_results(self, results):
        self.results_gz = gzip.compress(json.dumps(results, ensure_ascii=False, default=str).encode('utf-8'))

    def get_results(self):
        if not self.results_gz:
            return []
        return json.loads(gzip.decompress(bytes(self.results_gz)).decode('utf-8'))


class DDDecision(models.Model):
    """Décision humaine sur une parcelle (art. 11) — s'ajoute au résultat automatique, ne le remplace jamais."""
    DECISIONS = {'confirmer': 'Risque confirmé', 'lever': 'Risque levé', 'visite': 'Visite terrain demandée',
                 'exclure': 'Parcelle exclue du lot', 'documents': 'Documents demandés'}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis = models.ForeignKey(DDAnalysis, on_delete=models.CASCADE, related_name='decisions')
    parcel_key = models.CharField(max_length=80, db_index=True)       # « parcel:<uuid> » ou « legacy:<uuid> »
    decision = models.CharField(max_length=12, choices=list(DECISIONS.items()))
    comment = models.TextField(blank=True)
    author = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    author_name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    attachment_name = models.CharField(max_length=255, blank=True)
    attachment_type = models.CharField(max_length=100, blank=True)
    attachment_data = models.TextField(blank=True)                    # base64 (≤ 3 Mo) : photo terrain, titre foncier…

    class Meta:
        ordering = ['created_at']
