from pathlib import Path
from datetime import timedelta
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

_DEV_SECRET = 'django-insecure-geocollect-eudr-dev-key-change-in-production'
SECRET_KEY = config('SECRET_KEY', default=_DEV_SECRET)
DEBUG = config('DEBUG', default=True, cast=bool)
if not DEBUG and (SECRET_KEY == _DEV_SECRET or len(SECRET_KEY) < 32):
    # Sans vraie clé, les jetons de connexion pourraient être forgés : on refuse de démarrer
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured('SECRET_KEY absente ou trop courte en production.')
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=lambda v: [s.strip() for s in v.split(',')])

# ─── Apps ──────────────────────────────────────────────────────────────────────

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'django_filters',
    # Local apps
    'apps.accounts',
    'apps.cooperatives',
    'apps.producers',
    'apps.parcels',
    'apps.dashboard',
    'apps.registry',
    'apps.reports',
    'apps.monitoring',
]

MIDDLEWARE = [
    # Mesures des requêtes (Grafana) : en premier pour compter aussi les réponses des autres middlewares
    'apps.monitoring.middleware.PrometheusMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # Compresse les grosses réponses JSON (milliers de producteurs / polygones)
    'apps.monitoring.middleware.GZipExceptMetricsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # static files en production
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ─── Database ──────────────────────────────────────────────────────────────────
# Dev: SQLite | Prod: PostgreSQL + PostGIS (switch via .env)

DATABASE_URL = config('DATABASE_URL', default='')

if DATABASE_URL:
    # Production (Render/Railway/etc.) — parse the DATABASE_URL connection string
    import dj_database_url
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600, ssl_require=True)
    }
elif config('DB_ENGINE', default='') == 'django.contrib.gis.db.backends.postgis':
    DATABASES = {
        'default': {
            'ENGINE': 'django.contrib.gis.db.backends.postgis',
            'NAME': config('DB_NAME', default='geocollect'),
            'USER': config('DB_USER', default='postgres'),
            'PASSWORD': config('DB_PASSWORD', default='postgres'),
            'HOST': config('DB_HOST', default='localhost'),
            'PORT': config('DB_PORT', default='5432'),
        }
    }
else:
    if not DEBUG:
        # En production, SQLite serait effacé à chaque redéploiement : on refuse de démarrer.
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured(
            "DATABASE_URL manquante : configurez une base PostgreSQL (Render, Neon, Supabase...)."
        )
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# ─── Taille des envois ─────────────────────────────────────────────────────────
# Import de registres Excel et d'anciens polygones : corps JSON volumineux.
DATA_UPLOAD_MAX_MEMORY_SIZE = 30 * 1024 * 1024

# ─── Cache ──────────────────────────────────────────────────────────────────────
# « shared » : en base, partagé entre les processus gunicorn (limite des tentatives de connexion)
CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
    'shared': {'BACKEND': 'django.core.cache.backends.db.DatabaseCache', 'LOCATION': 'geocollect_cache'},
    # État des clients (actif, modules) : 60 s par processus, voir apps/accounts/tenancy.py
    'tenancy': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'tenancy'},
}

# ─── Custom User Model ──────────────────────────────────────────────────────────

AUTH_USER_MODEL = 'accounts.User'

# ─── REST Framework ────────────────────────────────────────────────────────────

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        # JWT + contrôle de l'organisation (suspendue / abonnement expiré → accès refusé)
        'apps.accounts.authentication.TenantJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_FILTER_BACKENDS': (
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ),
    'DEFAULT_PAGINATION_CLASS': 'utils.pagination.StandardPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
    ),
    # Limitation de débit : bloque la devinette de mots de passe et les abus
    'DEFAULT_THROTTLE_CLASSES': (
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.ScopedRateThrottle',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/min',
        'user': '600/min',        # imports et analyses par lots : ~40 appels en quelques minutes
        'login': '10/min',        # tentatives de connexion par adresse IP
        'password': '5/min',
        'message': '10/min',
    },
}

# ─── JWT ───────────────────────────────────────────────────────────────────────

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=2),   # renouvelé automatiquement par le navigateur
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# ─── Supervision (Grafana) ─────────────────────────────────────────────────
# Jeton exigé pour lire /api/v1/monitoring/metrics/ (vide = point désactivé)
MONITORING_TOKEN = config('MONITORING_TOKEN', default='')
# Indicateurs métier recalculés au plus toutes les N secondes (la base Neon gratuite peut dormir entre deux)
MONITORING_BUSINESS_TTL = config('MONITORING_BUSINESS_TTL', default=900, cast=int)

# ─── CORS ─────────────────────────────────────────────────────────────────────

CORS_ALLOWED_ORIGINS = config(
    'CORS_ALLOWED_ORIGINS',
    default='http://localhost:5173,https://localhost:5173,http://localhost:3000',
    cast=lambda v: [s.strip() for s in v.split(',')]
) + [
    'https://sunny-pegasus-8ea076.netlify.app',   # site de production Netlify (même si la variable manque)
    'https://geocollect-eudr.vercel.app',          # site de production Vercel
]
# En développement seulement : réseau local (téléphone). En production, seules les origines
# de CORS_ALLOWED_ORIGINS (le site Netlify) sont acceptées — plus de joker *.netlify.app.
CORS_ALLOWED_ORIGIN_REGEXES = [
    r'^https?://localhost:\d+$',
    r'^https?://127\.0\.0\.1:\d+$',
    r'^https?://192\.168\.\d+\.\d+:\d+$',
    r'^https?://10\.\d+\.\d+\.\d+:\d+$',
] if DEBUG else []
# Authentification par jeton (en-tête Authorization) : aucun cookie à partager entre sites
CORS_ALLOW_CREDENTIALS = False
# Le Polygon Validator envoie les gros fichiers compressés (Content-Encoding: gzip)
from corsheaders.defaults import default_headers  # noqa: E402
CORS_ALLOW_HEADERS = (*default_headers, 'content-encoding')

# ─── Celery ────────────────────────────────────────────────────────────────────

CELERY_BROKER_URL = config('REDIS_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = config('REDIS_URL', default='redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Africa/Abidjan'

# ─── Media & Static ────────────────────────────────────────────────────────────

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ─── Production security (actif quand DEBUG=False) ──────────────────────────────

# Render/Railway fournissent l'app derrière un proxy HTTPS
CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default='https://localhost:5173',
    cast=lambda v: [s.strip() for s in v.split(',') if s.strip()],
) + ['https://sunny-pegasus-8ea076.netlify.app']
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
if not DEBUG:
    SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=True, cast=bool)
    SECURE_REDIRECT_EXEMPT = [r'^$']          # contrôle de santé de Render
    SECURE_HSTS_SECONDS = 31536000            # HTTPS obligatoire pendant 1 an
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False    # sous-domaines onrender.com : hors de notre contrôle
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_SECURE = True
    CSRF_COOKIE_HTTPONLY = True

# ─── Email (confirmation d'inscription + accès) ─────────────────────────────────
# Si EMAIL_HOST_USER est défini → SMTP réel. Sinon → console (les emails s'affichent dans les logs).

EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')

if EMAIL_HOST_USER:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='GeoCollect EUDR <no-reply@geocollect.ci>')
# URL du frontend (pour le lien de connexion dans les emails)
FRONTEND_URL = config('FRONTEND_URL', default='https://sunny-pegasus-8ea076.netlify.app')

# ─── Passwords ────────────────────────────────────────────────────────────────

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
]

# ─── i18n ─────────────────────────────────────────────────────────────────────

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Africa/Abidjan'
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
