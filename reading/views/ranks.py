"""排行榜视图。

本模块提供跨班级/年级/全校三个维度的阅读排行，并按字数、时长、
本数三种口径分别排序，供教师、管理员、学生与家长查看。
统计周期的计算复用 :mod:`reading.stats` 中的工具函数。
"""

from datetime import date
from django.shortcuts import render
from ..models import Classroom
from ..personas import TEACHER, MANAGER, STUDENT, PARENT, accessible_classrooms, current_classroom, get_persona, persona_required
from ..stats import period, rank_rows, sort_rows


@persona_required(TEACHER, MANAGER, STUDENT, PARENT)
def ranks(request):
    """排行榜视图，按维度（tier）与周期（mode）汇总阅读排名。

    根据 GET 参数决定统计范围：

    - ``school``: 全校所有班级。
    - ``grade``: 指定年级（缺省或非法时回退为当前班级年级）。
    - ``class``（默认）: 当前用户可访问的班级，可用 ``class`` 参数进一步限定。

    排名结果会标记出当前学生本人所在行，便于高亮显示。

    Args:
        request (HttpRequest): 当前请求对象。支持的 GET 参数：
            ``tier``（维度）、``mode``（周期模式，默认 ``'week'``）、
            ``date``（周期锚点，ISO 格式，非法时回退为今天）、
            ``grade``（年级）、``class``（班级主键）。

    Returns:
        HttpResponse: 渲染 ``reading/ranks.html`` 的响应，携带维度、周期、
        年级列表、可访问班级、字数/时长/本数三套排行，以及是否展示
        班级列（非 class 维度时展示）等上下文。
    """
    persona = get_persona(request)
    tier = request.GET.get('tier', 'class')
    mode = request.GET.get('mode', 'week')
    try: anchor = date.fromisoformat(request.GET.get('date', ''))
    except ValueError: anchor = date.today()
    start, end = period(mode, anchor)
    home = current_classroom(request)
    if tier == 'school':
        classrooms = Classroom.objects.all(); grade = None
    elif tier == 'grade':
        try: grade = int(request.GET.get('grade'))
        except (TypeError, ValueError): grade = home.grade if home else None
        classrooms = Classroom.objects.filter(grade=grade) if grade else Classroom.objects.none()
    else:
        tier = 'class'
        pk = request.GET.get('class')
        accessible = accessible_classrooms(request)
        classrooms = accessible.filter(pk=pk) if pk else accessible
        grade = home.grade if home else None
    rows = rank_rows(classrooms, start, end)
    for row in rows:
        row['me'] = bool(persona.student and row['student_id'] == persona.student.pk)
    grades = sorted(Classroom.objects.values_list('grade', flat=True).distinct())
    return render(request, 'reading/ranks.html', {
        'tier': tier, 'mode': mode, 'anchor': anchor, 'start': start, 'end': end, 'grade': grade, 'grades': grades,
        'classes': accessible_classrooms(request), 'classroom': home,
        'word_rankings': sort_rows(rows, 'words'), 'time_rankings': sort_rows(rows, 'minutes'), 'book_rankings': sort_rows(rows, 'books'),
        'show_classroom': tier != 'class',
    })
