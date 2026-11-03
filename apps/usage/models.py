from django.db import models


class PageView(models.Model):
    """
    Page ouverte dans l'application web par un compte (statistiques d'utilisation, comme un outil d'audience, mais
    gardées dans GeoCollect). Adresse normalisée (sans identifiants), conservée RETENTION_DAYS jours.
    """
    DEVICES = {'ordinateur': 'Ordinateur', 'mobile': 'Téléphone', 'tablette': 'Tablette'}

    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='page_views')
    session = models.CharField(max_length=32)                 # identifiant aléatoire de l'onglet (30 min d'inactivité)
    path = models.CharField(max_length=120)
    device = models.CharField(max_length=10, choices=list(DEVICES.items()), default='ordinateur')
    at = models.DateTimeField(db_index=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'at'])]
        verbose_name = 'Page vue'
