import secrets

from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import (
    AccountAudit, Membership, Organization, ParentStudentLink, Profile, Student, TeacherInvite,
    ROLE_MANAGER, ROLE_PARENT, ROLE_TEACHER,
)
from ..accounts import DEFAULT_PASSWORD, full_english_name, unique_student_login
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
                    Membership.objects.create(
                        user=user, organization=invite.organization,
                        role=ROLE_TEACHER, active=False,
                    )
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
    organization = get_persona(request).organization
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'invite':
            code = _unique_code(TeacherInvite, 'code', 10)
            TeacherInvite.objects.create(
                code=code, label=(request.POST.get('label') or '').strip(),
                max_uses=max(1, int(request.POST.get('max_uses') or 20)), created_by=request.user,
                organization=organization,
            )
            AccountAudit.objects.create(actor=request.user, organization=organization,
                                        action='create_teacher_invite', detail=code)
            messages.success(request, f'教师邀请码已创建：{code}')
        elif action == 'approve':
            membership = get_object_or_404(
                Membership.objects.select_related('user'), pk=request.POST.get('profile'),
                organization=organization, role=ROLE_TEACHER, active=False,
            )
            profile = membership.user.profile
            profile.approved = True
            profile.save(update_fields=['approved'])
            profile.user.is_active = True
            profile.user.save(update_fields=['is_active'])
            membership.active = True
            membership.save(update_fields=['active'])
            AccountAudit.objects.create(actor=request.user, target=profile.user,
                                        organization=organization, action='approve_teacher')
            messages.success(request, f'已批准教师 {profile.user.get_full_name() or profile.user.username}')
        elif action == 'reject':
            membership = get_object_or_404(
                Membership.objects.select_related('user'), pk=request.POST.get('profile'),
                organization=organization, role=ROLE_TEACHER, active=False,
            )
            target = membership.user
            AccountAudit.objects.create(actor=request.user, target=target,
                                        organization=organization, action='reject_teacher')
            target.delete()
            messages.success(request, '申请已删除。')
        elif action in ('promote', 'demote', 'toggle_active'):
            membership = get_object_or_404(
                Membership.objects.select_related('user'), pk=request.POST.get('profile'),
                organization=organization, role__in=(ROLE_TEACHER, ROLE_MANAGER), active=True,
            )
            target = membership.user
            if not request.user.check_password(request.POST.get('current_password') or ''):
                messages.error(request, '当前管理者密码不正确，操作未执行。')
                return redirect('account_approvals')
            if target == request.user and action in ('demote', 'toggle_active'):
                messages.error(request, '不能停用自己或取消自己的管理者权限。')
                return redirect('account_approvals')
            if action == 'promote':
                membership.role = ROLE_MANAGER
                membership.save(update_fields=['role'])
                label = 'promote_manager'
                messages.success(request, f'{target.get_full_name() or target.username} 已成为管理者。')
            elif action == 'demote':
                active_managers = Membership.objects.filter(
                    organization=organization, role=ROLE_MANAGER, active=True, user__is_active=True
                ).count()
                if active_managers <= 1:
                    messages.error(request, '系统必须至少保留一名可用管理者。')
                    return redirect('account_approvals')
                membership.role = ROLE_TEACHER
                membership.save(update_fields=['role'])
                label = 'demote_manager'
                messages.success(request, f'{target.get_full_name() or target.username} 已改为教师。')
            else:
                if membership.role == ROLE_MANAGER and target.is_active:
                    active_managers = Membership.objects.filter(
                        organization=organization, role=ROLE_MANAGER, active=True, user__is_active=True
                    ).count()
                    if active_managers <= 1:
                        messages.error(request, '不能停用最后一名可用管理者。')
                        return redirect('account_approvals')
                target.is_active = not target.is_active
                target.save(update_fields=['is_active'])
                label = 'activate_account' if target.is_active else 'deactivate_account'
                messages.success(request, '账号已恢复。' if target.is_active else '账号已停用。')
            AccountAudit.objects.create(actor=request.user, target=target,
                                        organization=organization, action=label)
        return redirect('account_approvals')
    pending = Membership.objects.filter(
        organization=organization, role=ROLE_TEACHER, active=False
    ).select_related('user')
    staff = Membership.objects.filter(
        organization=organization, role__in=(ROLE_TEACHER, ROLE_MANAGER), active=True
    ).select_related('user').order_by('role', 'user__username')
    invites = TeacherInvite.objects.filter(organization=organization).order_by('-created_at')[:30]
    audits = AccountAudit.objects.filter(organization=organization).select_related('actor', 'target')[:30]
    return render(request, 'reading/account_approvals.html', {
        'pending': pending, 'staff': staff, 'invites': invites, 'audits': audits,
    })


