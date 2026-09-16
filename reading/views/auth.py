"""认证、会话身份（persona）与书架相关的视图。

本模块负责三类访问者的登录与登出：

- 员工（教师/管理员）：走 Django 内置认证体系，见 :data:`staff_login`。
- 学生：以「选班级 + 选学生」的方式进入，无需密码。
- 家长：以邮箱 + 密码登录，查看对应学生的阅读情况。

登录后由 :mod:`reading.personas` 在会话中记录「当前身份」，
后续视图据此判定权限与数据可见范围。模块同时提供书架（收藏）
的展示与增删。
"""

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

# 员工登录视图：复用 Django 的 LoginView，指定专用模板，
# 已登录用户再次访问时自动跳转，避免重复登录。
staff_login = LoginView.as_view(template_name='reading/login_staff.html', redirect_authenticated_user=True)


def login_hub(request):
    """登录入口分流页，根据当前身份跳转到对应首页。

    已具备身份（学生/家长/员工）的访问者会被直接送往各自首页，
    只有尚未登录者才会看到统一的登录选择页。

    Args:
        request (HttpRequest): 当前请求对象。

    Returns:
        HttpResponse: 重定向响应，或渲染 ``registration/login.html`` 的响应。
    """
    persona = get_persona(request)
    if persona.kind == STUDENT: return redirect('student_home')
    if persona.kind == PARENT: return redirect('parent_home')
    if persona.is_staff: return redirect('dashboard')
    return render(request, 'registration/login.html')


def student_login(request):
    """学生登录页：展示全部班级供学生选择。

    已是学生身份者直接跳转到学生首页。

    Args:
        request (HttpRequest): 当前请求对象。

    Returns:
        HttpResponse: 重定向响应，或渲染 ``reading/student_login.html``
        （携带按年级、名称排序的班级列表）的响应。
    """
    if request.user.is_authenticated: return redirect('dashboard')
    if get_persona(request).kind == STUDENT: return redirect('student_home')
    classrooms = Classroom.objects.all().order_by('grade', 'name')
    return render(request, 'reading/student_login.html', {'classrooms': classrooms})


def student_pick(request, classroom_id):
    """学生在指定班级内选择自己的名字以完成登录。

    GET 请求展示该班级学生名单；POST 请求校验所选学生属于该班级后，
    在会话中写入学生身份并跳转学生首页。

    Args:
        request (HttpRequest): 当前请求对象，POST 时需含 ``student`` 字段。
        classroom_id (int): 目标班级主键。

    Returns:
        HttpResponse: 重定向响应，或渲染 ``reading/student_pick.html`` 的响应。

    Raises:
        Http404: 当班级或所选学生不存在（或学生不属于该班级）时。
    """
    if request.user.is_authenticated: return redirect('dashboard')
    classroom = get_object_or_404(Classroom, pk=classroom_id)
    if request.method == 'POST':
        student = get_object_or_404(Student, pk=request.POST.get('student'), classroom=classroom)
        set_student_persona(request, student, STUDENT)
        return redirect('student_home')
    return render(request, 'reading/student_pick.html', {'classroom': classroom, 'students': classroom.students.all().order_by('name')})


def parent_login(request):
    """家长登录页：以邮箱 + 密码验证并建立家长身份。

    已是家长身份者直接跳转家长首页。POST 时按邮箱（不区分大小写）
    查找学生并校验密码，成功后写入家长身份；失败则回显错误提示。

    Args:
        request (HttpRequest): 当前请求对象，POST 时需含 ``email``、``password``。

    Returns:
        HttpResponse: 重定向响应，或渲染 ``reading/parent_login.html``
        （可能携带 ``error`` 文案）的响应。
    """
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
    """构建学生/家长首页所需的公共上下文数据。

    汇总学生的通过记录、个人阅读总量、班级目标完成度等信息，
    供学生首页与家长首页复用。

    Args:
        student (Student): 目标学生实例。

    Returns:
        dict: 包含 ``student``、``classroom``、``records``（最近 50 条通过记录）、
        ``totals``（个人字/分钟/本数合计）、``goal``、``class_words``
        （全班总字数）与 ``goal_percent``（目标完成百分比，封顶 100）等键的字典。
    """
    records = student.records.filter(passed=True)
    totals = rank_rows(Classroom.objects.filter(pk=student.classroom_id), date.min, date.max)
    total = next((r for r in totals if r['student_id'] == student.pk), {'words': 0, 'minutes': 0, 'books': 0})
    goal = getattr(student.classroom, 'goal', None)
    class_words = sum(r['words'] for r in totals)
    goal_percent = min(100, round(class_words * 100 / goal.words)) if goal and goal.words else 0
    return {'student': student, 'classroom': student.classroom, 'records': records.select_related('book')[:50],
            'totals': total, 'goal': goal, 'class_words': class_words, 'goal_percent': goal_percent}


