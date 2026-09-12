import html as htmllib
import re
import time
import urllib.parse
from decimal import Decimal, InvalidOperation
from django.conf import settings
from . import http

BASE = 'https://www.arbookfind.com/'
USER_TYPE_URL = BASE + 'UserType.aspx'
ADVANCED_URL = BASE + 'advanced.aspx'
TITLE_FIELD = 'ctl00$ContentPlaceHolder1$txtTitle'
SUBMIT_FIELD = 'ctl00$ContentPlaceHolder1$btnDoIt'
MAX_CANDIDATES = 8
DETAIL_SPANS = {
    'title': 'lblBookTitle', 'author': 'lblAuthor', 'quiz_no': 'lblQuizNumber', 'synopsis': 'lblBookSummary',
    'atos': 'lblBookLevel', 'interest_level': 'lblInterestLevel', 'points': 'lblPoints', 'words': 'lblWordCount',
    'fiction': 'lblFictionNonFiction', 'series': 'lblSeriesLabel', 'topics': 'lblTopicLabel',
}

class ArfError(Exception):
    pass

_last_call = 0.0

def _throttle():
    global _last_call
    gap = settings.ARF_THROTTLE - (time.monotonic() - _last_call)
    if gap > 0: time.sleep(gap)
    _last_call = time.monotonic()

def _text(markup):
    markup = re.sub(r'(?s)<(script|style)[^>]*>.*?</\1>', ' ', markup)
    return re.sub(r'\s+', ' ', htmllib.unescape(re.sub(r'(?s)<[^>]+>', ' ', markup))).strip()

def _span(markup, span_id):
    found = re.search(r'id="[^"]*%s"[^>]*>(.*?)</span>' % span_id, markup, re.S)
    return _text(found.group(1)) if found else ''

def _hidden(markup):
    fields = {}
    for tag in re.findall(r'<input[^>]*>', markup):
        if 'type="hidden"' not in tag: continue
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if name: fields[htmllib.unescape(name.group(1))] = htmllib.unescape(value.group(1)) if value else ''
    return fields

def _select_defaults(markup):
    fields = {}
    for name, body in re.findall(r'(?s)<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', markup):
        option = re.search(r'<option[^>]*value="([^"]*)"', body)
        fields[htmllib.unescape(name)] = htmllib.unescape(option.group(1)) if option else ''
    return fields

def _decimal(raw):
    try: return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError): return None

def atos_category(atos):
    if atos is None: return ''
    for ceiling, category in settings.ATOS_BANDS:
        if atos < ceiling: return category
    return 'upper_chapter'

def parse_result_rows(markup):
    rows = []
    for chunk in re.split(r'(?=<a[^>]*id="book-title")', markup)[1:]:
        anchor = re.match(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S)
        if not anchor: continue
        title = _text(anchor.group(2))
        body = _text(chunk[:chunk.find('</p>')] if '</p>' in chunk else chunk)
        author = re.match(r'%s\s+(.*?)\s*AR Quiz No\.' % re.escape(title), body)
        quiz_no = re.search(r'AR Quiz No\.\s*(\d+)\s*([A-Z]{2})?', body)
        interest = re.search(r'IL:\s*([A-Za-z0-9+\-]+)', body)
        level = re.search(r'BL:\s*([\d.]+)', body)
        points = re.search(r'AR Pts:\s*([\d.]+)', body)
        # bookdetail.aspx links carry a search-session id; the print view is session-free.
        if quiz_no:
            url = BASE + 'bookdetailprint.aspx?l=%s&q=%s' % (quiz_no.group(2) or 'EN', quiz_no.group(1))
        else:
            url = urllib.parse.urljoin(BASE, htmllib.unescape(anchor.group(1)))
        rows.append({
            'title': title, 'url': url,
            'author': author.group(1).strip() or None if author else None,
            'quiz_no': quiz_no.group(1) if quiz_no else None,
            'interest_level': interest.group(1) if interest else None,
            'atos': _decimal(level.group(1)) if level else None,
            'points': _decimal(points.group(1)) if points else None,
            'fiction': 'Nonfiction' if re.search(r'\bNonfiction\b', body) else ('Fiction' if re.search(r'\bFiction\b', body) else None),
        })
    return rows[:MAX_CANDIDATES]

def parse_detail(markup):
    raw = {key: _span(markup, span_id) for key, span_id in DETAIL_SPANS.items()}
    digits = re.sub(r'\D', '', raw['words'])
    series = raw['series'].split(';')[0].strip()
    return {
        'title': raw['title'] or None, 'author': raw['author'] or None, 'quiz_no': raw['quiz_no'] or None,
        'synopsis': raw['synopsis'] or None, 'atos': _decimal(raw['atos']), 'points': _decimal(raw['points']),
        'words': int(digits) if digits else None, 'interest_level': raw['interest_level'] or None,
        'fiction': raw['fiction'] or None, 'series': series or None, 'topics': raw['topics'] or None,
    }

def search_candidates(title):
    title = (title or '').strip()
    if not title: raise ArfError('no title')
    opener = http.build_opener(settings.ARF_PROXY)
    _throttle()
    status, markup, url = http.fetch(opener, USER_TYPE_URL, timeout=settings.ARF_TIMEOUT, retries=1)
    fields = _hidden(markup)
    if '__VIEWSTATE' not in fields: raise ArfError('unexpected entry page')
    fields.update({'radUserType': 'radTeacher', 'btnSubmitUserType': 'Submit'})
    _throttle()
    http.fetch(opener, USER_TYPE_URL, data=fields, timeout=settings.ARF_TIMEOUT)
    _throttle()
    status, markup, url = http.fetch(opener, ADVANCED_URL, timeout=settings.ARF_TIMEOUT, retries=1)
    fields = _hidden(markup)
    fields.update(_select_defaults(markup))
    if '__VIEWSTATE' not in fields or TITLE_FIELD not in markup: raise ArfError('unexpected search page')
    fields[TITLE_FIELD] = title
    fields[SUBMIT_FIELD] = 'Do It'
    _throttle()
    status, markup, url = http.fetch(opener, ADVANCED_URL, data=fields, timeout=settings.ARF_TIMEOUT)
    if 'bookfindererror' in url: raise ArfError('search rejected')
    return parse_result_rows(markup)

def fetch_detail(detail_url):
    if not (detail_url or '').startswith(BASE): raise ArfError('refusing a url outside AR BookFinder')
    opener = http.build_opener(settings.ARF_PROXY)
    _throttle()
    status, markup, url = http.fetch(opener, detail_url, timeout=settings.ARF_TIMEOUT, retries=1)
    if 'bookfindererror' in url: raise ArfError('detail unavailable')
    detail = parse_detail(markup)
    if detail['atos'] is None and detail['words'] is None: raise ArfError('unparsable detail page')
    detail['url'] = detail_url
    return detail
