"""书库浏览与书籍编辑工具视图。

本模块面向教师与管理员，提供书籍的增删改查，并集成三类外部工具：

- AR BookFinder 检索：按书名查找版本、回填作者/难度/字数等元数据。
- 封面检索：从 Open Library / Google Books 拉取候选封面。
- AI 出题：基于书籍文本或简介生成阅读理解题草稿，供人工编辑后保存。

书籍表单以「无刷新式工具面板」交互：各工具通过 :func:`book_tool` 统一
POST 入口按 ``action`` 分派，结果连同已填字段回显到同一表单页，
用户在确认无误后再显式保存。
"""

import re
import time
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from ..models import CATEGORY_CHOICES, Book
from ..personas import MANAGER, PARENT, STUDENT, TEACHER, get_persona, persona_required
from ..services import arbookfinder, covers, quizgen
from ..services.http import HttpError
from .auth import shelf_state

# 单本书籍题目编辑器允许的最大题目数。
MAX_QUESTIONS = 12

# 会话键：标记当前是否有 AI 出题请求正在进行，避免多标签页并发生成。
IN_FLIGHT_KEY = 'quizgen_in_flight'

# 匹配书籍表单页路径（新增页或编辑页），用于安全地重定向回来源表单。
FORM_PATHS = re.compile(r'^/library/(?:add/|\d+/edit/)$')


def _visible_books(request):
    organization = get_persona(request).organization
    return Book.objects.filter(Q(organization__isnull=True) | Q(organization=organization))


@persona_required(TEACHER, MANAGER, STUDENT, PARENT)
def library(request):
    """书库列表视图，支持按分类筛选与书名模糊搜索。

    Args:
        request (HttpRequest): 当前请求对象。支持的 GET 参数：
            ``category``（分类过滤）、``q``（书名包含匹配）。

    Returns:
        HttpResponse: 渲染 ``reading/library.html`` 的响应，携带书籍列表
        （最多 500 本）、分类选项与带计数的分类筛选 chips、当前筛选条件
        及馆藏总数。学生/家长身份会额外标注每本书的书架收藏状态。
    """
    books = _visible_books(request)
    category = request.GET.get('category', '')
    selected_series = request.GET.get('series', '').strip()
    standalone = selected_series == '__standalone__'
    q = request.GET.get('q', '').strip()
    all_visible = books
    counts = {r['category']: r['n'] for r in all_visible.values('category').annotate(n=Count('id'))}
    if category: books = books.filter(category=category)
    series_groups = list(books.values('series', 'category').annotate(count=Count('id')).order_by('series'))
    if standalone: books = books.filter(series='')
    elif selected_series: books = books.filter(series=selected_series)
    if q: books = books.filter(Q(title__icontains=q) | Q(series__icontains=q))
    chips = [{'value': value, 'label': str(label), 'count': counts.get(value, 0)} for value, label in CATEGORY_CHOICES]
    show_books = bool(selected_series or q or books.count() <= 12)
    books = list(books.order_by('series', 'series_order', 'title')[:500]) if show_books else []
    persona = get_persona(request)
    if persona.student: shelf_state(persona.student, books)
    return render(request, 'reading/library.html', {
        'books': books, 'categories': CATEGORY_CHOICES, 'chips': chips,
        'category': category, 'q': q, 'total': _visible_books(request).count(),
        'series_groups': series_groups, 'selected_series': selected_series,
        'show_books': show_books,
    })


@persona_required(TEACHER, MANAGER, STUDENT, PARENT)
def book_detail(request, pk):
    """书籍详情视图，展示单本书籍信息与同系列的其他书籍。

    Args:
        request (HttpRequest): 当前请求对象。
        pk (int): 书籍主键。

    Returns:
        HttpResponse: 渲染 ``reading/book_detail.html`` 的响应，携带书籍
        与同系列书籍（最多 12 本，无系列时为空）。学生/家长身份会
        标注该书的书架收藏状态。

    Raises:
        Http404: 指定书籍不存在。
    """
    book = get_object_or_404(_visible_books(request), pk=pk)
    siblings = Book.objects.none() if _blank(book, 'series') else _visible_books(request).filter(series=book.series).exclude(pk=book.pk).order_by('series_order', 'title')[:12]
    persona = get_persona(request)
    if persona.student: shelf_state(persona.student, [book])
    return render(request, 'reading/book_detail.html', {'book': book, 'siblings': siblings})


