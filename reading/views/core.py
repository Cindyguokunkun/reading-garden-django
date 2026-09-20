"""员工后台核心视图：仪表盘、数据录入与 Excel 导出。

本模块面向已登录的员工用户（教师/管理员），提供：

- :func:`dashboard`: 班级阅读数据总览与排行榜。
- :func:`action`: 统一的 POST 入口，按 ``action`` 字段分派新增班级、
  新增学生、设置目标、录入阅读记录等操作。
- :func:`export_excel`: 将当前班级的阅读记录导出为 .xlsx 文件。
"""

from datetime import date
from io import BytesIO
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from ..accounts import DEFAULT_PASSWORD, full_english_name, unique_student_login
from ..models import grade_choices, Book, ClassGoal, Classroom, Membership, ReadingRecord, Student, ROLE_TEACHER
from ..personas import MANAGER, TEACHER, accessible_classrooms, current_classroom, get_persona, persona_required
from ..stats import period, rank_rows, sort_rows
from .registration import _unique_code


@persona_required(TEACHER, MANAGER)
def dashboard(request):
    """班级阅读仪表盘视图。

    汇总当前班级在所选统计周期内的阅读记录、字数/时长排行、
    系列书籍分组以及班级目标完成度。

    Args:
        request (HttpRequest): 当前请求对象，需已登录。
            支持的 GET 查询参数：

            - ``mode``: 统计周期模式（默认 ``'week'``）。
            - ``date``: 周期锚点日期（ISO 格式，非法时回退为今天）。

    Returns:
        HttpResponse: 渲染 ``reading/dashboard.html`` 的响应，
        携带可访问班级、当前班级、学生、阅读记录（最多 100 条）、
        系列分组、字数/时长排行、周期信息与目标完成度等上下文。
    """
    persona = get_persona(request)
    if persona.is_manager:
        organization = persona.organization
        classes = Classroom.objects.filter(organization=organization).select_related('owner').annotate(
            active_students=Count('students', filter=Q(students__active=True))
        ).order_by('grade', 'section', 'name')
        return render(request, 'reading/management_dashboard.html', {
            'class_count': classes.count(),
            'student_count': Student.objects.filter(classroom__organization=organization, active=True).count(),
            'teacher_count': Membership.objects.filter(organization=organization, role=ROLE_TEACHER, active=True, user__is_active=True).count(),
            'book_count': Book.objects.count(),
            'classes': classes,
        })
    classroom = current_classroom(request)
    mode = request.GET.get('mode', 'week')
    try: anchor = date.fromisoformat(request.GET.get('date', ''))
    except ValueError: anchor = date.today()
    start, end = period(mode, anchor)
    students = classroom.students.filter(active=True) if classroom else Student.objects.none()
    records = ReadingRecord.objects.filter(student__classroom=classroom, student__active=True, passed=True).select_related('student', 'book') if classroom else ReadingRecord.objects.none()
    rows = rank_rows(Classroom.objects.filter(pk=classroom.pk) if classroom else Classroom.objects.none(), start, end)
    word_rankings = sort_rows(rows, 'words')
    time_rankings = sort_rows(rows, 'minutes')
    series = {}
    for book in Book.objects.all().order_by('series', 'series_order', 'title'): series.setdefault(book.series, []).append(book)
    total_words = records.aggregate(v=Sum('words'))['v'] or 0
    goal = getattr(classroom, 'goal', None) if classroom else None
    goal_percent = min(100, round(total_words * 100 / goal.words)) if goal and goal.words else 0
    return render(request, 'reading/dashboard.html', {'classes': accessible_classrooms(request), 'classroom': classroom, 'students': students, 'records': records[:100], 'series': series, 'word_rankings': word_rankings, 'time_rankings': time_rankings, 'mode': mode, 'anchor': anchor, 'start': start, 'end': end, 'total_words': total_words, 'total_minutes': records.aggregate(v=Sum('minutes'))['v'] or 0, 'goal': goal, 'goal_percent': goal_percent, 'grade_choices': grade_choices()})


