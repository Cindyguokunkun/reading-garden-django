import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from reading.models import Book
class Command(BaseCommand):
    help='Import the migrated book and quiz library'
    def handle(self,*args,**options):
        data=json.loads((Path(settings.BASE_DIR)/'library-data.json').read_text(encoding='utf-8-sig'))
        created_count = 0
        updated_count = 0
        unchanged_count = 0
        for row in data['books']:
            defaults = {
                'title': row['title'],
                'series': row['series'],
                'level': row['level'],
                'kind': row['kind'],
                'words': row['words'],
                'source': row['source'],
                'author': row.get('author', ''),
                'category': row.get('category', ''),
                'lexile': row.get('lexile', ''),
                'atos': row.get('atos'),
                'synopsis': row.get('synopsis', ''),
                'cover': row.get('cover', ''),
                'quiz_data': data['quizzes'].get(row['title']) or [],
            }
            if 'series_order' in row:
                defaults['series_order'] = row['series_order']
            existing = Book.objects.filter(source_id=row['id']).first()
            changed = existing is not None and any(getattr(existing, field) != value for field, value in defaults.items())
            _, created = Book.objects.update_or_create(source_id=row['id'], defaults=defaults)
            if created:
                created_count += 1
            elif changed:
                updated_count += 1
            else:
                unchanged_count += 1
        self.stdout.write(self.style.SUCCESS(
            f"Library total: {len(data['books'])}; created: {created_count}; "
            f"updated: {updated_count}; unchanged: {unchanged_count}"
        ))