def _atos(raw):
    """将表单提交的 ATOS 原始文本解析为一位小数的 Decimal。

    Args:
        raw (str | None): 表单中的 ATOS 字段值。

    Returns:
        Decimal | None: 解析并量化到 0.1 后的结果；空值或非法数字
        返回 ``None``。
    """
    raw = (raw or '').strip()
    if not raw: return None
    try:
        return Decimal(raw).quantize(Decimal('0.1'))
    except InvalidOperation:
        return None


def _apply_form(book, post):
    """将表单 POST 数据写入书籍实例的各字段（不保存）。

    对文本字段做去空白处理；系列留空时归一化为「Standalone」；
    字数、作者还兼容来自 AR BookFinder 工具回填的备选值
    （``words_choice``/``author_choice``）。

    Args:
        book (Book): 目标书籍实例，就地修改。
        post (QueryDict): 表单提交数据。

    Returns:
        Book: 已填充字段、但尚未 ``save()`` 的同一书籍实例。
    """
    book.title = (post.get('title') or '').strip()
    book.author = (post.get('author') or '').strip()
    book.series = (post.get('series') or '').strip() or _('Standalone')
    book.level = (post.get('level') or '').strip()
    book.category = post.get('category') or ''
    book.lexile = (post.get('lexile') or '').strip()
    book.synopsis = (post.get('synopsis') or '').strip()
    book.cover = (post.get('cover') or '').strip()
    words = (post.get('words') or '').strip()
    book.words = int(words) if words.isdigit() else None
    book.atos = _atos(post.get('atos'))
    if post.get('words_choice') == 'arf':
        arf_words = (post.get('words_arf') or '').strip()
        if arf_words.isdigit(): book.words = int(arf_words)
    if post.get('author_choice') == 'found':
        found = (post.get('author_found') or '').strip()
        if found: book.author = found[:200]
    return book


def _book_from_post(request):
    """根据 POST 数据构造待编辑的书籍实例（不保存）。

    当 POST 含合法数字 ``pk`` 时加载既有书籍，否则新建一本带随机
    ``source_id`` 的书籍实例，随后套用表单字段。

    Args:
        request (HttpRequest): 当前请求对象，读取其 ``POST``。

    Returns:
        Book: 已填充表单字段、尚未保存的书籍实例。

    Raises:
        Http404: 指定 ``pk`` 的书籍不存在。
    """
    pk = (request.POST.get('pk') or '').strip()
    if pk.isdigit():
        persona = get_persona(request)
        editable = Book.objects.filter(organization=persona.organization)
        if persona.is_platform_admin:
            editable = _visible_books(request)
        book = get_object_or_404(editable, pk=int(pk))
    else:
        persona = get_persona(request)
        book = Book(
            source_id=f'manual-{uuid.uuid4().hex[:12]}',
            organization=None if persona.is_platform_admin else persona.organization,
        )
    return _apply_form(book, request.POST)


def _blank(book, field):
    """判断书籍某字段是否应视为「空」，用于决定是否可被工具回填覆盖。

    Args:
        book (Book): 目标书籍实例。
        field (str): 字段名。

    Returns:
        bool: 字段值为假值时返回 ``True``；特别地，``series`` 字段等于
        「Standalone」占位值时也视为空。
    """
    value = getattr(book, field)
    return not value or (field == 'series' and value == _('Standalone'))


