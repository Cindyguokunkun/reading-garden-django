import re
from django.db import migrations


def classify_and_order(apps, schema_editor):
    Book = apps.get_model('reading', 'Book')
    for book in Book.objects.all().iterator():
        source = book.source_id or ''
        match = re.search(r'(\d+)$', source)
        if match:
            book.series_order = int(match.group(1))
        series = book.series or ''
        if '典范英语' in series:
            book.category = 'graded'
        elif any(name in series for name in ('Yasmin', 'Monkey Me', 'Frog and Toad', 'Pete the Cat')):
            book.category = 'bridge'
        elif any(name in series for name in ('Magic Tree House', 'My Weird School', 'Kung Pow Chicken', 'Dog Man')):
            book.category = 'early_chapter'
        elif 'Geronimo Stilton' in series:
            book.category = 'middle_chapter'
        book.save(update_fields=['series_order', 'category'])


class Migration(migrations.Migration):
    dependencies = [('reading', '0012_student_status_and_book_order')]
    operations = [migrations.RunPython(classify_and_order, migrations.RunPython.noop)]
