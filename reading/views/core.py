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
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from ..models import grade_choices, Book, ClassGoal, Classroom, ReadingRecord, Student
from ..personas import accessible_classrooms, current_classroom
from ..stats import period, rank_rows, sort_rows


@login_required
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
    classroom = current_classroom(request)
    mode = request.GET.get('mode', 'week')
    try: anchor = date.fromisoformat(request.GET.get('date', ''))
    except ValueError: anchor = date.today()
    start, end = period(mode, anchor)
    students = classroom.students.all() if classroom else Student.objects.none()
    records = ReadingRecord.objects.filter(student__classroom=classroom, passed=True).select_related('student', 'book') if classroom else ReadingRecord.objects.none()
    rows = rank_rows(Classroom.objects.filter(pk=classroom.pk) if classroom else Classroom.objects.none(), start, end)
    word_rankings = sort_rows(rows, 'words')
    time_rankings = sort_rows(rows, 'minutes')
    series = {}
    for book in Book.objects.all().order_by('series', 'title'): series.setdefault(book.series, []).append(book)
    total_words = records.aggregate(v=Sum('words'))['v'] or 0
    goal = getattr(classroom, 'goal', None) if classroom else None
    goal_percent = min(100, round(total_words * 100 / goal.words)) if goal and goal.words else 0
    return render(request, 'reading/dashboard.html', {'classes': accessible_classrooms(request), 'classroom': classroom, 'students': students, 'records': records[:100], 'series': series, 'word_rankings': word_rankings, 'time_rankings': time_rankings, 'mode': mode, 'anchor': anchor, 'start': start, 'end': end, 'total_words': total_words, 'total_minutes': records.aggregate(v=Sum('minutes'))['v'] or 0, 'goal': goal, 'goal_percent': goal_percent, 'grade_choices': grade_choices()})


@login_required
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

    Returns:
        HttpResponse: 重定向回仪表盘（带当前班级参数）的响应。

    Raises:
        Http404: ``record_add`` 时指定的学生（须属于当前班级）或书籍不存在。
    """
    kind = request.POST.get('action'); classroom = current_classroom(request)
    if kind == 'class_add':
        Classroom.objects.create(owner=request.user, name=request.POST['name'].strip(), grade=int(request.POST.get('grade') or 1))
    elif kind == 'student_add' and classroom:
        Student.objects.create(classroom=classroom, name=request.POST['name'].strip())
    elif kind == 'goal_set' and classroom:
        words = int(request.POST.get('words') or 0); deadline = request.POST.get('deadline') or None
        if words > 0: ClassGoal.objects.update_or_create(classroom=classroom, defaults={'words': words, 'deadline': deadline})
    elif kind == 'record_add' and classroom:
        student = get_object_or_404(Student, pk=request.POST['student'], classroom=classroom); book = get_object_or_404(Book, pk=request.POST['book'])
        ReadingRecord.objects.create(student=student, book=book, read_date=request.POST['date'], words=book.words or int(request.POST.get('words') or 0), minutes=int(request.POST['minutes']) if request.POST.get('minutes') else None, passed=True)
    messages.success(request, _('Saved'))
    return redirect(f'/?class={classroom.pk}' if classroom else '/')


@login_required
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
    for r in ReadingRecord.objects.filter(student__classroom=classroom).select_related('student', 'book'): ws.append([r.student.name, r.read_date, r.book.series, r.book.title, r.words, r.minutes, r.quiz_score])
    out = BytesIO(); wb.save(out); response = HttpResponse(out.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'); response['Content-Disposition'] = 'attachment; filename="reading-records.xlsx"'; return response