def _editor_rows(questions):
    """将题目数据规整为题目编辑器所需的行结构。

    每题的选项列表会被补足到至少 4 个（不足处填空字符串），
    以便前端渲染固定的选项输入框。

    Args:
        questions (list[dict]): 原始题目列表，每题含 ``options`` 等键。

    Returns:
        list[dict]: 规整后的题目行副本列表，``options`` 已转为字符串并补齐。
    """
    rows = []
    for question in questions:
        row = dict(question)
        options = [str(option) for option in (row.get('options') or [])]
        row['options'] = (options + [''] * 4)[:max(4, len(options))]
        rows.append(row)
    return rows


def _form_context(book, **extra):
    """构建书籍表单页的完整渲染上下文。

    在一份包含所有工具默认空值的基线上下文上，合并调用方传入的
    ``extra`` 覆盖项；若含题目则一并规整为编辑器行结构。

    Args:
        book (Book): 当前编辑的书籍实例。
        **extra: 需要覆盖或补充的上下文键值（如候选结果、题目、错误等）。

    Returns:
        dict: 供 ``reading/book_form.html`` 使用的上下文字典。
    """
    context = {'book': book, 'categories': CATEGORY_CHOICES, 'quizgen_enabled': settings.QUIZGEN_ENABLED,
        'candidates': [], 'cover_candidates': [], 'questions': None, 'errors': [], 'words_choice': '', 'words_arf': '',
        'author_choice': '', 'author_found': '', 'material': ''}
    context.update(extra)
    if context['questions']: context['questions'] = _editor_rows(context['questions'])
    return context


def _render_form(request, book, **extra):
    """渲染书籍表单页的便捷封装。

    Args:
        request (HttpRequest): 当前请求对象。
        book (Book): 当前编辑的书籍实例。
        **extra: 透传给 :func:`_form_context` 的上下文覆盖项。

    Returns:
        HttpResponse: 渲染 ``reading/book_form.html`` 的响应。
    """
    return render(request, 'reading/book_form.html', _form_context(book, **extra))


def _question_rows(post):
    """从表单 POST 重建题目编辑器的行数据。

    Args:
        post (QueryDict): 表单提交数据。以 ``q0_prompt`` 是否存在判断
            本次是否提交了题目编辑器；逐题读取 ``q{i}_prompt``、
            ``q{i}_opt{slot}``、``q{i}_answer``，并跳过标记删除的题目。

    Returns:
        list[dict] | None: 题目行列表（每行含 ``prompt``、过滤空值后的
        ``options`` 与 ``answer``）；当表单完全未提交编辑器时返回 ``None``。
    """
    if 'q0_prompt' not in post: return None
    rows = []
    for index in range(MAX_QUESTIONS):
        prompt = post.get(f'q{index}_prompt')
        if prompt is None: break
        if post.get(f'q{index}_drop'): continue
        options = [(post.get(f'q{index}_opt{slot}') or '').strip() for slot in range(6)]
        answer = (post.get(f'q{index}_answer') or '').strip()
        rows.append({'prompt': prompt, 'options': [option for option in options if option], 'answer': int(answer) if answer.isdigit() else answer})
    return rows


def _save_book(request, book, notice):
    """校验并保存书籍（含题目），成功后重定向回书库。

    若表单提交了题目编辑器，则先经 :func:`quizgen.validate_questions`
    校验；有错误时回填表单并展示错误，不保存。校验通过才写入题目并保存。

    Args:
        request (HttpRequest): 当前请求对象，读取其 ``POST``。
        book (Book): 已填充表单字段、待保存的书籍实例。
        notice (str): 保存成功后的提示文案。

    Returns:
        HttpResponse: 校验失败时返回重新渲染的表单响应；
        成功时返回重定向到 ``library`` 的响应。
    """
    rows = _question_rows(request.POST)
    questions, errors = (None, []) if rows is None else quizgen.validate_questions(rows, min_questions=0, max_questions=MAX_QUESTIONS)
    if errors:
        return _render_form(request, book, questions=rows, errors=errors, material=request.POST.get('material', ''))
    if questions is not None: book.quiz_data = questions
    book.save()
    messages.success(request, notice)
    return redirect('library')


