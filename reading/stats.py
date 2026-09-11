from datetime import date, timedelta
from django.conf import settings
from django.db.models import Count, Sum
from .models import ReadingRecord

def period(mode, anchor=None):
    anchor = anchor or date.today()
    if mode == 'month':
        start = anchor.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    elif mode == 'term':
        fm, fd = settings.TERM_BOUNDARIES['fall']
        sm, sd = settings.TERM_BOUNDARIES['spring']
        fall_start = anchor.replace(month=fm, day=fd)
        spring_start = anchor.replace(month=sm, day=sd)
        if anchor >= fall_start:
            start = fall_start
            end = anchor.replace(year=anchor.year + 1, month=sm, day=sd) - timedelta(days=1)
        elif anchor >= spring_start:
            start, end = spring_start, fall_start - timedelta(days=1)
        else:
            start = anchor.replace(year=anchor.year - 1, month=fm, day=fd)
            end = spring_start - timedelta(days=1)
    elif mode == 'all':
        start, end = date(2000, 1, 1), date(2100, 1, 1)
    else:
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=6)
    return start, end

def rank_rows(classrooms, start, end):
    qs = (ReadingRecord.objects
          .filter(passed=True, read_date__range=(start, end), student__classroom__in=classrooms)
          .values('student_id', 'student__name', 'student__name_en', 'student__classroom__name')
          .annotate(words=Sum('words'), minutes=Sum('minutes'), books=Count('book', distinct=True)))
    rows = [{'student_id': r['student_id'], 'name': r['student__name'], 'name_en': r['student__name_en'],
             'classroom_name': r['student__classroom__name'], 'words': r['words'] or 0,
             'minutes': r['minutes'] or 0, 'books': r['books']} for r in qs]
    return rows

def sort_rows(rows, metric):
    return sorted(rows, key=lambda r: (-r[metric], r['name']))
