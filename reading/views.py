import random
from datetime import date, datetime, timedelta
from io import BytesIO
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from .models import Book, Classroom, Goal, QuizAttempt, ReadingRecord, Student

def _student(request):
    try: return request.user.student_profile
    except Student.DoesNotExist: return None

def _class(request):
    qs = Classroom.objects.filter(owner=request.user)
    pk = request.GET.get('class') or request.POST.get('class')
    return qs.filter(pk=pk).first() or qs.first()

def _range(mode, anchor):
    if mode == 'month': return anchor.replace(day=1), (anchor.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
    start = anchor-timedelta(days=anchor.weekday()); return start, start+timedelta(days=6)

@login_required
def dashboard(request):
    student_profile = _student(request)
    if student_profile:
        records = student_profile.records.filter(passed=True).select_related('book')
        attempts = QuizAttempt.objects.filter(student=student_profile).select_related('book')[:20]
        return render(request,'reading/student_dashboard.html',{
            'student':student_profile,'records':records[:100],'attempts':attempts,
            'total_words':records.aggregate(v=Sum('words'))['v'] or 0,
            'total_minutes':records.aggregate(v=Sum('minutes'))['v'] or 0,
        })
    classroom = _class(request); mode=request.GET.get('mode','week')
    try: anchor=date.fromisoformat(request.GET.get('date',''))
    except ValueError: anchor=date.today()
    start,end=_range(mode,anchor)
    students = classroom.students.all() if classroom else Student.objects.none()
    records = ReadingRecord.objects.filter(student__classroom=classroom,passed=True) if classroom else ReadingRecord.objects.none()
    period = records.filter(read_date__range=(start,end))
    rows=[]
    for student in students:
        sr=period.filter(student=student)
        rows.append({'student':student,'words':sr.aggregate(v=Sum('words'))['v'] or 0,'minutes':sr.aggregate(v=Sum('minutes'))['v'] or 0})
    word_rankings=sorted(rows,key=lambda x:(-x['words'],x['student'].name))
    time_rankings=sorted(rows,key=lambda x:(-x['minutes'],x['student'].name))
    series={}
    for book in Book.objects.all().order_by('series','title'): series.setdefault(book.series,[]).append(book)
    return render(request,'reading/dashboard.html',{'classes':Classroom.objects.filter(owner=request.user),'classroom':classroom,'students':students,'records':records[:100],'series':series,'word_rankings':word_rankings,'time_rankings':time_rankings,'mode':mode,'anchor':anchor,'start':start,'end':end,'total_words':records.aggregate(v=Sum('words'))['v'] or 0,'total_minutes':records.aggregate(v=Sum('minutes'))['v'] or 0})

@login_required
@require_POST
def action(request):
    if _student(request):
        return HttpResponse('无权执行此操作',status=403)
    kind=request.POST.get('action'); classroom=_class(request)
    if kind=='class_add': Classroom.objects.create(owner=request.user,name=request.POST['name'].strip())
    elif kind=='student_add' and classroom: Student.objects.create(classroom=classroom,name=request.POST['name'].strip())
    elif kind=='student_account_create' and classroom:
        student=get_object_or_404(Student,pk=request.POST['student'],classroom=classroom)
        username=request.POST['username'].strip(); password=request.POST['password']
        if student.user:
            messages.error(request,'该学生已经有登录账号')
        elif len(password) < 6:
            messages.error(request,'初始密码至少需要6位')
        elif User.objects.filter(username=username).exists():
            messages.error(request,'登录名已被使用')
        else:
            user=User.objects.create_user(username=username,password=password,first_name=student.name)
            student.user=user; student.save(update_fields=['user'])
            messages.success(request,f'已为 {student.name} 创建账号')
    elif kind=='record_add' and classroom:
        student=get_object_or_404(Student,pk=request.POST['student'],classroom=classroom); book=get_object_or_404(Book,pk=request.POST['book'])
        ReadingRecord.objects.create(student=student,book=book,read_date=request.POST['date'],words=book.words or int(request.POST.get('words') or 0),minutes=int(request.POST['minutes']) if request.POST.get('minutes') else None,passed=True)
    messages.success(request,'已保存')
    return redirect(f'/?class={classroom.pk}' if classroom else '/')

@login_required
def quiz_start(request):
    student_profile=_student(request); classroom=student_profile.classroom if student_profile else _class(request)
    if request.method=='POST':
        student=student_profile or get_object_or_404(Student,pk=request.POST['student'],classroom=classroom); book=get_object_or_404(Book,pk=request.POST['book'])
        previous=QuizAttempt.objects.filter(student=student,book=book).count(); questions=[dict(q) for q in book.quiz_data]
        if previous: random.shuffle(questions)
        for q in questions:
            correct=q['options'][q['answer']]; random.shuffle(q['options']); q['answer']=q['options'].index(correct)
        attempt=QuizAttempt.objects.create(student=student,book=book,score=0,passed=False,answers=[],started_at=timezone.now())
        request.session[f'quiz_{attempt.pk}']=questions
        request.session[f'quiz_meta_{attempt.pk}']={'date':request.POST.get('date') or date.today().isoformat(),'minutes':request.POST.get('minutes') or None}
        return redirect('quiz_take',attempt_id=attempt.pk)
    return render(request,'reading/quiz_start.html',{'classroom':classroom,'student_mode':bool(student_profile),'student_profile':student_profile,'classes':Classroom.objects.filter(owner=request.user) if not student_profile else [],'students':classroom.students.all() if classroom and not student_profile else [],'books':Book.objects.exclude(quiz_data=[]).order_by('series','title')})

@login_required
def quiz_take(request,attempt_id):
    student_profile=_student(request)
    if student_profile:
        attempt=get_object_or_404(QuizAttempt,pk=attempt_id,student=student_profile)
    else:
        attempt=get_object_or_404(QuizAttempt,pk=attempt_id,student__classroom__owner=request.user)
    questions=request.session.get(f'quiz_{attempt.pk}',[])
    def result_context():
        answers=attempt.answers or []
        review=[]
        for index,(answer,question) in enumerate(zip(answers,questions),start=1):
            review.append({'number':index,'prompt':question['prompt'],'selected':question['options'][answer] if 0 <= answer < len(question['options']) else 'No answer','correct':question['options'][question['answer']],'is_correct':answer==question['answer']})
        return {'attempt':attempt,'correct':sum(item['is_correct'] for item in review),'total':len(questions),'review':review}
    if attempt.answers:
        return render(request,'reading/quiz_result.html',result_context())
    if request.method=='POST':
        answers=[int(request.POST.get(f'q{i}',-1)) for i in range(len(questions))]; correct=sum(a==q['answer'] for a,q in zip(answers,questions)); score=round(correct*100/len(questions)) if questions else 0
        attempt.score=score;attempt.passed=score>=60;attempt.answers=answers;attempt.save()
        if attempt.passed and not ReadingRecord.objects.filter(student=attempt.student,book=attempt.book,passed=True).exists():
            meta=request.session.get(f'quiz_meta_{attempt.pk}',{}); minutes=meta.get('minutes')
            ReadingRecord.objects.create(student=attempt.student,book=attempt.book,read_date=meta.get('date') or date.today(),words=attempt.book.words or 0,minutes=int(minutes) if minutes else None,quiz_score=score,passed=True)
        return render(request,'reading/quiz_result.html',result_context())
    return render(request,'reading/quiz_take.html',{'attempt':attempt,'questions':questions})

@login_required
def export_excel(request):
    if _student(request): return HttpResponse('无权导出',status=403)
    classroom=_class(request); wb=Workbook();ws=wb.active;ws.title='阅读记录';ws.append(['学生','日期','系列','书名','单词数','时长（分钟）','测评分数'])
    for r in ReadingRecord.objects.filter(student__classroom=classroom).select_related('student','book'): ws.append([r.student.name,r.read_date,r.book.series,r.book.title,r.words,r.minutes,r.quiz_score])
    out=BytesIO();wb.save(out);response=HttpResponse(out.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');response['Content-Disposition']='attachment; filename="reading-records.xlsx"';return response