@persona_required(TEACHER, MANAGER)
def book_add(request):
    """新增书籍视图：GET 展示空表单，POST 校验并保存。

    Args:
        request (HttpRequest): 当前请求对象，需为教师或管理员身份。

    Returns:
        HttpResponse: 渲染空表单，或保存后重定向到 ``library``
        （校验失败则回显表单）的响应。
    """
    persona = get_persona(request)
    book = Book(
        source_id=f'manual-{uuid.uuid4().hex[:12]}',
        organization=None if persona.is_platform_admin else persona.organization,
    )
    if request.method == 'POST':
        return _save_book(request, _apply_form(book, request.POST), _('Book added'))
    return _render_form(request, book)


@persona_required(TEACHER, MANAGER)
def book_edit(request, pk):
    """编辑既有书籍视图：GET 展示已填表单，POST 校验并保存。

    Args:
        request (HttpRequest): 当前请求对象，需为教师或管理员身份。
        pk (int): 书籍主键。

    Returns:
        HttpResponse: 渲染表单，或保存后重定向到 ``library``
        （校验失败则回显表单）的响应。

    Raises:
        Http404: 指定书籍不存在。
    """
    persona = get_persona(request)
    editable = Book.objects.filter(organization=persona.organization)
    if persona.is_platform_admin:
        editable = _visible_books(request)
    book = get_object_or_404(editable, pk=pk)
    if request.method == 'POST':
        return _save_book(request, _apply_form(book, request.POST), _('Book updated'))
    return _render_form(request, book)


def _slug(title):
    """将书名归一化为仅含小写字母与数字的键，用于宽松匹配封面。

    Args:
        title (str | None): 原始书名。

    Returns:
        str: 去除所有非字母数字字符并转小写后的结果。
    """
    return re.sub(r'[^a-z0-9]', '', (title or '').lower())


def _attach_covers(candidates, cover_rows):
    """为 AR BookFinder 候选项按书名匹配并附加封面地址。

    Args:
        candidates (list[dict]): AR BookFinder 候选书籍，每项含 ``title``。
        cover_rows (list[dict]): 封面检索结果，每项含 ``title`` 与 ``cover``。

    Returns:
        list[dict]: 候选项副本列表，每项新增 ``cover`` 键（无匹配时为空串）。
    """
    by_title = {}
    for row in cover_rows:
        by_title.setdefault(_slug(row['title']), row['cover'])
    return [dict(candidate, cover=by_title.get(_slug(candidate['title']), '')) for candidate in candidates]


def _cover_rows(request, title):
    """按书名检索封面，作为上下文片段返回；检索失败时给出提示。

    Args:
        request (HttpRequest): 当前请求对象，用于写入消息提示。
        title (str): 检索用的书名。

    Returns:
        dict: 含 ``cover_candidates`` 键的字典；无结果时该键为空列表，
        并向用户提示改为手填封面或改用英文书名。
    """
    try:
        return {'cover_candidates': covers.search_covers(title)}
    except covers.CoverError:
        messages.info(request, _('Neither cover service returned a cover for that title. Paste a cover image address by hand, or search the English title.'))
        return {'cover_candidates': []}