@persona_required(TEACHER, MANAGER)
def student_accounts(request):
    classes = accessible_classrooms(request)
    if request.method == 'POST':
        classroom = get_object_or_404(classes, pk=request.POST.get('classroom'))
        name = (request.POST.get('name') or '').strip()
        name_en = full_english_name(name, request.POST.get('name_en'))
        if not name or not name_en:
            messages.error(request, '请填写学生中文名和英文名。')
        else:
            student = Student(classroom=classroom, name=name, name_en=name_en,
                              login_id=unique_student_login(name_en), bind_code=_unique_code(Student, 'bind_code'))
            student.set_password(DEFAULT_PASSWORD)
            student.set_parent_password(DEFAULT_PASSWORD)
            student.save()
            messages.success(request, f'已创建 {name}，用户名：{student.login_id}，初始密码：{DEFAULT_PASSWORD}')
            return redirect('student_accounts')
    students = Student.objects.filter(classroom__in=classes, active=True).select_related('classroom').order_by('classroom__name', 'name')
    archived = Student.objects.filter(classroom__in=classes, active=False).select_related('classroom').order_by('classroom__name', 'name')
    return render(request, 'reading/student_accounts.html', {'students': students, 'archived': archived, 'classes': classes, 'default_password': DEFAULT_PASSWORD})


@persona_required(TEACHER, MANAGER)
@require_POST
def student_account_action(request, student_id):
    student = get_object_or_404(Student, pk=student_id, classroom__in=accessible_classrooms(request))
    action = request.POST.get('action')
    if not student.login_id:
        student.login_id = unique_student_login(student.name_en or student.name, student)
    if not student.bind_code or action == 'bind':
        student.bind_code = _unique_code(Student, 'bind_code')
    if action == 'password':
        student.set_password(DEFAULT_PASSWORD)
        messages.success(request, f'{student.name} 的密码已重置为 {DEFAULT_PASSWORD}')
    elif action == 'edit':
        name = (request.POST.get('name') or '').strip()
        duplicate = Student.objects.filter(classroom=student.classroom, active=True, name=name).exclude(pk=student.pk).exists()
        if duplicate:
            messages.error(request, '同一班级已经有这个姓名，请添加英文名或其他标识。')
        elif name:
            student.name = name
            name_en = full_english_name(name, request.POST.get('name_en'))
            if name_en and name_en != student.name_en:
                student.name_en = name_en
                student.login_id = unique_student_login(name_en, student)
            target_class = accessible_classrooms(request).filter(pk=request.POST.get('classroom')).first()
            if target_class:
                student.classroom = target_class
            messages.success(request, '学生姓名已更新。')
    elif action == 'archive':
        student.active = False
        messages.success(request, f'{student.name} 已停用，历史阅读记录仍然保留。')
    elif action == 'restore':
        student.active = True
        messages.success(request, f'{student.name} 已恢复。')
    elif action == 'delete':
        name = student.name
        student.delete()
        messages.success(request, f'{name} 已永久删除，相关阅读、测验和书架数据已一并清除。')
        return redirect('student_accounts')
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


@persona_required(TEACHER, MANAGER)
@require_POST
def organization_switch(request):
    membership = get_object_or_404(
        Membership, user=request.user, organization_id=request.POST.get('organization'),
        active=True, organization__active=True,
    )
    request.session['organization_id'] = membership.organization_id
    if hasattr(request, 'persona'):
        del request.persona
    return redirect('dashboard')


@persona_required(MANAGER)
def organization_manage(request):
    persona = get_persona(request)
    if not persona.is_platform_admin:
        return render(request, 'reading/forbidden.html', status=403)
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        slug = (request.POST.get('slug') or '').strip().lower()
        if name and slug and not Organization.objects.filter(slug=slug).exists():
            organization = Organization.objects.create(name=name, slug=slug)
            Membership.objects.create(
                user=request.user, organization=organization, role=ROLE_MANAGER, active=True
            )
            messages.success(request, f'学校已创建：{name}')
            return redirect('organization_manage')
        messages.error(request, '请填写学校名称和唯一英文标识。')
    return render(request, 'reading/organization_manage.html', {
        'all_organizations': Organization.objects.order_by('name'),
    })
