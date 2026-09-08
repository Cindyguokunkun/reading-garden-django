import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from reading.models import Book
class Command(BaseCommand):
    help='Import the migrated book and quiz library'
    def handle(self,*args,**options):
        data=json.loads((Path(settings.BASE_DIR)/'library-data.json').read_text(encoding='utf-8-sig'))
        for row in data['books']:
            Book.objects.update_or_create(source_id=row['id'],defaults={'title':row['title'],'series':row['series'],'level':row['level'],'kind':row['kind'],'words':row['words'],'source':row['source'],'quiz_data':data['quizzes'].get(row['title']) or []})
        self.stdout.write(self.style.SUCCESS(f"Imported {len(data['books'])} books"))
