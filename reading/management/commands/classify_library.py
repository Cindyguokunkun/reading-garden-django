"""Apply the shared difficulty bands to the database and seed data."""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from reading.difficulty import difficulty_category
from reading.models import Book


class Command(BaseCommand):
    help = 'Classify books and their quizzes with the same difficulty rules.'

    def handle(self, *args, **options):
        changed = 0
        for book in Book.objects.all():
            category = difficulty_category(series=book.series, atos=book.atos, current=book.category)
            if category and category != book.category:
                book.category = category
                book.save(update_fields=['category'])
                changed += 1

        path = Path(settings.BASE_DIR) / 'library-data.json'
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        json_changed = 0
        for row in data.get('books', []):
            category = difficulty_category(
                series=row.get('series', ''), atos=row.get('atos'), current=row.get('category', ''))
            if category and category != row.get('category'):
                row['category'] = category
                json_changed += 1
        if json_changed:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(
            f'Classified {changed} database books and {json_changed} seed-data books.'))
