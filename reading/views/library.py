import uuid
from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from ..models import CATEGORY_CHOICES, Book
from ..personas import MANAGER, PARENT, STUDENT, TEACHER, persona_required

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

def _apply_form(book, post):
    book.title = post['title'].strip()
    book.series = (post.get('series') or '').strip() or _('Standalone')
    book.level = (post.get('level') or '').strip()
    book.category = post.get('category') or ''
    book.lexile = (post.get('lexile') or '').strip()
    words = (post.get('words') or '').strip()
    book.words = int(words) if words.isdigit() else None
    return book

@persona_required(TEACHER, MANAGER)
def book_add(request):
    book = Book(source_id=f'manual-{uuid.uuid4().hex[:12]}')
    if request.method == 'POST':
        _apply_form(book, request.POST); book.save()
        messages.success(request, _('Book added'))
        return redirect('library')
    return render(request, 'reading/book_form.html', {'book': book, 'categories': CATEGORY_CHOICES})

@persona_required(TEACHER, MANAGER)
def book_edit(request, pk):
    book = get_object_or_404(Book, pk=pk)
    if request.method == 'POST':
        _apply_form(book, request.POST); book.save()
        messages.success(request, _('Book updated'))
        return redirect('library')
    return render(request, 'reading/book_form.html', {'book': book, 'categories': CATEGORY_CHOICES})
