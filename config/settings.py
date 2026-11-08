"""Settings - Student Records Portal (generic edition).

Runs on one PC / LAN. The secret key is generated on first run and kept in
.secret_key (never share or publish that file).
"""
import os
from pathlib import Path

from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent


def _secret_key():
    env = os.environ.get("DJANGO_SECRET_KEY")
    if env:
        return env
    key_file = BASE_DIR / ".secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = get_random_secret_key()
    key_file.write_text(key)
    return key


SECRET_KEY = _secret_key()
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "portal.middleware.SetupRequiredMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "portal.context_processors.branding",
    ]},
}]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
WHITENOISE_USE_FINDERS = True

# Uploaded question papers (PDF). Never served directly: only through a login-checked view.
MEDIA_ROOT = BASE_DIR / "media"
MAX_PAPER_UPLOAD_MB = 10
DATA_UPLOAD_MAX_NUMBER_FIELDS = 6000  # big marks / enrolment grids
MAX_LOGO_UPLOAD_MB = 2
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "index"
SESSION_COOKIE_AGE = 60 * 60 * 10

# ---- Portal -------------------------------------------------------------
# Institution name, classes, exams and pass marks are NOT set here: the first-run setup wizard
# (open the site once after installing) stores them in the database.
SOFTWARE_CREDIT = "Banajit Rajbongshi"
SHOW_SOFTWARE_CREDIT = True            # set False to hide the small "portal by ..." line in the footer
# The setup wizard only opens from the computer running the portal (safer). Set
# PORTAL_SETUP_ANYWHERE=1 to allow it from other computers (e.g. on a hosting service).
SETUP_ANYWHERE = os.environ.get("PORTAL_SETUP_ANYWHERE", "0") == "1"
