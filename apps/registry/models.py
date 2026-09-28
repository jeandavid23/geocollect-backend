import uuid
from django.db import models


class RegistrySheet(models.Model):
    """
    Une feuille du registre d'une coopérative (équivalent d'un onglet Excel).
    `data` est un tableau de lignes ; chaque cellule est un nombre, un texte, un booléen ou null.
    Une cellule qui commence par « = » est une formule (syntaxe Excel en anglais, ex. =SUM(B2:B10)).
    La ligne 1 contient les entêtes, comme dans le fichier d'origine.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cooperative = models.ForeignKey('cooperatives.Cooperative', on_delete=models.CASCADE, related_name='registry_sheets')
    name = models.CharField(max_length=100, verbose_name='Nom de la feuille')
    position = models.PositiveSmallIntegerField(default=0, verbose_name='Ordre')
    data = models.JSONField(default=list, blank=True, verbose_name='Cellules')
    col_widths = models.JSONField(default=list, blank=True, verbose_name='Largeur des colonnes (px)')
    source_file = models.CharField(max_length=255, blank=True, verbose_name='Fichier Excel d\'origine')
    updated_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Feuille du registre'
        verbose_name_plural = 'Feuilles du registre'
        ordering = ['cooperative', 'position', 'created_at']
        constraints = [
            models.UniqueConstraint(fields=['cooperative', 'name'], name='registry_sheet_unique_name'),
        ]

    def __str__(self):
        return f'{self.cooperative} — {self.name}'