def _pick(request, book, argument):
    """应用用户选中的 AR BookFinder 候选项，回填书籍元数据。

    抓取候选详情页后，按「仅填空缺、冲突则交由用户抉择」的策略回填
    字数、作者、ATOS、简介、分类、系列、难度与来源链接等字段；
    字数或作者与库中已有值不一致时，通过消息提示并返回冲突信息供前端选择。

    Args:
        request (HttpRequest): 当前请求对象，读取候选 URL/封面等 POST 字段。
        book (Book): 待回填的书籍实例，就地修改。
        argument (str): 候选项索引（数字字符串），非法时按 -1 处理。

    Returns:
        dict: 冲突信息字典，可能含 ``words_choice``/``words_arf`` 与
        ``author_choice``/``author_found``，供表单展示二选一控件；
        无冲突时为空字典。

    Raises:
        arbookfinder.ArfError: 未选中候选（缺少 URL）或详情抓取失败时。
    """
    index = int(argument) if argument.isdigit() else -1
    url = (request.POST.get(f'c{index}_url') or '').strip()
    if not url: raise arbookfinder.ArfError('no candidate was chosen')
    detail = arbookfinder.fetch_detail(url)
    conflict = {}
    if detail['words']:
        if not book.words:
            book.words = detail['words']
        elif detail['words'] != book.words:
            conflict = {'words_choice': 'db', 'words_arf': detail['words']}
            messages.info(request, _('AR BookFinder reports a different word count. Choose the one to keep.'))
    if detail['author']:
        if _blank(book, 'author'):
            book.author = detail['author']
        elif detail['author'] != book.author:
            conflict.update({'author_choice': 'db', 'author_found': detail['author']})
            messages.info(request, _('AR BookFinder reports a different author. Choose the one to keep.'))
    if detail['atos'] is not None: book.atos = detail['atos']
    if detail['synopsis']: book.synopsis = detail['synopsis']
    for field, value in (('category', arbookfinder.atos_category(detail['atos'])), ('series', detail['series']),
                         ('level', detail['interest_level']), ('source', detail['url'])):
        if value and _blank(book, field): setattr(book, field, value)
    cover = (request.POST.get(f'c{index}_cover') or '').strip()
    if cover and _blank(book, 'cover'):
        try:
            book.cover = covers.assert_cover_url(cover)
        except covers.CoverError:
            pass
    messages.success(request, _('The AR BookFinder details are filled in. Check them, then save.'))
    return conflict


def _cover_pick(request, book, argument):
    """应用用户选中的封面候选项，写入书籍封面地址。

    仅接受来自受信任封面服务的 URL；非法时给出错误提示且不修改封面。

    Args:
        request (HttpRequest): 当前请求对象，读取封面 URL 的 POST 字段。
        book (Book): 待回填封面的书籍实例，就地修改。
        argument (str): 候选项索引（数字字符串），非法时按 -1 处理。

    Returns:
        None: 结果通过消息提示反馈，不返回值。
    """
    index = int(argument) if argument.isdigit() else -1
    try:
        book.cover = covers.assert_cover_url(request.POST.get(f'v{index}_cover'))
    except covers.CoverError:
        messages.error(request, _('That cover address is not from Open Library or Google Books. Use one of the two services, or paste their thumbnail address.'))
        return
    messages.success(request, _('The cover is filled in. Check it, then save.'))


def _generate(request, book):
    """调用 AI 出题服务，为书籍生成阅读理解题草稿。

    通过会话中的进行中标记避免多标签页并发生成；素材优先取上传文件
    或粘贴文本，缺省时回退到书籍简介。无论成功与否都会清除进行中标记。

    Args:
        request (HttpRequest): 当前请求对象，读取上传文件/粘贴文本及会话。
        book (Book): 目标书籍，提供标题、系列、难度、字数与简介等上下文。

    Returns:
        dict: 成功时含 ``questions``（生成的题目）与 ``material``（所用素材）；
        当检测到另一标签页正在生成时返回空字典并提示用户等待。

    Raises:
        quizgen.QuizGenError: 由底层出题服务抛出（如未配置、素材为空、
            超时或服务返回不可用），交由调用方统一捕获处理。
    """
    started = request.session.get(IN_FLIGHT_KEY)
    if started and time.monotonic() - started < settings.QUIZGEN_TIMEOUT + 30:
        messages.error(request, _('Questions are still being generated in another tab. Wait for that one to finish.'))
        return {}
    material = quizgen.read_material(request.FILES.get('material_file'), request.POST.get('material')) or book.synopsis
    request.session[IN_FLIGHT_KEY] = time.monotonic()
    try:
        questions = quizgen.generate_questions(title=book.title, series=book.series, atos=book.atos, words=book.words,
            category_label=dict(CATEGORY_CHOICES).get(book.category, ''), material=material)
    finally:
        request.session.pop(IN_FLIGHT_KEY, None)
    messages.success(request, _('Here are %(count)s draft questions. Edit them, then save.') % {'count': len(questions)})
    return {'questions': questions, 'material': request.POST.get('material', '')}


