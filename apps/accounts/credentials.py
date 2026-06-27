"""Génération d'identifiants de connexion (username + mot de passe)."""
import re
import secrets
import unicodedata


def _slugify(value: str) -> str:
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^a-zA-Z0-9]+', '', value).lower()
    return value


def generate_username(full_name: str) -> str:
    """Ex: 'Kouassi Bernard' -> 'k.bernard' (unique en base)."""
    from .models import User

    parts = [p for p in full_name.strip().split() if p]
    if len(parts) >= 2:
        base = f'{_slugify(parts[0])[:1]}.{_slugify(parts[-1])}'
    elif parts:
        base = _slugify(parts[0])
    else:
        base = 'user'
    base = base or 'user'

    username = base
    i = 1
    while User.objects.filter(username=username).exists():
        i += 1
        username = f'{base}{i}'
    return username


def generate_password(length: int = 10) -> str:
    """Mot de passe lisible (lettres + chiffres + 1 symbole)."""
    alphabet = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    core = ''.join(secrets.choice(alphabet) for _ in range(length - 1))
    return f'{core}{secrets.choice("!@#$%")}'
