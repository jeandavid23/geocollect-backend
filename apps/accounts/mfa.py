"""
Double authentification (code à 6 chiffres, norme TOTP RFC 6238) — compatible Google Authenticator, Microsoft
Authenticator, Authy, 2FAS… Sans dépendance externe.

Connexion en deux temps : identifiant + mot de passe → jeton signé valable 5 minutes (`mfa_token`) → code à 6 chiffres
(ou un code de secours à usage unique) → jetons de session.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from django.contrib.auth.hashers import check_password, make_password
from django.core import signing

ISSUER = 'GeoCollect EUDR'
STEP = 30
DIGITS = 6
MFA_ROLES = ('owner', 'super_admin')
_SALT = 'geocollect-mfa-login'


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def _code(secret, counter):
    key = base64.b32decode(secret + '=' * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    o = digest[-1] & 0x0F
    value = (struct.unpack('>I', digest[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(value).zfill(DIGITS)


def verify_totp(secret, code, window=1, at=None):
    """Code valable sur la période courante ± `window` périodes de 30 s (décalage d'horloge du téléphone)."""
    code = ''.join(ch for ch in str(code or '') if ch.isdigit())
    if not secret or len(code) != DIGITS:
        return False
    counter = int((at or time.time()) // STEP)
    return any(hmac.compare_digest(_code(secret, counter + d), code) for d in range(-window, window + 1))


def otpauth_uri(user, secret):
    label = quote(f'{ISSUER}:{user.username}')
    return f'otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP}'


def new_recovery_codes(n=8):
    """Codes de secours (affichés une seule fois) et leurs empreintes (stockées)."""
    codes = [f'{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}' for _ in range(n)]
    return codes, [make_password(c) for c in codes]


def use_recovery_code(user, code):
    code = str(code or '').strip().upper()
    for i, h in enumerate(user.totp_recovery or []):
        if check_password(code, h):
            user.totp_recovery = [x for j, x in enumerate(user.totp_recovery) if j != i]
            user.save(update_fields=['totp_recovery'])
            return True
    return False


def check_code(user, code):
    """Code à 6 chiffres de l'application, ou code de secours (consommé)."""
    if verify_totp(user.totp_secret, code):
        return True
    return '-' in str(code or '') and use_recovery_code(user, code)


def login_token(user):
    return signing.dumps({'u': str(user.pk)}, salt=_SALT)


def user_from_login_token(token, max_age=300):
    from .models import User
    try:
        data = signing.loads(token, salt=_SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    return User.objects.filter(pk=data.get('u'), is_active=True).first()
