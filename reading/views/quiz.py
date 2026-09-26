"""测验（Quiz）相关视图：开始、作答与回顾。

本模块实现学生针对某本书的阅读理解测验全流程：

- :func:`quiz_start`: 选择学生与书籍、校验可考资格并创建测验实例。
- :func:`quiz_take`: 展示题目、判分、写入结果，并在通过时生成阅读记录。
- :func:`quiz_review`: 回看已提交测验的题目与答案。

每本书最多允许 :data:`MAX_SUBMITTED_ATTEMPTS` 次「已提交但未通过」的作答；
一旦通过则不可再考。题目快照随测验记录持久化保存，重考时会打乱题序与选项。
访问权限通过 :func:`_can_access_attempt` 按身份类型逐一判定。
"""

import random
from datetime import date
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from ..models import CATEGORY_CHOICES, Book, Classroom, QuizAttempt, ReadingRecord, Student
from ..personas import MANAGER, PARENT, STUDENT, TEACHER, accessible_classrooms, current_classroom, get_persona, persona_required

# 每本书允许的「已提交但未通过」的最大作答次数。
MAX_SUBMITTED_ATTEMPTS = 3


def _attempt_stats(student, book):
    """统计某学生对某本书的测验通过情况与失败次数。

    Args:
        student (Student): 目标学生。
        book (Book): 目标书籍。

    Returns:
        tuple[bool, int]: ``(passed, failed)``。``passed`` 表示是否存在
        已提交的通过记录；``failed`` 表示已提交但未通过的次数。
    """
    passed = QuizAttempt.objects.filter(student=student, book=book, passed=True, submitted=True).exists()
    failed = QuizAttempt.objects.filter(student=student, book=book, passed=False, submitted=True).count()
    return passed, failed


def _remaining(student, book):
    """计算某学生对某本书剩余的可提交作答次数。

    Args:
        student (Student): 目标学生。
        book (Book): 目标书籍。

    Returns:
        int: :data:`MAX_SUBMITTED_ATTEMPTS` 减去已失败次数，最小为 0。
    """
    failed = QuizAttempt.objects.filter(student=student, book=book, passed=False, submitted=True).count()
    return max(MAX_SUBMITTED_ATTEMPTS - failed, 0)


def _quizable_books(organization):
    """返回全部「有题目、可测验」的书籍。

    Returns:
        QuerySet[Book]: 排除 ``quiz_data`` 为空列表的书籍，
        按系列、书名排序。
    """
    return Book.objects.filter(
        Q(organization__isnull=True) | Q(organization=organization)
    ).exclude(quiz_data=[]).order_by('category', 'series', 'series_order', 'title')


def _quiz_catalog(organization):
    """Build category -> series -> books groups for the three-step picker."""
    labels = dict(CATEGORY_CHOICES)
    grouped = {}
    for book in _quizable_books(organization):
        category = book.category or 'uncategorized'
        grouped.setdefault(category, {}).setdefault(book.series or str(_('Standalone')), []).append(book)
    catalog = []
    order = [value for value, _label in CATEGORY_CHOICES] + ['uncategorized']
    for category in order:
        if category not in grouped:
            continue
        catalog.append({
            'value': category,
            'label': str(labels.get(category, _('Uncategorized'))),
            'series': [{'name': name, 'books': books} for name, books in grouped[category].items()],
        })
    return catalog


def _build_questions(book, retake):
    """根据书籍题库构建本次测验的题目快照，并打乱选项顺序。

    每题的正确答案索引会随选项打乱而重新计算，确保判分依据一致。

    Args:
        book (Book): 目标书籍，其 ``quiz_data`` 提供原始题目。
        retake (bool): 是否为重考。为 ``True`` 时额外打乱题目顺序，
            以降低凭记忆答题的可能。

    Returns:
        list[dict]: 题目列表，每题含 ``prompt``、``options`` 与
        重新定位后的 ``answer`` 索引。
    """
    questions = [dict(q) for q in book.quiz_data]
    if retake: random.shuffle(questions)
    for q in questions:
        correct = q['options'][q['answer']]; random.shuffle(q['options']); q['answer'] = q['options'].index(correct)
    return questions


