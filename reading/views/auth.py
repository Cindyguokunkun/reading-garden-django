from datetime import date
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.views import LoginView
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from ..models import Book, Classroom, QuizAttempt, ShelfItem, Student
from ..personas import PARENT, STUDENT, clear_persona, get_persona, persona_required, set_student_persona
from ..stats import rank_rows
from .quiz import MAX_SUBMITTED_ATTEMPTS

staff_login = LoginView.as_view(template_name='reading/login_staff.html', redirect_authenticated_user=True)

def login_hub(request):
    persona = get_persona(request)
    if persona.kind == STUDENT: return redirect('student_home')
    if persona.kind == PARENT: return redirect('parent_home')
    if persona.is_staff: return redirect('dashboard')
    return render(request, 'registration/login.html')

def student_login(request):
    if request.user.is_authenticated: return redirect('dashboard')
    if get_persona(request).kind == STUDENT: return redirect('student_home')
    classrooms = Classroom.objects.all().order_by('grade', 'name')
    return render(request, 'reading/student_login.html', {'classrooms': classrooms})

def student_pick(request, classroom_id):
    if request.user.is_authenticated: return redirect('dashboard')
    classroom = get_object_or_404(Classroom, pk=classroom_id)
    if request.method == 'POST':
        student = get_object_or_404(Student, pk=request.POST.get('student'), classroom=classroom)
        set_student_persona(request, student, STUDENT)
        return redirect('student_home')
    return render(request, 'reading/student_pick.html', {'classroom': classroom, 'students': classroom.students.all().order_by('name')})

def parent_login(request):
    if request.user.is_authenticated: return redirect('dashboard')
    if get_persona(request).kind == PARENT: return redirect('parent_home')
    error = None
    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip(); password = request.POST.get('password') or ''
        student = Student.objects.filter(email__iexact=email).first() if email else None
        if student and student.check_password(password):
            set_student_persona(request, student, PARENT)
            return redirect('parent_home')
        error = _('Email or password is incorrect')
    return render(request, 'reading/parent_login.html', {'error': error})

def _home_context(student):
    records = student.records.filter(passed=True)
    totals = rank_rows(Classroom.objects.filter(pk=student.classroom_id), date.min, date.max)
    total = next((r for r in totals if r['student_id'] == student.pk), {'words': 0, 'minutes': 0, 'books': 0})
    goal = getattr(student.classroom, 'goal', None)
    class_words = sum(r['words'] for r in totals)
    goal_percent = min(100, round(class_words * 100 / goal.words)) if goal and goal.words else 0
    return {'student': student, 'classroom': student.classroom, 'records': records.select_related('book')[:50],
            'totals': total, 'goal': goal, 'class_words': class_words, 'goal_percent': goal_percent}

def _attempt_rows(student):
    rows = list(QuizAttempt.objects.filter(student=student, submitted=True)
        .select_related('book').order_by('-completed_at')[:30])
    failed = {row['book_id']: row['n'] for row in QuizAttempt.objects
        .filter(student=student, submitted=True, passed=False).values('book_id').annotate(n=Count('id'))}
    for attempt in rows:
        attempt.remaining = max(MAX_SUBMITTED_ATTEMPTS - failed.get(attempt.book_id, 0), 0)
    return rows

@persona_required(STUDENT)
def student_home(request):
    return render(request, 'reading/student_home.html', _home_context(get_persona(request).student))

@persona_required(PARENT)
def parent_home(request):
    student = get_persona(request).student
    return render(request, 'reading/parent_home.html', {**_home_context(student), 'attempts': _attempt_rows(student)})

@require_POST
def logout(request):
    if request.user.is_authenticated: auth_logout(request)
    else: clear_persona(request)
    return redirect('login')

def shelf_state(student, books):
    """Stamp each book with in_shelf and the best passed score, in two queries."""
    passed = {row['book_id']: row['best'] for row in QuizAttempt.objects
        .filter(student=student, passed=True, submitted=True).values('book_id').annotate(best=Max('score'))}
    on_shelf = set(ShelfItem.objects.filter(student=student).values_list('book_id', flat=True))
    for book in books:
        book.in_shelf = book.pk in on_shelf
        book.shelf_best = passed.get(book.pk, '')
    return on_shelf

@persona_required(STUDENT, PARENT)
def shelf(request):
    student = get_persona(request).student
    items = list(student.shelf.select_related('book')[:200])
    shelf_state(student, [item.book for item in items])
    return render(request, 'reading/shelf.html', {'items': items, 'count': len(items), 'student': student})

@persona_required(STUDENT, PARENT)
@require_POST
def shelf_change(request):
    student = get_persona(request).student
    book = get_object_or_404(Book, pk=request.POST.get('book'))
    if request.POST.get('remove'):
        ShelfItem.objects.filter(student=student, book=book).delete()
    else:
        ShelfItem.objects.get_or_create(student=student, book=book)
    where = request.POST.get('next') or 'shelf'
    if where == 'library': return redirect('library')
    if where == 'book_detail': return redirect('book_detail', pk=book.pk)
    return redirect('shelf')
