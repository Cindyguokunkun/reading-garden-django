import re
import unicodedata

from django.contrib.auth.models import User
from pypinyin import lazy_pinyin

from .models import Student


DEFAULT_PASSWORD = '000000'
COMPOUND_SURNAMES = {
    '欧阳', '司马', '上官', '诸葛', '东方', '皇甫', '尉迟', '公孙', '慕容', '长孙',
    '宇文', '司徒', '司空', '夏侯', '南宫', '令狐', '钟离', '轩辕', '百里', '呼延',
}


def full_english_name(chinese_name, english_given_name):
    chinese_name = (chinese_name or '').strip()
    given = ' '.join((english_given_name or '').strip().split())
    if not chinese_name or not given:
        return given
    if not '\u4e00' <= chinese_name[0] <= '\u9fff':
        return given
    surname = chinese_name[:2] if chinese_name[:2] in COMPOUND_SURNAMES else chinese_name[:1]
    romanized_surname = ''.join(lazy_pinyin(surname)).title()
    if given.lower().endswith(f' {romanized_surname.lower()}'):
        return given
    return f'{given} {romanized_surname}'


def username_base(english_name):
    value = unicodedata.normalize('NFKD', (english_name or '').strip())
    value = ''.join(character for character in value if not unicodedata.combining(character))
    parts = re.findall(r'[A-Za-z0-9]+', value)
    return ' '.join(part[:1].upper() + part[1:].lower() for part in parts)[:20] or 'Reader'


def unique_staff_username(english_name, exclude_user=None):
    base = username_base(english_name)
    candidate = base
    suffix = 2
    users = User.objects.all()
    if exclude_user:
        users = users.exclude(pk=exclude_user.pk)
    while users.filter(username__iexact=candidate).exists():
        candidate = f'{base}{suffix}'[:150]
        suffix += 1
    return candidate


def unique_student_login(english_name, exclude_student=None):
    base = username_base(english_name)
    candidate = base
    suffix = 2
    students = Student.objects.all()
    if exclude_student:
        students = students.exclude(pk=exclude_student.pk)
    while students.filter(login_id__iexact=candidate).exists():
        candidate = f'{base}{suffix}'[:24]
        suffix += 1
    return candidate
