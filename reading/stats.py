from datetime import date, timedelta
from django.conf import settings
from django.db.models import Count, Sum
from django.utils.translation import gettext_lazy as _
from .models import ReadingRecord

# 阅读等级称号：(累计词数阈值, 称号)，按阈值升序排列，达到即晋级。
# 称号只按累计词数实时算出，不存数据库；阈值与名字在此一处集中调整。
READING_LEVELS = [
    (10000, _('Bronze Reader')),
    (100000, _('Silver Reader')),
    (500000, _('Gold Reader')),
    (1000000, _('Platinum Reader')),
    (5000000, _('Diamond Reader')),
    (10000000, _('Legendary Reader')),
]

# 百万榜上榜门槛（累计词数）：达到此值即进入全校「百万星光榜」。
MILLION_CLUB_WORDS = 1000000

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
          .filter(student__active=True)
          .values('student_id', 'student__name', 'student__name_en', 'student__classroom__name')
          .annotate(words=Sum('words'), minutes=Sum('minutes'), books=Count('book', distinct=True)))
    rows = [{'student_id': r['student_id'], 'name': r['student__name'], 'name_en': r['student__name_en'],
             'classroom_name': r['student__classroom__name'], 'words': r['words'] or 0,
             'minutes': r['minutes'] or 0, 'books': r['books']} for r in qs]
    return rows

def sort_rows(rows, metric):
    return sorted(rows, key=lambda r: (-r[metric], r['name']))

def reading_level(words):
    """根据累计词数算出阅读等级徽章信息。

    等级只由累计词数实时推导，不落库，因此永远不会与阅读记录对不上。
    未达到首个阈值时 ``name`` 为空，仍会给出朝下一级的进度，便于激励。

    Args:
        words (int): 累计（通过测验的）阅读词数。

    Returns:
        dict: 含以下键——
            ``name`` 当前等级称号（未达标时为空字符串）、
            ``index`` 当前等级序号（未达标为 -1，用于徽章配色）、
            ``next_name`` 下一等级称号（已满级为空）、
            ``next_threshold`` 下一等级阈值、
            ``progress`` 朝下一级的进度百分比（0~100）、
            ``remaining`` 距下一级还差的词数、
            ``top`` 是否已达最高等级。
    """
    words = words or 0
    name, floor, index = '', 0, -1
    for position, (threshold, title) in enumerate(READING_LEVELS):
        if words >= threshold:
            name, floor, index = title, threshold, position
    if index >= len(READING_LEVELS) - 1:
        return {'name': name, 'index': index, 'next_name': '', 'next_threshold': 0,
                'progress': 100, 'remaining': 0, 'top': True, 'words': words}
    next_threshold, next_name = READING_LEVELS[index + 1]
    span = next_threshold - floor
    progress = min(100, round((words - floor) * 100 / span)) if span else 0
    return {'name': name, 'index': index, 'next_name': next_name,
            'next_threshold': next_threshold, 'progress': progress,
            'remaining': max(next_threshold - words, 0), 'top': False, 'words': words}
