"""Création de notifications : membres de la coopérative + son super admin gestionnaire + le propriétaire."""


def notify_cooperative(cooperative, *, title, message='', ntype='info', exclude_user=None):
    """Notifie les utilisateurs de la coopérative, son super admin gestionnaire et le propriétaire."""
    from .models import User, Notification

    recipients = set()
    if cooperative:
        for u in User.objects.filter(cooperative=cooperative, is_active=True):
            recipients.add(u.id)
    # Le super admin qui gère la coopérative (son client) et le propriétaire de la plateforme
    if cooperative and cooperative.managed_by_id:
        recipients.add(cooperative.managed_by_id)
    for u in User.objects.filter(role='owner', is_active=True):
        recipients.add(u.id)
    if exclude_user is not None:
        recipients.discard(getattr(exclude_user, 'id', exclude_user))

    objs = [
        Notification(recipient_id=uid, cooperative=cooperative, type=ntype,
                     title=title, message=message)
        for uid in recipients
    ]
    if objs:
        Notification.objects.bulk_create(objs)
