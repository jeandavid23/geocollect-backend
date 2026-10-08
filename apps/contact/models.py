import uuid

from django.db import models


class DemoRequest(models.Model):
    """Demande de démonstration envoyée depuis la page de présentation publique."""
    PROFILES = {'cooperative': 'Coopérative', 'exportateur': 'Exportateur', 'certification': 'Organisme / certification', 'autre': 'Autre'}
    STATUSES = {'nouvelle': 'Nouvelle', 'contactee': 'Contactée', 'demo': 'Démo faite', 'client': 'Devenue cliente', 'sans_suite': 'Sans suite'}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField(max_length=150)
    organization = models.CharField(max_length=200)
    profile = models.CharField(max_length=20, choices=list(PROFILES.items()), default='cooperative')
    phone = models.CharField(max_length=40)
    email = models.EmailField(blank=True)
    producers = models.PositiveIntegerField(null=True, blank=True, verbose_name='Nombre approximatif de producteurs')
    plan = models.CharField(max_length=30, blank=True, verbose_name='Formule envisagée')
    message = models.TextField(blank=True, max_length=2000)
    status = models.CharField(max_length=20, choices=list(STATUSES.items()), default='nouvelle')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Demande de démonstration'

    def __str__(self):
        return f'{self.organization} — {self.full_name}'