@persona_required(TEACHER, MANAGER)
@require_POST
def action(request):
    """后台数据录入的统一 POST 分派入口。

    根据 POST 的 ``action`` 字段执行相应写操作，全部成功后给出
    统一的「已保存」提示并重定向回仪表盘。

    Args:
        request (HttpRequest): 当前请求对象，需已登录且为 POST。
            ``action`` 取值及所需字段：

            - ``class_add``: 新增班级，需 ``name``、可选 ``grade``。
            - ``student_add``: 向当前班级新增学生，需 ``name``。
            - ``goal_set``: 设置/更新班级目标，需 ``words``、可选 ``deadline``。
            - ``record_add``: 录入阅读记录，需 ``student``、``book``、
              ``date``、``minutes``，可选 ``words``。
            - ``class_promote``: 将指定 ``class`` 升一个年级（原地升级，
              学生与阅读数据保持不变并继续累加）。
            - ``class_delete``: 永久删除指定 ``class`` 及其学生与全部阅读数据。
            - ``class_promote_all``: 仅管理者可用，将本校所有班级各升一个年级。

    Returns:
        HttpResponse: 重定向回仪表盘（带当前班级参数）的响应。

    Raises:
        Http404: ``record_add`` 时指定的学生（须属于当前班级）或书籍不存在。
    """
    kind = request.POST.get('action'); classroom = current_classroom(request)
    if kind == 'class_add':
        grade = int(request.POST.get('grade') or 1)
        section = int(request.POST.get('section') or 1)
        Classroom.objects.create(owner=request.user, organization=get_persona(request).organization,
                                 name=f'Y{grade}C{section}', grade=grade, section=section)
    elif kind == 'student_add' and classroom:
        name = request.POST['name'].strip()
        name_en = full_english_name(name, request.POST.get('name_en'))
        student = Student.objects.create(classroom=classroom, name=name, name_en=name_en,
                                         login_id=unique_student_login(name_en or name))
        student.bind_code = _unique_code(Student, 'bind_code')
        student.set_password(DEFAULT_PASSWORD)
        student.set_parent_password(DEFAULT_PASSWORD)
        student.save(update_fields=['login_id', 'bind_code', 'password_hash', 'parent_password_hash'])
        messages.success(request, _('%(name)s created. Student ID: %(login)s; initial password: %(pin)s. Please save it now.') % {
            'name': student.name, 'login': student.login_id, 'pin': DEFAULT_PASSWORD,
        })
    elif kind == 'goal_set' and classroom:
        words = int(request.POST.get('words') or 0); deadline = request.POST.get('deadline') or None
        if words > 0: ClassGoal.objects.update_or_create(classroom=classroom, defaults={'words': words, 'deadline': deadline})
    elif kind == 'record_add' and classroom:
        student = get_object_or_404(Student, pk=request.POST['student'], classroom=classroom); book = get_object_or_404(Book, pk=request.POST['book'])
        ReadingRecord.objects.create(student=student, book=book, read_date=request.POST['date'], words=book.words or int(request.POST.get('words') or 0), minutes=int(request.POST['minutes']) if request.POST.get('minutes') else None, passed=True)
    elif kind in ('class_promote', 'class_delete', 'class_promote_all'):
        return _class_management_action(request, kind)
    messages.success(request, _('Saved'))
    return redirect(f'/?class={classroom.pk}' if classroom else '/')


def _class_management_action(request, kind):
    """处理班级升级与删除（含全校一键升级）。

    升级采用「原地修改 :class:`~reading.models.Classroom` 的 ``grade``」策略：
    由于学生（:class:`~reading.models.Student`）及其阅读记录、测验、书架、
    目标等数据都外键到学生本人而非年级，升级不会新建任何记录，学生的累计
    阅读量会跟着他一直累加。班级的 ``name`` 在 ``save()`` 时按
    ``Y{grade}C{section}`` 自动重命名，因此「三年级三班」升级后即变为
    「四年级三班」（Y3C3 -> Y4C3）。

    目标班级必须落在 :func:`~reading.personas.accessible_classrooms` 范围内：
    教师仅限本人名下班级，管理者为本校全部班级；``class_promote_all`` 额外
    要求管理者身份。

    Args:
        request (HttpRequest): 已登录员工的 POST 请求，``class`` 指定目标班级主键。
        kind (str): ``class_promote``、``class_delete`` 或 ``class_promote_all``。

    Returns:
        HttpResponse: 携带成功/失败提示并重定向回相应仪表盘的响应；
            无权限执行全校升级时返回 403 页面。
    """
    persona = get_persona(request)
    accessible = accessible_classrooms(request)
    if kind == 'class_promote_all':
        if not persona.is_manager:
            return render(request, 'reading/forbidden.html', status=403)
        promotable = list(accessible.filter(grade__lt=12))
        with transaction.atomic():
            for target in promotable:
                target.grade += 1
                target.save()
        if promotable:
            messages.success(request, _('Promoted %(count)s classes to the next grade.') % {'count': len(promotable)})
        else:
            messages.info(request, _('No classes to promote; every class is already at Grade 12.'))
        return redirect('/')
    target = accessible.filter(pk=request.POST.get('class')).first()
    if not target:
        messages.error(request, _('Class not found, or you do not have permission to manage it.'))
        return redirect('/')
    if kind == 'class_promote':
        if target.grade >= 12:
            messages.error(request, _('Already at the top grade; cannot promote further.'))
        else:
            target.grade += 1
            target.save()
            messages.success(request, _('Class promoted to %(name)s (Grade %(grade)s).') % {'name': target.name, 'grade': target.grade})
        return redirect(f'/?class={target.pk}')
    name = target.name
    student_count = target.students.count()
    target.delete()
    messages.success(request, _('Deleted class %(name)s along with its %(count)s students and all of their reading data.') % {'name': name, 'count': student_count})
    return redirect('/')


@persona_required(TEACHER, MANAGER)
def export_excel(request):
    """将当前班级的阅读记录导出为 Excel（.xlsx）文件下载。

    Args:
        request (HttpRequest): 当前请求对象，需已登录。

    Returns:
        HttpResponse: 内容为 xlsx 二进制、附带 ``Content-Disposition``
        下载头（文件名 ``reading-records.xlsx``）的响应。表格首行为
        国际化表头，其后逐行写入每条阅读记录。
    """
    classroom = current_classroom(request); wb = Workbook(); ws = wb.active; ws.title = _('Reading records'); ws.append([_('Student'), _('Date'), _('Series'), _('Title'), _('Words'), _('Minutes'), _('Quiz score')])
    for r in ReadingRecord.objects.filter(student__classroom=classroom, student__active=True).select_related('student', 'book'): ws.append([r.student.name, r.read_date, r.book.series, r.book.title, r.words, r.minutes, r.quiz_score])
    out = BytesIO(); wb.save(out); response = HttpResponse(out.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'); response['Content-Disposition'] = 'attachment; filename="reading-records.xlsx"'; return response
