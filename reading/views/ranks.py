from datetime import date
from django.shortcuts import render
from ..models import Classroom
from ..personas import TEACHER, MANAGER, STUDENT, PARENT, accessible_classrooms, current_classroom, get_persona, persona_required
from ..stats import period, rank_rows, sort_rows

@persona_required(TEACHER, MANAGER, STUDENT, PARENT)
def ranks(request):
    persona = get_persona(request)
    tier = request.GET.get('tier', 'class')
    if persona.student: tier = 'class'
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
