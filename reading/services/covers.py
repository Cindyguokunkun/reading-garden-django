"""书籍封面检索服务。

本模块从 Open Library 与 Google Books 两个公开服务按书名检索候选封面，
供书籍编辑表单选择使用。检索受节流约束以尊重服务方频率限制，
并对最终选定的封面 URL 做白名单校验，防止写入任意外链。
"""

import json
import time
import urllib.parse
from django.conf import settings
from . import http

# Open Library 检索接口与封面图地址模板（%s 为封面 ID）。
OPENLIBRARY_URL = 'https://openlibrary.org/search.json'
OPENLIBRARY_COVER = 'https://covers.openlibrary.org/b/id/%s-M.jpg'

# Google Books 检索接口。
GOOGLE_URL = 'https://www.googleapis.com/books/v1/volumes'

# 单次检索返回的最大候选数。
MAX_CANDIDATES = 8

# 允许作为封面的主机白名单，用于校验用户选定/回填的封面 URL。
ALLOWED_HOSTS = ('covers.openlibrary.org', 'books.google.com', 'encrypted-tbn0.gstatic.com')


class CoverError(Exception):
    """封面检索或校验失败的异常。"""

    pass


# 上次调用外部封面服务的时间戳（time.monotonic），用于节流。
_last_call = 0.0


def _throttle():
    """按配置的最小间隔节流对外部封面服务的调用。

    若距上次调用不足 ``settings.COVERS_THROTTLE`` 秒，则阻塞等待补足间隔，
    随后更新上次调用时间戳。

    Returns:
        None: 仅产生副作用（可能阻塞当前线程），不返回值。
    """
    global _last_call
    gap = settings.COVERS_THROTTLE - (time.monotonic() - _last_call)
    if gap > 0: time.sleep(gap)
    _last_call = time.monotonic()


def _authors(names):
    """将作者名列表规整为以「 & 」连接的展示字符串。

    Args:
        names (Iterable | None): 原始作者名集合，可能含空白或 ``None``。

    Returns:
        str: 过滤空白项后拼接的作者字符串；无有效作者时为空串。
    """
    names = [str(name).strip() for name in (names or []) if str(name).strip()]
    return ' & '.join(names)


def parse_openlibrary(payload):
    """解析 Open Library 检索响应为统一的封面候选结构。

    仅保留同时具备书名与封面 ID（``cover_i``）的文档，并据封面 ID
    拼出中号封面图地址。

    Args:
        payload (dict): Open Library 检索接口返回的 JSON 数据。

    Returns:
        list[dict]: 候选列表，每项含 ``title``、``author``、``year``、
        ``provider``（固定为 ``'Open Library'``）与 ``cover``，
        最多 :data:`MAX_CANDIDATES` 条。
    """
    rows = []
    for doc in payload.get('docs') or []:
        title = str(doc.get('title') or '').strip()
        if not title or not doc.get('cover_i'): continue
        rows.append({'title': title, 'author': _authors(doc.get('author_name')), 'year': doc.get('first_publish_year'),
            'provider': 'Open Library', 'cover': OPENLIBRARY_COVER % doc['cover_i']})
    return rows[:MAX_CANDIDATES]


def parse_google(payload):
    """解析 Google Books 检索响应为统一的封面候选结构。

    仅保留同时具备书名与缩略图封面的条目；缩略图若为 http 会被升级为
    https，出版年从 ``publishedDate`` 前四位解析。

    Args:
        payload (dict): Google Books 检索接口返回的 JSON 数据。

    Returns:
        list[dict]: 候选列表，每项含 ``title``、``author``、``year``、
        ``provider``（固定为 ``'Google Books'``）与 ``cover``，
        最多 :data:`MAX_CANDIDATES` 条。
    """
    rows = []
    for item in payload.get('items') or []:
        info = item.get('volumeInfo') or {}
        title = str(info.get('title') or '').strip()
        cover = str((info.get('imageLinks') or {}).get('thumbnail') or '')
        if not title or not cover: continue
        if cover.startswith('http://'): cover = 'https://' + cover[len('http://'):]
        published = str(info.get('publishedDate') or '')
        rows.append({'title': title, 'author': _authors(info.get('authors')), 'year': int(published[:4]) if published[:4].isdigit() else None,
            'provider': 'Google Books', 'cover': cover})
    return rows[:MAX_CANDIDATES]


def _openlibrary(opener, title):
    """向 Open Library 检索指定书名的封面候选。

    Args:
        opener (urllib.request.OpenerDirector): 用于发请求的 opener。
        title (str): 检索书名。

    Returns:
        list[dict]: 解析后的封面候选列表，结构见 :func:`parse_openlibrary`。

    Raises:
        http.HttpError: 请求失败或超时。
        ValueError: 响应不是合法 JSON。
    """
    query = urllib.parse.urlencode({'q': title, 'fields': 'title,author_name,first_publish_year,cover_i', 'limit': MAX_CANDIDATES})
    _throttle()
    status, markup, url = http.fetch(opener, f'{OPENLIBRARY_URL}?{query}', timeout=settings.COVERS_TIMEOUT, retries=1)
    return parse_openlibrary(json.loads(markup))


def _google(opener, title):
    """向 Google Books 检索指定书名的封面候选。

    Args:
        opener (urllib.request.OpenerDirector): 用于发请求的 opener。
        title (str): 检索书名，以 ``intitle:`` 限定匹配标题。

    Returns:
        list[dict]: 解析后的封面候选列表，结构见 :func:`parse_google`。

    Raises:
        http.HttpError: 请求失败或超时。
        ValueError: 响应不是合法 JSON。
    """
    query = urllib.parse.urlencode({'q': f'intitle:{title}', 'maxResults': MAX_CANDIDATES})
    _throttle()
    status, markup, url = http.fetch(opener, f'{GOOGLE_URL}?{query}', timeout=settings.COVERS_TIMEOUT, retries=1)
    return parse_google(json.loads(markup))


def search_covers(title):
    """按书名检索封面，依次尝试两个服务，返回首个有结果者的候选。

    单个服务的失败（网络、解析等）被容错为空结果并继续尝试下一个；
    两个服务都无结果时抛出异常。

    Args:
        title (str): 检索书名，空白将被拒绝。

    Returns:
        list[dict]: 封面候选列表。

    Raises:
        CoverError: 书名为空，或两个服务均未返回任何封面。
    """
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
    """校验封面 URL 是否来自受信任的服务，防止写入任意外链。

    由于选定的封面 URL 来自表单隐藏字段（客户端可篡改），此处强制要求
    其为 https 且主机在白名单内。

    Args:
        url (str | None): 待校验的封面地址。

    Returns:
        str: 去空白后、通过校验的封面 URL。

    Raises:
        CoverError: URL 非 https，或主机不在 :data:`ALLOWED_HOSTS` 白名单内。
    """
    # The chosen url comes back from a hidden form field, so pin it to the two services.
    url = (url or '').strip()
    if not url.startswith('https://') or (urllib.parse.urlparse(url).hostname or '') not in ALLOWED_HOSTS:
        raise CoverError('refusing a cover outside the known services')
    return url
