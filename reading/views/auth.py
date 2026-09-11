from datetime import date
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.views import LoginView
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from ..models import Classroom, Student
from ..personas import PARENT, STUDENT, clear_persona, get_persona, persona_required, set_student_persona
from ..stats import rank_rows

staff_login = LoginView.as_view(template_name='reading/login_staff.html', redirect_authenticated_user=True)

def login_hub(request):
    persona = get_persona(request)
    if persona.kind == STUDENT: return redirect('student_home')
    if persona.kind == PARENT: return redirect('parent_home')
    if persona.is_staff: return redirect('dashboard')
    return render(request, 'registration/login.html')

def student_login(request):
    if get_persona(request).kind == STUDENT: return redirect('student_home')
    classrooms = Classroom.objects.all().order_by('grade', 'name')
    return render(request, 'reading/student_login.html', {'classrooms': classrooms})

def student_pick(request, classroom_id):
    classroom = get_object_or_404(Classroom, pk=classroom_id)
    if request.method == 'POST':
        student = get_object_or_404(Student, pk=request.POST.get('student'), classroom=classroom)
        set_student_persona(request, student, STUDENT)
        return redirect('student_home')
    return render(request, 'reading/student_pick.html', {'classroom': classroom, 'students': classroom.students.all().order_by('name')})

def parent_login(request):
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

@persona_required(STUDENT)
def student_home(request):
    return render(request, 'reading/student_home.html', _home_context(get_persona(request).student))

@persona_required(PARENT)
def parent_home(request):
    return render(request, 'reading/parent_home.html', _home_context(get_persona(request).student))

@require_POST
def logout(request):
    if get_persona(request).kind in (STUDENT, PARENT): clear_persona(request)
    else: auth_logout(request)
    return redirect('login')
