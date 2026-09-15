import re
import time
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from django.conf import settings
from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from ..models import CATEGORY_CHOICES, Book
from ..personas import MANAGER, PARENT, STUDENT, TEACHER, persona_required
from ..services import arbookfinder, covers, quizgen
from ..services.http import HttpError

MAX_QUESTIONS = 12
IN_FLIGHT_KEY = 'quizgen_in_flight'
FORM_PATHS = re.compile(r'^/library/(?:add/|\d+/edit/)$')

@persona_required(TEACHER, MANAGER, STUDENT, PARENT)
def library(request):
    books = Book.objects.all()
    category = request.GET.get('category', '')
    q = request.GET.get('q', '').strip()
    if category: books = books.filter(category=category)
    if q: books = books.filter(title__icontains=q)
    counts = {r['category']: r['n'] for r in Book.objects.values('category').annotate(n=Count('id'))}
    chips = [{'value': value, 'label': str(label), 'count': counts.get(value, 0)} for value, label in CATEGORY_CHOICES]
    return render(request, 'reading/library.html', {
        'books': books.order_by('series', 'title')[:500], 'categories': CATEGORY_CHOICES, 'chips': chips,
        'category': category, 'q': q, 'total': Book.objects.count(),
    })

@persona_required(TEACHER, MANAGER, STUDENT, PARENT)
def book_detail(request, pk):
    book = get_object_or_404(Book, pk=pk)
    siblings = Book.objects.none() if _blank(book, 'series') else Book.objects.filter(series=book.series).exclude(pk=book.pk).order_by('title')[:12]
    return render(request, 'reading/book_detail.html', {'book': book, 'siblings': siblings})

def _atos(raw):
    raw = (raw or '').strip()
    if not raw: return None
    try:
        return Decimal(raw).quantize(Decimal('0.1'))
    except InvalidOperation:
        return None

def _apply_form(book, post):
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
    return book

def _book_from_post(request):
    pk = (request.POST.get('pk') or '').strip()
    book = get_object_or_404(Book, pk=int(pk)) if pk.isdigit() else Book(source_id=f'manual-{uuid.uuid4().hex[:12]}')
    return _apply_form(book, request.POST)

def _blank(book, field):
    value = getattr(book, field)
    return not value or (field == 'series' and value == _('Standalone'))

def _editor_rows(questions):
    rows = []
    for question in questions:
        row = dict(question)
        options = [str(option) for option in (row.get('options') or [])]
        row['options'] = (options + [''] * 4)[:max(4, len(options))]
        rows.append(row)
    return rows

def _form_context(book, **extra):
    context = {'book': book, 'categories': CATEGORY_CHOICES, 'quizgen_enabled': settings.QUIZGEN_ENABLED,
        'candidates': [], 'cover_candidates': [], 'questions': None, 'errors': [], 'words_choice': '', 'words_arf': '', 'material': ''}
    context.update(extra)
    if context['questions']: context['questions'] = _editor_rows(context['questions'])
    return context

def _render_form(request, book, **extra):
    return render(request, 'reading/book_form.html', _form_context(book, **extra))

def _question_rows(post):
    """Rebuild the question editor rows, or None when the form posted no editor at all."""
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
    book = Book(source_id=f'manual-{uuid.uuid4().hex[:12]}')
    if request.method == 'POST':
        return _save_book(request, _apply_form(book, request.POST), _('Book added'))
    return _render_form(request, book)

@persona_required(TEACHER, MANAGER)
def book_edit(request, pk):
    book = get_object_or_404(Book, pk=pk)
    if request.method == 'POST':
        return _save_book(request, _apply_form(book, request.POST), _('Book updated'))
    return _render_form(request, book)

def _pick(request, book, argument):
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
    if detail['atos'] is not None: book.atos = detail['atos']
    if detail['synopsis']: book.synopsis = detail['synopsis']
    for field, value in (('author', detail['author']), ('category', arbookfinder.atos_category(detail['atos'])), ('series', detail['series']),
                         ('level', detail['interest_level']), ('source', detail['url'])):
        if value and _blank(book, field): setattr(book, field, value)
    messages.success(request, _('The AR BookFinder details are filled in. Check them, then save.'))
    return conflict

def _cover_pick(request, book, argument):
    index = int(argument) if argument.isdigit() else -1
    try:
        book.cover = covers.assert_cover_url(request.POST.get(f'v{index}_cover'))
    except covers.CoverError:
        messages.error(request, _('That cover address is not from Open Library or Google Books. Use one of the two services, or paste their thumbnail address.'))
        return
    messages.success(request, _('The cover is filled in. Check it, then save.'))

def _generate(request, book):
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
    path = urlparse(request.META.get('HTTP_REFERER') or '').path
    messages.warning(request, _('Reloading this page discards unsaved changes, including any generated questions. Open the book form again.'))
    return redirect(path if FORM_PATHS.match(path) else 'library')

@persona_required(TEACHER, MANAGER)
def book_tool(request):
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
                candidates = arbookfinder.search_candidates(book.title)
                if candidates:
                    extra['candidates'] = candidates
                    messages.success(request, _('Found %(count)s matches. Choose the right one to fill in the difficulty.') % {'count': len(candidates)})
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
