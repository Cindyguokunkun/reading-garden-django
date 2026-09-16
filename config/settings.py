import os
from pathlib import Path
from .envfile import load_env
BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = 'local-reading-garden-change-me'
DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '0.0.0.0', '*']
INSTALLED_APPS = ['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','reading']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.locale.LocaleMiddleware','reading.middleware.StudentEnglishMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.template.context_processors.i18n','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','reading.personas.persona_processor']}}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': {'ENGINE':'django.db.backends.sqlite3','NAME':BASE_DIR/'db.sqlite3'}}
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = 'zh-hans'
LANGUAGES = [('zh-hans','简体中文'),('en','English')]
LOCALE_PATHS = [BASE_DIR/'locale']
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True
TERM_BOUNDARIES = {'fall': (9, 1), 'spring': (2, 1)}
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR/'static']
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'
load_env(BASE_DIR/'.env')
ARF_PROXY = os.environ.get('ARF_PROXY', 'http://127.0.0.1:7897')
ARF_TIMEOUT = int(os.environ.get('ARF_TIMEOUT', '15'))
ARF_THROTTLE = float(os.environ.get('ARF_THROTTLE', '1.0'))
COVERS_PROXY = os.environ.get('COVERS_PROXY', 'http://127.0.0.1:7897')
COVERS_TIMEOUT = int(os.environ.get('COVERS_TIMEOUT', '10'))
COVERS_THROTTLE = float(os.environ.get('COVERS_THROTTLE', '1.0'))
QUIZGEN_BASE_URL = os.environ.get('QUIZGEN_BASE_URL', '')
QUIZGEN_API_KEY = os.environ.get('QUIZGEN_API_KEY', '')
QUIZGEN_MODEL = os.environ.get('QUIZGEN_MODEL', '')
QUIZGEN_PROXY = os.environ.get('QUIZGEN_PROXY', '')
QUIZGEN_TIMEOUT = int(os.environ.get('QUIZGEN_TIMEOUT', '120'))
QUIZGEN_QUESTIONS = int(os.environ.get('QUIZGEN_QUESTIONS', '10'))
QUIZGEN_MIN_QUESTIONS = int(os.environ.get('QUIZGEN_MIN_QUESTIONS', '5'))
QUIZGEN_MATERIAL_CHARS = int(os.environ.get('QUIZGEN_MATERIAL_CHARS', '8000'))
QUIZGEN_JSON_MODE = os.environ.get('QUIZGEN_JSON_MODE', '1') == '1'
QUIZGEN_ENABLED = bool(QUIZGEN_BASE_URL and QUIZGEN_API_KEY and QUIZGEN_MODEL)
ATOS_BANDS = [(1.5, 'graded'), (2.5, 'bridge'), (3.5, 'early_chapter'), (5.0, 'middle_chapter')]
