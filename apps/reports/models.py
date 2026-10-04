import uuid

from django.db import models


class AnalysisReport(models.Model):
    """
    Rapport d'un traitement (déforestation, Polygon Validator, Self-intersection, Polygon & GMR) ou d'un import,
    gardé pour être retéléchargé depuis « Rapports ». Le contenu complet (synthèse, tableaux, polygones)
    est stocké compressé (gzip) : ~3 Mo pour 10 000 polygones.
    """
    KINDS = {
        'deforestation': 'Analyse déforestation',
        'validator': 'Polygon Validator (chevauchements)',
        'selfintersection': 'Self-intersection (nettoyage)',
        'gmr': 'Polygon & GMR (registre × polygones)',
        'import_registry': 'Import du registre',
        'import_polygons': 'Import de polygones',
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cooperative = models.ForeignKey('cooperatives.Cooperative', on_delete=models.CASCADE, related_name='reports')
    kind = models.CharField(max_length=30, choices=list(KINDS.items()))
    title = models.CharField(max_length=200)
    source = models.CharField(max_length=255, blank=True)        # fichier ou source des polygones
    summary = models.JSONField(default=list, blank=True)          # [[libellé, valeur], …] (affiché sans télécharger)
    feature_count = models.PositiveIntegerField(default=0)
    content = models.BinaryField()                                # JSON gzip : {summary, tables, features}
    size_bytes = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['cooperative', 'kind', '-created_at'])]
        verbose_name = 'Rapport de traitement'

    def __str__(self):
        return f'{self.get_kind_display()} — {self.cooperative} — {self.created_at:%d/%m/%Y %H:%M}'
