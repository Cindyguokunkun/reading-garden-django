import random
from datetime import date
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from ..models import Book, Classroom, QuizAttempt, ReadingRecord, Student
from ..personas import MANAGER, STUDENT, TEACHER, current_classroom, get_persona, persona_required

MAX_SUBMITTED_ATTEMPTS = 3

def _attempt_stats(student, book):
    passed = QuizAttempt.objects.filter(student=student, book=book, passed=True, submitted=True).exists()
    failed = QuizAttempt.objects.filter(student=student, book=book, passed=False, submitted=True).count()
    return passed, failed

def _quizable_books():
    return Book.objects.exclude(quiz_data=[]).order_by('series', 'title')

def _build_questions(book, retake):
    questions = [dict(q) for q in book.quiz_data]
    if retake: random.shuffle(questions)
    for q in questions:
        correct = q['options'][q['answer']]; random.shuffle(q['options']); q['answer'] = q['options'].index(correct)
    return questions

@persona_required(TEACHER, MANAGER, STUDENT)
def quiz_start(request):
    persona = get_persona(request)
    classroom = current_classroom(request)
    if request.method == 'POST':
        if persona.kind == STUDENT:
            student = persona.student
        else:
            student = get_object_or_404(Student, pk=request.POST['student'], classroom=classroom)
        book = get_object_or_404(Book, pk=request.POST['book'])
        passed, failed = _attempt_stats(student, book)
        back = 'student_home' if persona.kind == STUDENT else 'quiz_start'
        if passed:
            messages.error(request, _('This book has already been passed.'))
            return redirect(back)
        if failed >= MAX_SUBMITTED_ATTEMPTS:
            messages.error(request, _('All 3 quiz attempts for this book have been used.'))
            return redirect(back)
        attempt = QuizAttempt.objects.create(student=student, book=book, score=0, passed=False, answers=[], started_at=timezone.now())
        request.session[f'quiz_{attempt.pk}'] = _build_questions(book, retake=failed > 0)
        request.session[f'quiz_meta_{attempt.pk}'] = {'date': request.POST.get('date') or date.today().isoformat(), 'minutes': request.POST.get('minutes') or None}
        return redirect('quiz_take', attempt_id=attempt.pk)
    if persona.kind == STUDENT:
        return render(request, 'reading/quiz_start.html', {'classroom': classroom, 'students': [], 'books': _quizable_books()})
    return render(request, 'reading/quiz_start.html', {'classroom': classroom, 'classes': Classroom.objects.filter(owner=request.user), 'students': classroom.students.all() if classroom else [], 'books': _quizable_books()})

def _can_access_attempt(persona, attempt):
    if persona.kind == STUDENT: return attempt.student_id == persona.student.pk
    if persona.kind == MANAGER: return True
    if persona.kind == TEACHER: return attempt.student.classroom.owner_id == persona.user.pk
    return False

@persona_required(TEACHER, MANAGER, STUDENT)
def quiz_take(request, attempt_id):
    persona = get_persona(request)
    attempt = get_object_or_404(QuizAttempt, pk=attempt_id)
    if not _can_access_attempt(persona, attempt):
        return HttpResponseForbidden()
    questions = request.session.get(f'quiz_{attempt.pk}', [])
    if request.method == 'POST':
        answers = [int(request.POST.get(f'q{i}', -1)) for i in range(len(questions))]; correct = sum(a == q['answer'] for a, q in zip(answers, questions)); score = round(correct * 100 / len(questions)) if questions else 0
        attempt.score = score; attempt.passed = score >= 60; attempt.answers = answers; attempt.submitted = True; attempt.save()
        if attempt.passed and not ReadingRecord.objects.filter(student=attempt.student, book=attempt.book, passed=True).exists():
            meta = request.session.get(f'quiz_meta_{attempt.pk}', {}); minutes = meta.get('minutes')
            ReadingRecord.objects.create(student=attempt.student, book=attempt.book, read_date=meta.get('date') or date.today(), words=attempt.book.words or 0, minutes=int(minutes) if minutes else None, quiz_score=score, passed=True)
        remaining = MAX_SUBMITTED_ATTEMPTS - QuizAttempt.objects.filter(student=attempt.student, book=attempt.book, passed=False, submitted=True).count()
        return render(request, 'reading/quiz_result.html', {'attempt': attempt, 'correct': correct, 'total': len(questions), 'remaining': max(remaining, 0)})
    attempt_number = QuizAttempt.objects.filter(student=attempt.student, book=attempt.book, passed=False, submitted=True).count() + 1
    return render(request, 'reading/quiz_take.html', {'attempt': attempt, 'questions': questions, 'attempt_number': attempt_number, 'max_attempts': MAX_SUBMITTED_ATTEMPTS})
