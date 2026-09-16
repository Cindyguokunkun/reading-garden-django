from functools import wraps
from django.shortcuts import render
from .models import Classroom, Membership, Organization, ParentStudentLink, Student

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
    def __init__(self, kind, display_name='', student=None, user=None, organization=None, membership=None):
        self.kind = kind
        self.display_name = display_name
        self.student = student
        self.user = user
        self.organization = organization
        self.membership = membership
    @property
    def is_staff(self): return self.kind in (TEACHER, MANAGER)
    @property
    def is_manager(self): return self.kind == MANAGER
    @property
    def is_platform_admin(self): return bool(self.user and self.user.is_superuser)
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
        if role == PARENT:
            links = ParentStudentLink.objects.filter(parent=request.user).select_related('student', 'student__classroom')
            selected = request.session.get('parent_student_id')
            link = links.filter(student_id=selected).first() or links.first()
            student = link.student if link else None
            persona = Persona(PARENT, request.user.get_full_name() or request.user.username,
                              student=student, user=request.user,
                              organization=student.classroom.organization if student else None)
        else:
            memberships = Membership.objects.filter(
                user=request.user, active=True, organization__active=True
            ).select_related('organization')
            selected = request.session.get('organization_id')
            membership = memberships.filter(organization_id=selected).first() or memberships.first()
            if not membership:
                organization = Organization.objects.filter(active=True).first()
                if organization:
                    membership, _ = Membership.objects.get_or_create(
                        user=request.user, organization=organization,
                        defaults={'role': role, 'active': True},
                    )
            if membership:
                request.session['organization_id'] = membership.organization_id
                persona = Persona(membership.role, request.user.get_full_name() or request.user.username,
                                  user=request.user, organization=membership.organization,
                                  membership=membership)
            else:
                persona = Persona(ANONYMOUS)
    elif kind in (STUDENT, PARENT):
        student = Student.objects.filter(pk=request.session.get('persona_student_id'), active=True).select_related('classroom', 'classroom__owner').first()
        if student:
            persona = Persona(kind, student.name_en or student.name, student=student,
                              organization=student.classroom.organization)
        else:
            clear_persona(request)
            persona = Persona(ANONYMOUS)
    else:
        persona = Persona(ANONYMOUS)
    request.persona = persona
    return persona

def persona_processor(request):
    persona = get_persona(request)
    organizations = (Organization.objects.filter(
        memberships__user=request.user, memberships__active=True, active=True
    ).distinct() if request.user.is_authenticated and persona.is_staff else Organization.objects.none())
    return {'persona': persona, 'organizations': organizations,
            'student_english': getattr(request, 'student_english', False)}

def current_classroom(request):
    persona = get_persona(request)
    if persona.student: return persona.student.classroom
    if persona.kind == MANAGER: qs = Classroom.objects.filter(organization=persona.organization)
    elif persona.kind == TEACHER: qs = Classroom.objects.filter(owner=request.user, organization=persona.organization)
    else: return None
    pk = request.GET.get('class') or request.POST.get('class')
    return qs.filter(pk=pk).first() or qs.first()

def accessible_classrooms(request):
    persona = get_persona(request)
    if persona.kind == MANAGER: return Classroom.objects.filter(organization=persona.organization)
    if persona.kind == TEACHER: return Classroom.objects.filter(owner=request.user, organization=persona.organization)
    if persona.student: return Classroom.objects.filter(pk=persona.student.classroom_id)
    return Classroom.objects.none()

def set_student_persona(request, student, kind):
    request.session['persona_kind'] = kind
    request.session['persona_student_id'] = student.pk

def clear_persona(request):
    for key in ('persona_kind', 'persona_student_id', 'parent_student_id'): request.session.pop(key, None)

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
