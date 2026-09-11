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
    classroom = current_classroom(request); wb = Workbook(); ws = wb.active; ws.title = _('Reading records'); ws.append([_('Student'), _('Date'), _('Series'), _('Title'), _('Words'), _('Minutes'), _('Quiz score')])
    for r in ReadingRecord.objects.filter(student__classroom=classroom).select_related('student', 'book'): ws.append([r.student.name, r.read_date, r.book.series, r.book.title, r.words, r.minutes, r.quiz_score])
    out = BytesIO(); wb.save(out); response = HttpResponse(out.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'); response['Content-Disposition'] = 'attachment; filename="reading-records.xlsx"'; return response
