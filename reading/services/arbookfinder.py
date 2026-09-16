"""AR BookFinder 书籍检索与详情抓取服务。

本模块模拟对 arbookfind.com 的多步表单交互，按书名检索候选版本，
并抓取单本书的详情页，解析出作者、ATOS 难度、字数、系列、兴趣等级等
元数据，供书籍编辑表单回填。站点为 ASP.NET 表单，需携带 ``__VIEWSTATE``
等隐藏字段并维持会话 Cookie；所有请求受节流约束。
"""

import html as htmllib
import re
import time
import urllib.parse
from decimal import Decimal, InvalidOperation
from django.conf import settings
from . import http

# AR BookFinder 站点基址与关键入口 URL。
BASE = 'https://www.arbookfind.com/'
USER_TYPE_URL = BASE + 'UserType.aspx'
ADVANCED_URL = BASE + 'advanced.aspx'

# 高级检索表单中「书名」输入框与「提交」按钮的字段名。
TITLE_FIELD = 'ctl00$ContentPlaceHolder1$txtTitle'
SUBMIT_FIELD = 'ctl00$ContentPlaceHolder1$btnDoIt'

# 单次检索返回的最大候选数。
MAX_CANDIDATES = 8

# 详情页各字段与其对应 HTML span 元素 id 的映射。
DETAIL_SPANS = {
    'title': 'lblBookTitle', 'author': 'lblAuthor', 'quiz_no': 'lblQuizNumber', 'synopsis': 'lblBookSummary',
    'atos': 'lblBookLevel', 'interest_level': 'lblInterestLevel', 'points': 'lblPoints', 'words': 'lblWordCount',
    'fiction': 'lblFictionNonFiction', 'series': 'lblSeriesLabel', 'topics': 'lblTopicLabel',
}


class ArfError(Exception):
    """AR BookFinder 检索、抓取或解析失败的异常。"""

    pass


# 上次调用 AR BookFinder 的时间戳（time.monotonic），用于节流。
_last_call = 0.0


def _throttle():
    """按配置的最小间隔节流对 AR BookFinder 的调用。

    若距上次调用不足 ``settings.ARF_THROTTLE`` 秒，则阻塞等待补足间隔，
    随后更新上次调用时间戳。

    Returns:
        None: 仅产生副作用（可能阻塞当前线程），不返回值。
    """
    global _last_call
    gap = settings.ARF_THROTTLE - (time.monotonic() - _last_call)
    if gap > 0: time.sleep(gap)
    _last_call = time.monotonic()


def _text(markup):
    """将 HTML 片段清洗为单行纯文本。

    先移除 ``<script>``/``<style>`` 整块内容，再剥离所有标签、
    反转义 HTML 实体，并把连续空白压缩为单个空格。

    Args:
        markup (str): 原始 HTML 片段。

    Returns:
        str: 清洗后的纯文本，首尾已去空白。
    """
    markup = re.sub(r'(?s)<(script|style)[^>]*>.*?</\1>', ' ', markup)
    return re.sub(r'\s+', ' ', htmllib.unescape(re.sub(r'(?s)<[^>]+>', ' ', markup))).strip()


def _span(markup, span_id):
    """提取页面中指定 id 的 ``<span>`` 元素的纯文本内容。

    Args:
        markup (str): 页面 HTML。
        span_id (str): 目标 span 的 id（支持后缀匹配）。

    Returns:
        str: 该 span 的纯文本内容；未找到时返回空串。
    """
    found = re.search(r'id="[^"]*%s"[^>]*>(.*?)</span>' % span_id, markup, re.S)
    return _text(found.group(1)) if found else ''


def _hidden(markup):
    """收集表单中所有隐藏 input 字段的 name→value 映射。

    用于在提交 ASP.NET 表单时回填 ``__VIEWSTATE`` 等必需隐藏字段。

    Args:
        markup (str): 表单页 HTML。

    Returns:
        dict[str, str]: 隐藏字段名到其值（均已反转义）的字典。
    """
    fields = {}
    for tag in re.findall(r'<input[^>]*>', markup):
        if 'type="hidden"' not in tag: continue
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if name: fields[htmllib.unescape(name.group(1))] = htmllib.unescape(value.group(1)) if value else ''
    return fields


