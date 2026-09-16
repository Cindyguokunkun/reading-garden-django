from functools import wraps
from django.shortcuts import render
from .models import Classroom, Student

TEACHER = 'teacher'
MANAGER = 'manager'
STUDENT = 'student'
PARENT = 'parent'
ANONYMOUS = 'anonymous'

def get_role(user):
    if not user.is_authenticated: return None
    if user.is_superuser: return MANAGER
    profile = getattr(user, 'profile', None)
    return profile.role if profile else TEACHER

class Persona:
    def __init__(self, kind, display_name='', student=None, user=None):
        self.kind = kind
        self.display_name = display_name
        self.student = student
        self.user = user
    @property
    def is_staff(self): return self.kind in (TEACHER, MANAGER)
    @property
    def is_manager(self): return self.kind == MANAGER
    @property
    def classroom(self): return self.student.classroom if self.student else None
    def __bool__(self): return self.kind != ANONYMOUS

def get_persona(request):
    persona = getattr(request, 'persona', None)
    if persona is not None: return persona
    kind = request.session.get('persona_kind')
    if request.user.is_authenticated:
        # A signed-in staff account outranks any student/parent keys left in the session.
        if kind: clear_persona(request)
        role = get_role(request.user)
        persona = Persona(role, request.user.get_full_name() or request.user.username, user=request.user)
    elif kind in (STUDENT, PARENT):
        student = Student.objects.filter(pk=request.session.get('persona_student_id')).select_related('classroom', 'classroom__owner').first()
        if student:
            persona = Persona(kind, student.name_en or student.name, student=student)
        else:
            clear_persona(request)
            persona = Persona(ANONYMOUS)
    else:
        persona = Persona(ANONYMOUS)
    request.persona = persona
    return persona

def persona_processor(request):
    return {'persona': get_persona(request)}

def current_classroom(request):
    persona = get_persona(request)
    if persona.student: return persona.student.classroom
    if persona.kind == MANAGER: qs = Classroom.objects.all()
    elif persona.kind == TEACHER: qs = Classroom.objects.filter(owner=request.user)
    else: return None
    pk = request.GET.get('class') or request.POST.get('class')
    return qs.filter(pk=pk).first() or qs.first()

def accessible_classrooms(request):
    persona = get_persona(request)
    if persona.kind == MANAGER: return Classroom.objects.all()
    if persona.kind == TEACHER: return Classroom.objects.filter(owner=request.user)
    if persona.student: return Classroom.objects.filter(pk=persona.student.classroom_id)
    return Classroom.objects.none()

def set_student_persona(request, student, kind):
    request.session['persona_kind'] = kind
    request.session['persona_student_id'] = student.pk

def clear_persona(request):
    for key in ('persona_kind', 'persona_student_id'): request.session.pop(key, None)

def persona_required(*kinds):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            persona = get_persona(request)
            if persona.kind == ANONYMOUS:
                from django.contrib.auth.views import redirect_to_login
                return redirect_to_login(request.get_full_path())
            if kinds and persona.kind not in kinds:
                return render(request, 'reading/forbidden.html', status=403)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator

def manager_required(view):
    return persona_required(MANAGER)(view)

def student_persona_required(view):
    return persona_required(STUDENT)(view)
