"""
Suivi d'utilisation des outils (pour le tableau de bord du propriétaire) : une ligne du journal d'activité
par traitement — action « run_tool », ressource = identifiant du module, détails = nombre de polygones traités.
"""
import logging

from .models import ActivityLog

log = logging.getLogger(__name__)


def log_tool_run(request, module, items):
    try:
        ActivityLog.objects.create(user=request.user, action='run_tool', resource=module,
                                   details=str(int(items or 0)), ip_address=request.META.get('REMOTE_ADDR'))
    except Exception:  # noqa: BLE001 — le suivi ne doit jamais faire échouer un traitement
        log.exception('Suivi d\'utilisation')