def _select_defaults(markup):
    """收集表单中每个 ``<select>`` 的首个 option 值作为默认选中项。

    ASP.NET 提交需要连同下拉框的当前值一并回传，此函数取每个下拉框
    的第一个选项值作为默认。

    Args:
        markup (str): 表单页 HTML。

    Returns:
        dict[str, str]: 下拉框 name 到其默认 option 值（均已反转义）的字典。
    """
    fields = {}
    for name, body in re.findall(r'(?s)<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', markup):
        option = re.search(r'<option[^>]*value="([^"]*)"', body)
        fields[htmllib.unescape(name)] = htmllib.unescape(option.group(1)) if option else ''
    return fields


def _decimal(raw):
    """尽力将原始值解析为 Decimal。

    Args:
        raw: 待解析值，可为字符串、数字或 ``None``。

    Returns:
        Decimal | None: 解析成功返回 Decimal；非法或不可解析时返回 ``None``。
    """
    try: return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError): return None


def atos_category(atos):
    """按 ATOS 难度值将书籍归入配置的分级类别。

    依据 ``settings.ATOS_BANDS``（若干「上界→类别」区间，按序匹配）
    判定分类；高于所有上界时归为最高级别。

    Args:
        atos (Decimal | None): ATOS 难度值，``None`` 时无法分类。

    Returns:
        str: 类别标识（如 ``'early_chapter'``）；``atos`` 为 ``None`` 时返回
        空串；超出所有区间时返回 ``'upper_chapter'``。
    """
    if atos is None: return ''
    for ceiling, category in settings.ATOS_BANDS:
        if atos < ceiling: return category
    return 'upper_chapter'


def parse_result_rows(markup):
    """解析检索结果页 HTML，提取候选书籍列表。

    以每本书的标题锚点为界切分，解析出书名、作者、测验号、兴趣等级、
    ATOS、点数与虚实类别，并优先构造无需会话的「打印版」详情 URL。

    Args:
        markup (str): 检索结果页 HTML。

    Returns:
        list[dict]: 候选列表，每项含 ``title``、``url``、``author``、
        ``quiz_no``、``interest_level``、``atos``、``points``、``fiction``
        等键（缺失值为 ``None``），最多 :data:`MAX_CANDIDATES` 条。
    """
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
    """解析书籍详情页 HTML，提取结构化的书籍元数据。

    按 :data:`DETAIL_SPANS` 逐字段抓取 span 文本，并对字数（仅取数字）、
    系列（取分号前的首段）、ATOS/点数（转 Decimal）等做规整。

    Args:
        markup (str): 详情页 HTML。

    Returns:
        dict: 含 ``title``、``author``、``quiz_no``、``synopsis``、``atos``、
        ``points``、``words``、``interest_level``、``fiction``、``series``、
        ``topics`` 等键的字典，空值归一化为 ``None``。
    """
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
    """按书名检索 AR BookFinder，返回候选书籍列表。

    完整走一遍站点的多步表单流程：进入用户类型页并选择「教师」、
    提交后进入高级检索页、填入书名并提交，最后解析结果页。
    每一步之间都会节流，并依赖会话 Cookie 串联。

    Args:
        title (str): 检索书名，空白将被拒绝。

    Returns:
        list[dict]: 候选书籍列表，结构见 :func:`parse_result_rows`。

    Raises:
        ArfError: 书名为空、页面结构与预期不符（缺少 ``__VIEWSTATE``
            或书名字段），或检索被站点拒绝时。
        http.HttpError: 任一请求失败或超时。
    """
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
    """抓取并解析单本书的详情页，返回其元数据。

    仅接受 AR BookFinder 站内 URL；抓取后解析详情，并要求至少能解析出
    ATOS 或字数之一，否则视为无效详情页。

    Args:
        detail_url (str): 详情页地址，须以站点基址 :data:`BASE` 开头。

    Returns:
        dict: 详情字典（结构见 :func:`parse_detail`），并额外附加
        ``url`` 键为传入的详情页地址。

    Raises:
        ArfError: URL 不属于本站、详情不可用（被重定向到错误页），
            或页面既无 ATOS 又无字数、无法解析时。
        http.HttpError: 请求失败或超时。
    """
    if not (detail_url or '').startswith(BASE): raise ArfError('refusing a url outside AR BookFinder')
    opener = http.build_opener(settings.ARF_PROXY)
    _throttle()
    status, markup, url = http.fetch(opener, detail_url, timeout=settings.ARF_TIMEOUT, retries=1)
    if 'bookfindererror' in url: raise ArfError('detail unavailable')
    detail = parse_detail(markup)
    if detail['atos'] is None and detail['words'] is None: raise ArfError('unparsable detail page')
    detail['url'] = detail_url
    return detail
