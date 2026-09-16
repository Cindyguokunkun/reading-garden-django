import secrets
import string

from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import (
    ParentStudentLink, Profile, Student, TeacherInvite,
    ROLE_MANAGER, ROLE_PARENT, ROLE_TEACHER,
)
from ..personas import MANAGER, TEACHER, accessible_classrooms, get_persona, persona_required


def _code(length=8):
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _unique_code(model, field, length=8):
    while True:
        value = _code(length)
        if not model.objects.filter(**{field: value}).exists():
            return value


def register_hub(request):
    if get_persona(request):
        return redirect('login')
    return render(request, 'reading/register_hub.html')


def teacher_register(request):
    if get_persona(request):
        return redirect('login')
    errors = []
    values = request.POST if request.method == 'POST' else {}
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        full_name = (request.POST.get('full_name') or '').strip()
        email = (request.POST.get('email') or '').strip().lower()
        invite_code = (request.POST.get('invite_code') or '').strip().upper()
        password = request.POST.get('password') or ''
        confirm = request.POST.get('confirm') or ''
        if not username or not full_name or not email or not invite_code:
            errors.append('请完整填写所有必填项。')
        if User.objects.filter(username__iexact=username).exists():
            errors.append('这个用户名已被使用。')
        if User.objects.filter(email__iexact=email).exists():
            errors.append('这个邮箱已被使用。')
        if len(password) < 8:
            errors.append('密码至少需要 8 位。')
        if password != confirm:
            errors.append('两次输入的密码不一致。')
        invite = TeacherInvite.objects.filter(code__iexact=invite_code, active=True).first()
        if not invite or not invite.available:
            errors.append('教师邀请码无效或已用完。')
        if not errors:
            with transaction.atomic():
                invite = TeacherInvite.objects.select_for_update().get(pk=invite.pk)
                if not invite.available:
                    errors.append('教师邀请码已用完，请联系管理者。')
                else:
                    user = User.objects.create_user(username=username, email=email, password=password,
                                                    first_name=full_name, is_active=False)
                    Profile.objects.create(user=user, role=ROLE_TEACHER, approved=False)
                    invite.uses += 1
                    invite.save(update_fields=['uses'])
            if not errors:
                return render(request, 'reading/register_done.html', {'kind': 'teacher'})
    return render(request, 'reading/teacher_register.html', {'errors': errors, 'values': values})


def parent_register(request):
    if get_persona(request):
        return redirect('login')
    errors = []
    values = request.POST if request.method == 'POST' else {}
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        full_name = (request.POST.get('full_name') or '').strip()
        email = (request.POST.get('email') or '').strip().lower()
        bind_code = (request.POST.get('bind_code') or '').strip().upper()
        password = request.POST.get('password') or ''
        confirm = request.POST.get('confirm') or ''
        student = Student.objects.filter(bind_code__iexact=bind_code).first() if bind_code else None
        if not username or not full_name or not email:
            errors.append('请完整填写所有必填项。')
        if User.objects.filter(username__iexact=username).exists() or User.objects.filter(email__iexact=email).exists():
            errors.append('用户名或邮箱已被使用。')
        if len(password) < 8:
            errors.append('密码至少需要 8 位。')
        if password != confirm:
            errors.append('两次输入的密码不一致。')
        if bind_code and not student:
            errors.append('孩子绑定码不正确。')
        if not errors:
            user = User.objects.create_user(username=username, email=email, password=password,
                                            first_name=full_name)
            Profile.objects.create(user=user, role=ROLE_PARENT, approved=True)
            if student:
                ParentStudentLink.objects.create(parent=user, student=student)
            login(request, user)
            if student:
                request.session['parent_student_id'] = student.pk
            return redirect('parent_home')
    return render(request, 'reading/parent_register.html', {'errors': errors, 'values': values})


@persona_required(MANAGER)
def account_approvals(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'invite':
            code = _unique_code(TeacherInvite, 'code', 10)
            TeacherInvite.objects.create(
                code=code, label=(request.POST.get('label') or '').strip(),
                max_uses=max(1, int(request.POST.get('max_uses') or 20)), created_by=request.user,
            )
            messages.success(request, f'教师邀请码已创建：{code}')
        elif action == 'approve':
            profile = get_object_or_404(Profile, pk=request.POST.get('profile'), role=ROLE_TEACHER)
            profile.approved = True
            profile.save(update_fields=['approved'])
            profile.user.is_active = True
            profile.user.save(update_fields=['is_active'])
            messages.success(request, f'已批准教师 {profile.user.get_full_name() or profile.user.username}')
        elif action == 'reject':
            profile = get_object_or_404(Profile, pk=request.POST.get('profile'), role=ROLE_TEACHER, approved=False)
            profile.user.delete()
            messages.success(request, '申请已删除。')
        return redirect('account_approvals')
    pending = Profile.objects.filter(role=ROLE_TEACHER, approved=False).select_related('user')
    invites = TeacherInvite.objects.order_by('-created_at')[:30]
    return render(request, 'reading/account_approvals.html', {'pending': pending, 'invites': invites})


@persona_required(TEACHER, MANAGER)
def student_accounts(request):
    classes = accessible_classrooms(request)
    students = Student.objects.filter(classroom__in=classes).select_related('classroom').order_by('classroom__name', 'name')
    return render(request, 'reading/student_accounts.html', {'students': students})


@persona_required(TEACHER, MANAGER)
@require_POST
def student_account_action(request, student_id):
    student = get_object_or_404(Student, pk=student_id, classroom__in=accessible_classrooms(request))
    action = request.POST.get('action')
    if not student.login_id:
        student.login_id = f'S{student.pk:05d}'
    if not student.bind_code or action == 'bind':
        student.bind_code = _unique_code(Student, 'bind_code')
    if action == 'password':
        pin = ''.join(secrets.choice(string.digits) for _ in range(6))
        student.set_password(pin)
        messages.success(request, f'{student.name} 的新初始密码：{pin}（请现在记下）')
    student.save()
    return redirect('student_accounts')


@persona_required(ROLE_PARENT)
def parent_bind(request):
    error = None
    if request.method == 'POST':
        code = (request.POST.get('bind_code') or '').strip().upper()
        student = Student.objects.filter(bind_code__iexact=code).first()
        if not student:
            error = '绑定码不正确。'
        else:
            ParentStudentLink.objects.get_or_create(parent=request.user, student=student)
            request.session['parent_student_id'] = student.pk
            return redirect('parent_home')
    return render(request, 'reading/parent_bind.html', {'error': error})


@persona_required(ROLE_PARENT)
@require_POST
def parent_switch(request):
    link = get_object_or_404(ParentStudentLink, parent=request.user, student_id=request.POST.get('student'))
    request.session['parent_student_id'] = link.student_id
    return redirect('parent_home')