def _attempt_rows(student):
    """查询学生最近的测验作答记录，并标注每本书的剩余可考次数。

    Args:
        student (Student): 目标学生实例。

    Returns:
        list[QuizAttempt]: 最近 30 条已提交的测验记录（含书籍信息）。
        每条记录会附加一个 ``remaining`` 属性，表示对应书籍剩余的
        可提交次数（:data:`MAX_SUBMITTED_ATTEMPTS` 减去失败次数，最小为 0）。
    """
    rows = list(QuizAttempt.objects.filter(student=student, submitted=True)
        .select_related('book').order_by('-completed_at')[:30])
    failed = {row['book_id']: row['n'] for row in QuizAttempt.objects
        .filter(student=student, submitted=True, passed=False).values('book_id').annotate(n=Count('id'))}
    for attempt in rows:
        attempt.remaining = max(MAX_SUBMITTED_ATTEMPTS - failed.get(attempt.book_id, 0), 0)
    return rows


@persona_required(STUDENT)
def student_home(request):
    """学生首页视图，展示个人阅读概览。

    Args:
        request (HttpRequest): 当前请求对象，需为学生身份。

    Returns:
        HttpResponse: 渲染 ``reading/student_home.html`` 的响应。
    """
    return render(request, 'reading/student_home.html', _home_context(get_persona(request).student))


@persona_required(PARENT)
def parent_home(request):
    """家长首页视图，在阅读概览之外额外展示测验记录。

    Args:
        request (HttpRequest): 当前请求对象，需为家长身份。

    Returns:
        HttpResponse: 渲染 ``reading/parent_home.html`` 的响应，
        上下文在学生首页基础上追加 ``attempts``（测验作答列表）。
    """
    student = get_persona(request).student
    return render(request, 'reading/parent_home.html', {**_home_context(student), 'attempts': _attempt_rows(student)})


@require_POST
def logout(request):
    """登出视图，按身份类型清除对应会话状态。

    学生/家长身份清除 persona 会话；员工身份则调用 Django 内置登出。
    仅接受 POST 请求。

    Args:
        request (HttpRequest): 当前请求对象。

    Returns:
        HttpResponse: 重定向到登录入口 ``login`` 的响应。
    """
    if request.user.is_authenticated: auth_logout(request)
    else: clear_persona(request)
    return redirect('login')


def shelf_state(student, books):
    """为一批书籍标注书架收藏状态与历史最佳通过分数。

    仅用两次聚合查询即完成标注，避免逐本书查库造成的 N+1 问题。
    标注结果以动态属性形式写回每个 ``book`` 对象。

    Args:
        student (Student): 目标学生实例。
        books (Iterable[Book]): 需要标注的书籍集合。

    Returns:
        set[int]: 该学生已收藏的书籍主键集合。

    Note:
        会为每个 ``book`` 附加两个属性：``in_shelf``（bool，是否在书架上）
        与 ``shelf_best``（该生通过测验的最高分，无记录时为空字符串）。
    """
    passed = {row['book_id']: row['best'] for row in QuizAttempt.objects
        .filter(student=student, passed=True, submitted=True).values('book_id').annotate(best=Max('score'))}
    on_shelf = set(ShelfItem.objects.filter(student=student).values_list('book_id', flat=True))
    for book in books:
        book.in_shelf = book.pk in on_shelf
        book.shelf_best = passed.get(book.pk, '')
    return on_shelf


@persona_required(STUDENT, PARENT)
def shelf(request):
    """书架页视图，展示当前学生收藏的书籍。

    Args:
        request (HttpRequest): 当前请求对象，需为学生或家长身份。

    Returns:
        HttpResponse: 渲染 ``reading/shelf.html`` 的响应，
        携带收藏项列表（最多 200 条）、数量与学生信息。
    """
    student = get_persona(request).student
    items = list(student.shelf.select_related('book')[:200])
    shelf_state(student, [item.book for item in items])
    return render(request, 'reading/shelf.html', {'items': items, 'count': len(items), 'student': student})


@persona_required(STUDENT, PARENT)
@require_POST
def shelf_change(request):
    """书架收藏增删视图，仅接受 POST。

    根据 ``remove`` 字段决定移除或新增收藏（新增采用 get_or_create 去重），
    完成后按 ``next`` 字段返回来源页面，避免打断用户浏览。

    Args:
        request (HttpRequest): 当前请求对象，需为学生或家长身份。
            POST 数据可含 ``book``（书籍主键）、``remove``（移除标记）、
            ``next``（返回目标：``library`` / ``book_detail`` / 默认 ``shelf``）。

    Returns:
        HttpResponse: 重定向回来源页面的响应。

    Raises:
        Http404: 当指定书籍不存在时。
    """
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