def _recover_questions(attempt):
    """为题目快照缺失的未提交作答重建题目并写回记录。

    早期版本仅把题目缓存于会话，会话丢失（重新登录、更换浏览器、
    会话过期等）后「继续上次的测试」会打开 0 题的空答卷。此处按
    创建时相同的规则重建题目快照并持久化，保证继续答题始终有题。

    Args:
        attempt (QuizAttempt): 未提交且 ``questions`` 为空的作答记录。

    Returns:
        list[dict]: 重建后的题目列表，格式同 :func:`_build_questions`。
    """
    _, failed = _attempt_stats(attempt.student, attempt.book)
    questions = _build_questions(attempt.book, retake=failed > 0)
    attempt.questions = questions
    attempt.save(update_fields=['questions'])
    return questions


@persona_required(TEACHER, MANAGER, STUDENT)
def quiz_start(request):
    """测验入口视图：GET 展示选择表单，POST 创建测验实例并跳转作答。

    学生身份直接以本人应试；教师/管理员需指定当前班级内的学生。
    POST 时校验该书是否已通过、是否已用尽作答次数，通过后创建
    :class:`QuizAttempt`、把题目快照写入记录并在会话缓存阅读元信息，
    再跳转作答页。

    Args:
        request (HttpRequest): 当前请求对象。POST 需含 ``book``，
            员工身份还需 ``student``；可选 ``date``、``minutes``。
            GET 可选 ``book`` 用于预选。

    Returns:
        HttpResponse: 重定向到作答页，或渲染 ``reading/quiz_start.html``
        的响应。

    Raises:
        Http404: 指定的学生（须属于当前班级）或书籍不存在。
    """
    persona = get_persona(request)
    classroom = current_classroom(request)
    if request.method == 'POST':
        if persona.kind == STUDENT:
            student = persona.student
        else:
            student = get_object_or_404(Student, pk=request.POST['student'], classroom=classroom)
        book = get_object_or_404(_quizable_books(persona.organization), pk=request.POST['book'])
        passed, failed = _attempt_stats(student, book)
        back = 'student_home' if persona.kind == STUDENT else 'quiz_start'
        if passed:
            messages.error(request, _('This book has already been passed.'))
            return redirect(back)
        if failed >= MAX_SUBMITTED_ATTEMPTS:
            messages.error(request, _('All 3 quiz attempts for this book have been used.'))
            return redirect(back)
        attempt = QuizAttempt.objects.create(student=student, book=book, score=0, passed=False, answers=[],
            questions=_build_questions(book, retake=failed > 0), started_at=timezone.now())
        request.session[f'quiz_meta_{attempt.pk}'] = {'date': request.POST.get('date') or date.today().isoformat(), 'minutes': request.POST.get('minutes') or None}
        return redirect('quiz_take', attempt_id=attempt.pk)
    chosen = request.GET.get('book') or ''
    chosen = int(chosen) if chosen.isdigit() else None
    if persona.kind == STUDENT:
        return render(request, 'reading/quiz_start.html', {'classroom': classroom, 'students': [], 'catalog': _quiz_catalog(persona.organization), 'chosen': chosen})
    return render(request, 'reading/quiz_start.html', {'classroom': classroom, 'classes': accessible_classrooms(request), 'students': classroom.students.filter(active=True) if classroom else [], 'catalog': _quiz_catalog(persona.organization), 'chosen': chosen})


def _can_access_attempt(persona, attempt):
    """判定某身份是否有权访问指定测验记录。

    Args:
        persona: 当前访问者身份对象。
        attempt (QuizAttempt): 目标测验记录。

    Returns:
        bool: 访问规则如下——学生/家长仅能访问本人的记录；
        管理员可访问全部；教师仅能访问自己班级学生的记录；
        其余情况返回 ``False``。
    """
    if persona.kind in (STUDENT, PARENT): return attempt.student_id == persona.student.pk
    if persona.kind == MANAGER:
        return attempt.student.classroom.organization_id == persona.organization.pk
    if persona.kind == TEACHER:
        return (attempt.student.classroom.organization_id == persona.organization.pk
                and attempt.student.classroom.owner_id == persona.user.pk)
    return False


