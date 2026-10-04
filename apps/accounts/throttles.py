"""
Limitation des tentatives de connexion, fiable derrière le proxy de Render :
  - compteur PARTAGÉ entre les processus gunicorn (cache en base de données) ;
  - compté par IDENTIFIANT visé (non falsifiable, contrairement à l'adresse IP
    transmise par le proxy) : un compte ne peut pas être attaqué au-delà de 10 essais / minute.
"""
from django.core.cache import caches
from rest_framework.throttling import SimpleRateThrottle


class LoginThrottle(SimpleRateThrottle):
    cache = caches['shared']
    scope = 'login'

    def get_cache_key(self, request, view):
        username = str(request.data.get('username', '')).strip().lower()[:150] if hasattr(request, 'data') else ''
        return self.cache_format % {'scope': self.scope, 'ident': username or self.get_ident(request)}


class PasswordThrottle(SimpleRateThrottle):
    cache = caches['shared']
    scope = 'password'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': str(request.user.pk)}


class MessageThrottle(SimpleRateThrottle):
    """Envoi de messages : 10 par minute et par compte."""
    cache = caches['shared']
    scope = 'message'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': str(request.user.pk)}
