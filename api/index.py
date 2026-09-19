import os
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ.setdefault('SQLITE_PATH', '/tmp/reading-garden-preview.sqlite3')
os.environ.setdefault('DJANGO_DEBUG', '0')
os.environ.setdefault('DJANGO_ALLOWED_HOSTS', '*')
os.environ.setdefault('DJANGO_SECRET_KEY', 'preview-only-reading-garden-key')

from django.core.management import call_command
from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
database_path = Path(os.environ['SQLITE_PATH'])
if not database_path.exists():
    call_command('migrate', interactive=False, verbosity=0)
    call_command('seed_library', verbosity=0)

    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user_model.objects.create_superuser(
        username='preview',
        email='',
        password='reading-preview-2026',
    )

app = application