@persona_required(TEACHER, MANAGER, STUDENT)
def quiz_take(request, attempt_id):
    """测验作答视图：GET 展示题目，POST 判分并写入结果。

    POST 时按记录中的题目快照对学生答案判分，得分 ≥ 60 视为通过，
    更新测验记录；若本次通过且该书尚无通过的阅读记录，则据会话元信息
    新建一条阅读记录。无访问权限者返回 403。

    Args:
        request (HttpRequest): 当前请求对象。POST 需按题目数量提供
            ``q0``、``q1`` … 等选项索引字段。
        attempt_id (int): 目标测验记录主键。

    Returns:
        HttpResponse: 渲染 ``reading/quiz_result.html``（提交后）或
        ``reading/quiz_take.html``（作答中）的响应；无权限时返回 403。

    Raises:
        Http404: 指定测验记录不存在。
    """
    persona = get_persona(request)
    attempt = get_object_or_404(QuizAttempt, pk=attempt_id)
    if not _can_access_attempt(persona, attempt):
        return HttpResponseForbidden()
    questions = attempt.questions
    if not attempt.submitted and not questions:
        questions = _recover_questions(attempt)
    # A completed attempt is immutable. Refreshing the result page or sending
    # another POST must never change its score, answers, or reading record.
    if attempt.submitted:
        correct = sum(
            answer == question['answer']
            for answer, question in zip(attempt.answers, questions)
        )
        return render(request, 'reading/quiz_result.html', {
            'attempt': attempt,
            'correct': correct,
            'total': len(questions),
            'remaining': _remaining(attempt.student, attempt.book),
        })
    if request.method == 'POST':
        answers = [int(request.POST.get(f'q{i}', -1)) for i in range(len(questions))]; correct = sum(a == q['answer'] for a, q in zip(answers, questions)); score = round(correct * 100 / len(questions)) if questions else 0
        attempt.score = score; attempt.passed = score >= 60; attempt.answers = answers; attempt.questions = questions; attempt.submitted = True; attempt.completed_at = timezone.now(); attempt.save()
        if attempt.passed and not ReadingRecord.objects.filter(student=attempt.student, book=attempt.book, passed=True).exists():
            meta = request.session.get(f'quiz_meta_{attempt.pk}', {}); minutes = meta.get('minutes')
            ReadingRecord.objects.create(student=attempt.student, book=attempt.book, read_date=meta.get('date') or date.today(), words=attempt.book.words or 0, minutes=int(minutes) if minutes else None, quiz_score=score, passed=True)
        return render(request, 'reading/quiz_result.html', {'attempt': attempt, 'correct': correct, 'total': len(questions), 'remaining': _remaining(attempt.student, attempt.book)})
    attempt_number = QuizAttempt.objects.filter(student=attempt.student, book=attempt.book, passed=False, submitted=True).count() + 1
    return render(request, 'reading/quiz_take.html', {'attempt': attempt, 'questions': questions, 'attempt_number': attempt_number, 'max_attempts': MAX_SUBMITTED_ATTEMPTS})


@persona_required(TEACHER, MANAGER, STUDENT, PARENT)
def quiz_review(request, attempt_id):
    """测验回顾视图，展示某次已提交测验的题目与作答对错。

    仅能回顾已提交的记录，且需通过访问权限校验。学生只能回顾
    已通过的测验；未通过的测验对学生隐藏题目详情，仅显示剩余次数。
    员工身份始终可查看题目。

    Args:
        request (HttpRequest): 当前请求对象。
        attempt_id (int): 目标测验记录主键（须已提交）。

    Returns:
        HttpResponse: 渲染 ``reading/quiz_review.html`` 的响应；
        无权限或学生查看未通过测验时返回 403。

    Raises:
        Http404: 指定的已提交测验记录不存在。
    """
    persona = get_persona(request)
    attempt = get_object_or_404(QuizAttempt, pk=attempt_id, submitted=True)
    if not _can_access_attempt(persona, attempt):
        return HttpResponseForbidden()
    if persona.kind == STUDENT and not attempt.passed:
        return HttpResponseForbidden()
    show_questions = attempt.passed or persona.is_staff
    rows = []
    if show_questions:
        for question, answer in zip(attempt.questions, attempt.answers):
            options = [{'text': text, 'chosen': index == answer, 'answer': index == question['answer']}
                for index, text in enumerate(question['options'])]
            rows.append({'prompt': question['prompt'], 'options': options, 'correct': answer == question['answer']})
    context = {'attempt': attempt, 'rows': rows, 'show_questions': show_questions}
    if not show_questions:
        context['remaining'] = _remaining(attempt.student, attempt.book)
    return render(request, 'reading/quiz_review.html', context)
