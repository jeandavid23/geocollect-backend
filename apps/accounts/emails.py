"""Envoi d'emails de confirmation d'inscription avec les accès."""
from django.conf import settings
from django.core.mail import send_mail


def send_credentials_email(*, to_email, full_name, role_label, username, password,
                           cooperative_name=None, code=None):
    """Envoie les identifiants de connexion par email. Ne lève jamais d'exception
    (l'échec d'email ne doit pas bloquer la création du compte)."""
    if not to_email:
        return False

    login_url = settings.FRONTEND_URL
    lines = [
        f"Bonjour {full_name},",
        "",
        f"Votre compte {role_label} a été créé sur la plateforme GeoCollect EUDR.",
        "",
        "Vos accès de connexion :",
        f"  • Identifiant : {username}",
        f"  • Mot de passe : {password}",
    ]
    if code:
        lines.append(f"  • Code : {code}")
    if cooperative_name:
        lines.append(f"  • Coopérative : {cooperative_name}")
    lines += [
        "",
        f"Connectez-vous ici : {login_url}",
        "",
        "Pour votre sécurité, changez votre mot de passe après la première connexion "
        "(menu « Mon compte »).",
        "",
        "— L'équipe GeoCollect EUDR",
    ]
    body = "\n".join(lines)

    try:
        send_mail(
            subject="Vos accès GeoCollect EUDR",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to_email],
            fail_silently=True,
        )
        return True
    except Exception:
        return False
