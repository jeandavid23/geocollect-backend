"""Création de notifications : destinataires = membres de la coopérative + tous les super admins."""


def notify_cooperative(cooperative, *, title, message='', ntype='info', exclude_user=None):
    """Crée une notification pour tous les utilisateurs de la coopérative + tous les super admins."""
    from .models import User, Notification

    recipients = set()
    if cooperative:
        for u in User.objects.filter(cooperative=cooperative, is_active=True):
            recipients.add(u.id)
    # Super admins reçoivent TOUTES les notifications
    for u in User.objects.filter(role='super_admin', is_active=True):
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
