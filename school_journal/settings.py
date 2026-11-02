"""Настройки проекта «Электронный журнал».

Безопасность: по умолчанию проект стартует в «безопасном» режиме (DEBUG=0),
а для удобной локальной работы включите DEBUG=1 в .env. Всё, что касается HTTPS
(редирект, защищённые cookie, HSTS), включается переменными окружения — см. README.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "да"}


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --- Основное -----------------------------------------------------------------
DEBUG = env_bool("DEBUG", False)

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-dev-key-only-for-local-debug"
    else:
        raise RuntimeError(
            "Не задан SECRET_KEY. Укажите длинный случайный ключ в переменной "
            "окружения SECRET_KEY (минимум 50 символов)."
        )
if not DEBUG:
    # В боевом режиме ключ должен быть длинным и случайным: им подписываются
    # сессии, токены CSRF и шифруется пароль SMTP.
    if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("django-insecure-"):
        raise RuntimeError(
            "SECRET_KEY слишком короткий или сгенерирован Django для отладки. "
            "Сгенерируйте случайный ключ не короче 50 символов, например: "
            "python -c \"import secrets;print(secrets.token_urlsafe(64))\""
        )
    if len(set(SECRET_KEY)) < 5:
        raise RuntimeError("SECRET_KEY состоит из слишком однообразных символов.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "*" if DEBUG else "")
if not DEBUG and not ALLOWED_HOSTS:
    raise RuntimeError(
        "Не задан ALLOWED_HOSTS. Перечислите домены через запятую, например: "
        "journal.example.ru,www.journal.example.ru"
    )

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# Если список не задан в .env, доверяем своим же доменам из ALLOWED_HOSTS.
# Это нужно для HTTPS: Django сверяет источник запроса с этими адресами и без них
# отклоняет вход и другие отправки форм с ошибкой 403.
_own_origins = [
    f"{scheme}://{host}"
    for host in ALLOWED_HOSTS
    if host and host != "*"
    for scheme in ("https", "http")
]
# Свои домены доверяем всегда, даже если в .env перечислено что-то ещё:
# иначе после смены домена вход и сохранение отваливались бы с ошибкой 403.
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(list(CSRF_TRUSTED_ORIGINS) + _own_origins))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "journal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # Выбор языка интерфейса. Стоит сразу после сессий (язык берётся из сессии
    # и из cookie LANGUAGE_COOKIE_NAME) и до CommonMiddleware — так требует
    # документация Django, иначе переключение языка и редиректы работают неверно.
    "django.middleware.locale.LocaleMiddleware",
    # Переключает сессию демо-режима на отдельную демонстрационную базу.
    # Стоит до аутентификации: пользователь демо-режима ищется уже в демо-базе.
    "journal.middleware.DemoDatabaseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # Первый запуск: пока в базе нет главного администратора, все страницы
    # ведут на /setup/. Стоит после сессий (виден флаг демо-режима) и после
    # аутентификации; настроенному журналу не мешает.
    "journal.middleware.FirstRunSetupMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "journal.middleware.AccessControlMiddleware",
    # В режиме отладки подменяет страницу 404 Django на нашу (в бою это делает handler404).
    "journal.middleware.NiceErrorPagesMiddleware",
]

# Демо-данные живут в отдельной базе и в рабочую не попадают.
DATABASE_ROUTERS = ["journal.demo_router.DemoRouter"]

ROOT_URLCONF = "school_journal.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "journal.context_processors.product",
                "journal.context_processors.theme",
                "journal.context_processors.demo_mode",
                "journal.context_processors.periods",
                "journal.context_processors.branding",
            ],
        },
    },
]

WSGI_APPLICATION = "school_journal.wsgi.application"

# --- База данных -------------------------------------------------------------
# По умолчанию — PostgreSQL (как в docker-compose).
# Для локального запуска без Docker: DB_ENGINE=sqlite
# Демонстрационные данные хранятся отдельно (алиас "demo"): см. journal/demo_router.py.
DEMO_DB_NAME = os.environ.get("DEMO_DB_NAME", "school_journal_demo")
DEMO_SQLITE_NAME = os.environ.get("DEMO_SQLITE_NAME", "db_demo.sqlite3")
MAIN_SQLITE_NAME = os.environ.get("DB_SQLITE_NAME", "db.sqlite3")

if os.environ.get("DB_ENGINE", "postgres") == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / MAIN_SQLITE_NAME,
        },
        "demo": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / DEMO_SQLITE_NAME,
        },
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "school_journal"),
            "USER": os.environ.get("POSTGRES_USER", "school_journal"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "school_journal"),
            "HOST": os.environ.get("POSTGRES_HOST", "db"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
            # Проверяем «живость» постоянного соединения: иначе после паузы
            # PostgreSQL может закрыть его, и страница отдаёт 500.
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {"connect_timeout": 10},
        },
        "demo": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": DEMO_DB_NAME,
            "USER": os.environ.get("POSTGRES_USER", "school_journal"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "school_journal"),
            "HOST": os.environ.get("POSTGRES_HOST", "db"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {"connect_timeout": 10},
        },
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTH_USER_MODEL = "journal.User"

# --- Язык интерфейса ----------------------------------------------------------
# Русский — язык по умолчанию: все тексты в шаблонах написаны по-русски,
# поэтому для ru перевода не требуется, а для остальных языков он лежит в locale/.
LANGUAGE_CODE = os.environ.get("DJANGO_LANGUAGE_CODE", "ru")
LANGUAGES = [
    ("ru", "Русский"),
    ("en", "English"),
]
# Каталоги переводов проекта: locale/<код языка>/LC_MESSAGES/django.po|.mo
# После правки .po их нужно собрать: python manage.py compilemessages
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True
# Переключатель языка (POST /i18n/setlang/) хранит выбор в cookie и в сессии.
# Язык не сбрасывается при закрытии браузера: cookie живёт год.
LANGUAGE_COOKIE_NAME = "django_language"
LANGUAGE_COOKIE_AGE = 365 * 24 * 60 * 60
LANGUAGE_COOKIE_SAMESITE = "Lax"

TIME_ZONE = os.environ.get("TIME_ZONE", "Europe/Moscow")
# По умолчанию даты хранятся с часовым поясом (так рекомендует Django);
# USE_TZ=0 в .env — только для переноса старых данных с наивными датами.
USE_TZ = env_bool("USE_TZ", True)

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Сжатие без manifest: статика работает даже до collectstatic.
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"
    },
}

# Оформление: Tailwind лежит в контейнере (journal/static/journal/vendor/tailwind.js),
# поэтому интернет для интерфейса не нужен. TAILWIND_CDN=1 — снова брать с CDN.
TAILWIND_CDN = env_bool("TAILWIND_CDN", False)
TAILWIND_LOCAL = "journal/vendor/tailwind.js"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "login"

# --- Демо-режим ---------------------------------------------------------------
DEMO_MODE = env_bool("DEMO_MODE", DEBUG)

# Название организации: показывается на странице входа и в подвале.
# Значение по умолчанию нейтральное: журнал ставят разные организации.
FUND_NAME = os.environ.get("FUND_NAME", "Образовательная организация")

# --- Безопасность -------------------------------------------------------------
# Работа за обратным прокси (nginx, traefik): доверяем заголовкам X-Forwarded-*.
TRUST_PROXY_HEADERS = env_bool("TRUST_PROXY_HEADERS", False)
if TRUST_PROXY_HEADERS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# HTTPS: включается, когда сайт опубликован по https (иначе локальный http сломается).
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000 if SECURE_SSL_REDIRECT else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0

SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", SECURE_SSL_REDIRECT)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", SECURE_SSL_REDIRECT)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 12 * 3600)
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_SAVE_EVERY_REQUEST = True
# Галочка «Сохранить вход»: сессия живёт столько дней без активности (не больше 90).
REMEMBER_ME_DAYS = min(max(env_int("REMEMBER_ME_DAYS", 30), 1), 90)

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Ограничения на размер запросов и загружаемых файлов (резервная копия).
DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("DATA_UPLOAD_MAX_MEMORY_SIZE", 8 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = env_int("DATA_UPLOAD_MAX_NUMBER_FIELDS", 5000)

# Адрес админки Django (можно спрятать за нестандартный путь).
ADMIN_URL = os.environ.get("ADMIN_URL", "admin/")

# Красивые страницы ошибок (404, 403, 400, 500) с блоком для разработчика.
# NICE_ERRORS_IN_DEBUG=0 возвращает встроенную отладочную страницу Django 404.
NICE_ERRORS_IN_DEBUG = env_bool("NICE_ERRORS_IN_DEBUG", True)

# Неудачная проверка CSRF тоже показывается понятной страницей.
CSRF_FAILURE_VIEW = "journal.error_views.csrf_failure"

# Защита от сканеров уязвимостей: адреса вида /.env, /wp-admin, /graphql
# попадают в журнал как «подозрительный запрос», а после нескольких таких
# запросов адрес блокируется автоматически на AUTO_BLOCK_HOURS часов.
AUTO_BLOCK_PROBES = env_bool("AUTO_BLOCK_PROBES", True)
AUTO_BLOCK_THRESHOLD = int(os.environ.get("AUTO_BLOCK_THRESHOLD", 5))
AUTO_BLOCK_WINDOW_MINUTES = int(os.environ.get("AUTO_BLOCK_WINDOW_MINUTES", 15))
AUTO_BLOCK_HOURS = int(os.environ.get("AUTO_BLOCK_HOURS", 24))
# Свои локальные и частные адреса не блокируем, чтобы не запереть себя.
AUTO_BLOCK_PRIVATE = env_bool("AUTO_BLOCK_PRIVATE", False)

# Демо-режим: у каждого посетителя своя копия демонстрационной базы (SQLite).
# Файлы-копии живут до закрытия браузера и удаляются уборкой по возрасту.
if not ADMIN_URL.endswith("/"):
    ADMIN_URL += "/"

DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "journal@localhost")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Письма по умолчанию пишем в консоль: настоящие настройки SMTP задаёт админ
# в разделе «Безопасность», они хранятся в базе.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
# Каким транспортом отправлять уведомления из раздела «Безопасность».
NOTIFY_EMAIL_BACKEND = os.environ.get(
    "NOTIFY_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
