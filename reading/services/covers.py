import json
import time
import urllib.parse
from django.conf import settings
from . import http

OPENLIBRARY_URL = 'https://openlibrary.org/search.json'
OPENLIBRARY_COVER = 'https://covers.openlibrary.org/b/id/%s-M.jpg'
GOOGLE_URL = 'https://www.googleapis.com/books/v1/volumes'
MAX_CANDIDATES = 8
ALLOWED_HOSTS = ('covers.openlibrary.org', 'books.google.com', 'encrypted-tbn0.gstatic.com')

class CoverError(Exception):
    pass

_last_call = 0.0

def _throttle():
    global _last_call
    gap = settings.COVERS_THROTTLE - (time.monotonic() - _last_call)
    if gap > 0: time.sleep(gap)
    _last_call = time.monotonic()

def _first(names):
    names = [str(name).strip() for name in (names or []) if str(name).strip()]
    return names[0] if names else ''

def parse_openlibrary(payload):
    rows = []
    for doc in payload.get('docs') or []:
        title = str(doc.get('title') or '').strip()
        if not title or not doc.get('cover_i'): continue
        rows.append({'title': title, 'author': _first(doc.get('author_name')), 'year': doc.get('first_publish_year'),
            'provider': 'Open Library', 'cover': OPENLIBRARY_COVER % doc['cover_i']})
    return rows[:MAX_CANDIDATES]

def parse_google(payload):
    rows = []
    for item in payload.get('items') or []:
        info = item.get('volumeInfo') or {}
        title = str(info.get('title') or '').strip()
        cover = str((info.get('imageLinks') or {}).get('thumbnail') or '')
        if not title or not cover: continue
        if cover.startswith('http://'): cover = 'https://' + cover[len('http://'):]
        published = str(info.get('publishedDate') or '')
        rows.append({'title': title, 'author': _first(info.get('authors')), 'year': int(published[:4]) if published[:4].isdigit() else None,
            'provider': 'Google Books', 'cover': cover})
    return rows[:MAX_CANDIDATES]

def _openlibrary(opener, title):
    query = urllib.parse.urlencode({'q': title, 'fields': 'title,author_name,first_publish_year,cover_i', 'limit': MAX_CANDIDATES})
    _throttle()
    status, markup, url = http.fetch(opener, f'{OPENLIBRARY_URL}?{query}', timeout=settings.COVERS_TIMEOUT, retries=1)
    return parse_openlibrary(json.loads(markup))

def _google(opener, title):
    query = urllib.parse.urlencode({'q': f'intitle:{title}', 'maxResults': MAX_CANDIDATES})
    _throttle()
    status, markup, url = http.fetch(opener, f'{GOOGLE_URL}?{query}', timeout=settings.COVERS_TIMEOUT, retries=1)
    return parse_google(json.loads(markup))

def search_covers(title):
    title = (title or '').strip()
    if not title: raise CoverError('no title')
    opener = http.build_opener(settings.COVERS_PROXY, cookies=False)
    rows = []
    for provider in (_openlibrary, _google):
        try:
            rows = provider(opener, title)
        except (http.HttpError, CoverError, ValueError):
            rows = []
        if rows: break
    if not rows: raise CoverError('no cover found')
    return rows

def assert_cover_url(url):
    # The chosen url comes back from a hidden form field, so pin it to the two services.
    url = (url or '').strip()
    if not url.startswith('https://') or (urllib.parse.urlparse(url).hostname or '') not in ALLOWED_HOSTS:
        raise CoverError('refusing a cover outside the known services')
    return url
