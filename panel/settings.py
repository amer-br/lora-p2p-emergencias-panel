"""Configuración del panel de administración del campus.

Los valores sensibles vienen de variables de entorno (ver .env.example). Sin
POSTGRES_HOST se usa SQLite, útil para correr las pruebas fuera de Docker.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _lista(nombre, defecto=''):
    return [v.strip() for v in os.environ.get(nombre, defecto).split(',') if v.strip()]


DEBUG = os.environ.get('DJANGO_DEBUG', '1') == '1'
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'solo-para-desarrollo-local' if DEBUG else '')
if not SECRET_KEY:
    raise RuntimeError('Define DJANGO_SECRET_KEY cuando DJANGO_DEBUG=0')
ALLOWED_HOSTS = _lista('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1')
CSRF_TRUSTED_ORIGINS = _lista('DJANGO_CSRF_TRUSTED_ORIGINS')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'emergencias',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'panel.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'panel.wsgi.application'

if os.environ.get('POSTGRES_HOST'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'HOST': os.environ['POSTGRES_HOST'],
            'NAME': os.environ['POSTGRES_DB'],
            'USER': os.environ['POSTGRES_USER'],
            'PASSWORD': os.environ['POSTGRES_PASSWORD'],
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
]

LANGUAGE_CODE = 'es-mx'
TIME_ZONE = 'America/Mexico_City'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    # En producción (Docker) los estáticos se comprimen y versionan con collectstatic.
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage' if DEBUG
                    else 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'tablero'
LOGOUT_REDIRECT_URL = 'login'

# --- Red LoRa ---
RADIO_URL = os.environ.get('RADIO_URL', 'ws://localhost:8080/ws')
PANEL_SENDER_ID = int(os.environ.get('PANEL_SENDER_ID', '0x0000CA0A'), 0)

# Centro y límites del campus (mismos valores que lib/app_scope.dart).
LIMITES_CAMPUS = {'norte': 22.77481, 'sur': 22.76790, 'oeste': -102.64876, 'este': -102.63754}

# Teselas del mapa: se descargan de OSM una vez y se sirven desde este directorio.
TESELAS_DIR = os.environ.get('TESELAS_DIR', str(BASE_DIR / 'teselas'))
TESELAS_ORIGEN = os.environ.get('TESELAS_ORIGEN', 'https://tile.openstreetmap.org/{z}/{x}/{y}.png')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'consola': {'class': 'logging.StreamHandler'}},
    'loggers': {'emergencias': {'handlers': ['consola'], 'level': 'INFO'}},
}