def _reload_redirect(request):
    """处理对表单页的非 POST（刷新/直接访问）请求，安全重定向。

    由于刷新会丢弃未保存的改动（包括已生成的题目），此处给出警告并
    仅在来源路径确为书籍表单页时重定向回该表单，否则回到书库。

    Args:
        request (HttpRequest): 当前请求对象，读取 ``HTTP_REFERER``。

    Returns:
        HttpResponse: 重定向回表单页或 ``library`` 的响应。
    """
    path = urlparse(request.META.get('HTTP_REFERER') or '').path
    messages.warning(request, _('Reloading this page discards unsaved changes, including any generated questions. Open the book form again.'))
    return redirect(path if FORM_PATHS.match(path) else 'library')


@persona_required(TEACHER, MANAGER)
def book_tool(request):
    """书籍编辑工具的统一 POST 分派入口。

    按 POST ``action`` 字段（形如 ``名称:参数``）执行对应工具：

    - ``lookup``: 按书名检索 AR BookFinder 候选与封面。
    - ``pick``: 应用选中的 AR BookFinder 候选，回填元数据。
    - ``cover``: 仅检索封面候选。
    - ``coverpick``: 应用选中的封面。
    - ``quizgen``: 调用 AI 生成题目草稿。
    - ``editquiz``: 载入既有题目到编辑器（书籍须已保存）。

    所有工具异常均被捕获并转为用户可读的消息提示，最终连同已填字段
    回显到表单页。非 POST 请求交给 :func:`_reload_redirect` 处理。

    Args:
        request (HttpRequest): 当前请求对象，需为教师或管理员身份。
            POST 需含 ``action``，并按工具需要提供相应字段。

    Returns:
        HttpResponse: 渲染书籍表单页（携带工具结果与消息）的响应，
        或非 POST 时的重定向响应。
    """
    if request.method != 'POST':
        return _reload_redirect(request)
    action, separator, argument = (request.POST.get('action') or '').partition(':')
    book = _book_from_post(request)
    extra = {'material': request.POST.get('material', '')}
    try:
        if action == 'lookup':
            if not book.title:
                messages.error(request, _('Type the book title first, then look it up.'))
            else:
                extra.update(_cover_rows(request, book.title))
                candidates = arbookfinder.search_candidates(book.title)
                if candidates:
                    extra['candidates'] = _attach_covers(candidates, extra['cover_candidates'])
                    messages.success(request, _('Found %(count)s editions. Pick the right one to fill in the author, level and word count.') % {'count': len(candidates)})
                else:
                    messages.error(request, _('AR BookFinder has no match for that title. Fill in the fields by hand, or search the English title.'))
        elif action == 'pick':
            extra.update(_pick(request, book, argument))
        elif action == 'cover':
            if not book.title:
                messages.error(request, _('Type the book title first, then look it up.'))
            else:
                extra['cover_candidates'] = covers.search_covers(book.title)
                messages.success(request, _('Found %(count)s covers. Choose the right one.') % {'count': len(extra['cover_candidates'])})
        elif action == 'coverpick':
            _cover_pick(request, book, argument)
        elif action == 'quizgen':
            extra.update(_generate(request, book))
        elif action == 'editquiz':
            if not book.pk:
                messages.error(request, _('Save the book first, then edit its questions.'))
            else:
                extra['questions'] = [dict(question) for question in book.quiz_data]
        else:
            messages.error(request, _('That tool is not available.'))
    except arbookfinder.ArfError:
        messages.error(request, _('AR BookFinder did not return usable data. You can still fill in every field by hand.'))
    except covers.CoverError:
        messages.error(request, _('Neither cover service returned a cover for that title. Paste a cover image address by hand, or search the English title.'))
    except HttpError:
        messages.error(request, _('The request did not reach the service. Check the network, or the proxy in .env.'))
    except quizgen.QuizGenError as error:
        messages.error(request, str(error))
    return _render_form(request, book, **extra)
