from datetime import date
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from .models import Book, Classroom, ReadingRecord, Student

class ReadingGardenTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('teacher',password='testpass')
        self.room=Classroom.objects.create(owner=self.user,name='Y3C3')
        self.student=Student.objects.create(classroom=self.room,name='Amy')
        self.book=Book.objects.create(source_id='test-book',title='Test Book',series='Tests',words=100,quiz_data=[{'prompt':f'Question {i+1}?','options':['Right','A','B','C'],'answer':0} for i in range(10)])
        self.client.login(username='teacher',password='testpass')

    def test_dashboard_and_record(self):
        self.assertEqual(self.client.get(reverse('dashboard')).status_code,200)
        response=self.client.post(reverse('action'),{'action':'record_add','class':self.room.pk,'student':self.student.pk,'book':self.book.pk,'date':date.today().isoformat(),'minutes':'12'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(ReadingRecord.objects.get().words,100)

    def test_passing_quiz_adds_words_once(self):
        response=self.client.post(reverse('quiz_start'),{'class':self.room.pk,'student':self.student.pk,'book':self.book.pk})
        attempt_id=int(response.url.strip('/').split('/')[-1])
        questions=self.client.session[f'quiz_{attempt_id}']
        answers={f'q{i}':str(q['answer']) for i,q in enumerate(questions)}
        response=self.client.post(reverse('quiz_take',args=[attempt_id]),answers)
        self.assertContains(response,'100%')
        self.assertEqual(ReadingRecord.objects.count(),1)

    def test_excel_export(self):
        response=self.client.get(reverse('export_excel')+f'?class={self.room.pk}')
        self.assertEqual(response.status_code,200)
        self.assertIn('spreadsheetml',response['Content-Type'])

from datetime import timedelta
from io import BytesIO
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from openpyxl import Workbook
from .models import ClassGoal, Profile, QuizAttempt

def make_book(source_id, words=100, quiz=True, **kwargs):
    defaults = dict(title='Test Book', series='Tests')
    defaults.update(kwargs)
    return Book.objects.create(source_id=source_id, words=words, title=defaults['title'], series=defaults['series'],
        quiz_data=[{'prompt': f'Question {i+1}?', 'options': ['Right', 'A', 'B', 'C'], 'answer': 0} for i in range(10)] if quiz else [])

def take_quiz(client, classroom, student, book, correct):
    response = client.post(reverse('quiz_start'), {'class': classroom.pk, 'student': student.pk, 'book': book.pk})
    if response.status_code != 302 or response.url == reverse('quiz_start'):
        return response, None, None
    attempt_id = int(response.url.strip('/').split('/')[-1])
    questions = client.session[f'quiz_{attempt_id}']
    answers = {f'q{i}': str(q['answer'] if correct else (q['answer'] + 1) % len(q['options'])) for i, q in enumerate(questions)}
    return client.post(reverse('quiz_take', args=[attempt_id]), answers), attempt_id, questions

class PersonaTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('t1', password='pw')
        self.room = Classroom.objects.create(owner=self.teacher, name='Y3C3', grade=3)
        self.student = Student.objects.create(classroom=self.room, name='Amy', email='amy@example.com')
        self.student.set_password('amypw'); self.student.save()

    def _parent_session(self):
        session = self.client.session
        session['persona_kind'] = 'parent'; session['persona_student_id'] = self.student.pk; session.save()

    def test_student_two_step_login(self):
        self.assertEqual(self.client.get(reverse('student_login')).status_code, 200)
        response = self.client.post(reverse('student_pick', args=[self.room.pk]), {'student': self.student.pk})
        self.assertRedirects(response, reverse('student_home'))
        self.assertContains(self.client.get(reverse('student_home')), 'Amy')

    def test_parent_login_and_rejects_bad_password(self):
        response = self.client.post(reverse('parent_login'), {'email': 'amy@example.com', 'password': 'amypw'})
        self.assertRedirects(response, reverse('parent_home'))
        self.client.post(reverse('logout'))
        response = self.client.post(reverse('parent_login'), {'email': 'amy@example.com', 'password': 'wrong'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '阅读成长</h1>')

    def test_parent_cannot_take_quiz(self):
        self._parent_session()
        self.assertEqual(self.client.get(reverse('quiz_start')).status_code, 403)

    def test_teacher_cannot_open_other_class_attempt(self):
        other = User.objects.create_user('t2', password='pw')
        room2 = Classroom.objects.create(owner=other, name='Y4C1', grade=4)
        s2 = Student.objects.create(classroom=room2, name='Bob')
        attempt = QuizAttempt.objects.create(student=s2, book=make_book('b-oth'), score=0, passed=False, started_at=timezone.now())
        self.client.login(username='t1', password='pw')
        self.assertEqual(self.client.get(reverse('quiz_take', args=[attempt.pk])).status_code, 403)

class QuizRetakeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('teacher2', password='pw')
        self.room = Classroom.objects.create(owner=self.user, name='Y3C3')
        self.student = Student.objects.create(classroom=self.room, name='Amy')
        self.book = make_book('retake-book')
        self.client.login(username='teacher2', password='pw')

    def test_three_submissions_block_fourth(self):
        for _ in range(3):
            response, _, _ = take_quiz(self.client, self.room, self.student, self.book, correct=False)
            self.assertContains(response, '再试一次')
        response = self.client.post(reverse('quiz_start'), {'class': self.room.pk, 'student': self.student.pk, 'book': self.book.pk})
        self.assertRedirects(response, reverse('quiz_start'))
        self.assertEqual(QuizAttempt.objects.count(), 3)

    def test_unsubmitted_attempts_do_not_count(self):
        for _ in range(2):
            self.client.post(reverse('quiz_start'), {'class': self.room.pk, 'student': self.student.pk, 'book': self.book.pk})
        self.assertEqual(QuizAttempt.objects.filter(submitted=True).count(), 0)
        response, _, _ = take_quiz(self.client, self.room, self.student, self.book, correct=True)
        self.assertContains(response, '通过')
        self.assertEqual(ReadingRecord.objects.count(), 1)

    def test_passed_book_cannot_be_retested(self):
        take_quiz(self.client, self.room, self.student, self.book, correct=True)
        response = self.client.post(reverse('quiz_start'), {'class': self.room.pk, 'student': self.student.pk, 'book': self.book.pk})
        self.assertRedirects(response, reverse('quiz_start'))
        self.assertEqual(QuizAttempt.objects.count(), 1)
        self.assertEqual(ReadingRecord.objects.count(), 1)

    def test_retake_keeps_options_but_may_move_them(self):
        _, _, first = take_quiz(self.client, self.room, self.student, self.book, correct=False)
        _, _, second = take_quiz(self.client, self.room, self.student, self.book, correct=False)
        first_correct = sorted(q['options'][q['answer']] for q in first)
        second_correct = sorted(q['options'][q['answer']] for q in second)
        self.assertEqual(first_correct, second_correct)
        self.assertEqual(sorted(o for q in first for o in q['options']), sorted(o for q in second for o in q['options']))

class RanksTests(TestCase):
    def setUp(self):
        self.t1 = User.objects.create_user('r1', password='pw')
        self.t2 = User.objects.create_user('r2', password='pw')
        self.a = Classroom.objects.create(owner=self.t1, name='A', grade=3)
        self.b = Classroom.objects.create(owner=self.t2, name='B', grade=3)
        self.c = Classroom.objects.create(owner=self.t2, name='C', grade=4)
        book = make_book('rank-book'); book2 = make_book('rank-book2')
        today = date.today()
        def rec(classroom, name, words, minutes, when=today, bk=book):
            s = Student.objects.get_or_create(classroom=classroom, name=name)[0]
            ReadingRecord.objects.create(student=s, book=bk, read_date=when, words=words, minutes=minutes, passed=True)
        rec(self.a, 'a1', 500, 30); rec(self.a, 'a2', 300, 40)
        rec(self.b, 'b1', 700, 20); rec(self.c, 'c1', 1000, 50)
        rec(self.a, 'a1', 5000, 10, when=today - timedelta(days=10), bk=book2)
        self.client.login(username='r1', password='pw')

    def names(self, response, key='word_rankings'):
        return [r['name'] for r in response.context[key]]

    def test_class_tier_scoped_to_teacher(self):
        self.assertEqual(self.names(self.client.get(reverse('ranks'))), ['a1', 'a2'])

    def test_grade_tier_spans_teachers(self):
        self.assertEqual(self.names(self.client.get(reverse('ranks') + '?tier=grade&grade=3')), ['b1', 'a1', 'a2'])

    def test_school_tier_and_week_boundary(self):
        self.assertEqual(self.names(self.client.get(reverse('ranks') + '?tier=school')), ['c1', 'b1', 'a1', 'a2'])
        self.assertEqual(self.names(self.client.get(reverse('ranks') + '?tier=school&mode=all')), ['a1', 'c1', 'b1', 'a2'])

    def test_minutes_and_books_boards(self):
        response = self.client.get(reverse('ranks') + '?tier=school&mode=all')
        self.assertEqual([r['name'] for r in response.context['time_rankings']][0], 'c1')
        self.assertEqual([r['books'] for r in response.context['book_rankings']][0], 2)

class ImportTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_user('boss', password='pw')
        Profile.objects.create(user=self.boss, role='manager')
        self.teacher = User.objects.create_user('imp_t', password='pw')

    def upload(self, rows):
        wb = Workbook(); ws = wb.active
        ws.append(['年级', '班级名称', '教师用户名', '学生姓名', '英文名', '邮箱', '初始密码', '家长1', '家长2'])
        for row in rows: ws.append(row)
        out = BytesIO(); wb.save(out)
        f = SimpleUploadedFile('import.xlsx', out.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        return self.client.post(reverse('manage_import'), {'file': f})

    def test_import_creates_students_and_parent_can_login(self):
        self.client.login(username='boss', password='pw')
        response = self.upload([
            [3, 'Y3C3', 'imp_t', '王小明', 'Xiaoming Wang', 'xm@example.com', 'read1234', '王妈妈', ''],
            [3, 'Y3C3', 'imp_t', '小红', '', 'xh@example.com', 'read1234', '', ''],
        ])
        self.assertContains(response, '成功导入 2 名学生')
        self.assertEqual(Student.objects.count(), 2)
        self.assertEqual(Classroom.objects.get(name='Y3C3').grade, 3)
        self.client.post(reverse('logout'))
        response = self.client.post(reverse('parent_login'), {'email': 'xm@example.com', 'password': 'read1234'})
        self.assertRedirects(response, reverse('parent_home'))

    def test_duplicate_email_rolls_back_whole_import(self):
        self.client.login(username='boss', password='pw')
        self.upload([[3, 'Y3C3', 'imp_t', '王小明', '', 'xm@example.com', 'read1234', '', '']])
        response = self.upload([
            [3, 'Y3C3', 'imp_t', '小刚', '', 'xg@example.com', 'read1234', '', ''],
            [3, 'Y3C3', 'imp_t', '小美', '', 'xm@example.com', 'read1234', '', ''],
        ])
        self.assertContains(response, '第 3 行')
        self.assertFalse(Student.objects.filter(name='小刚').exists())
        self.assertEqual(Student.objects.count(), 1)

    def test_non_manager_forbidden(self):
        self.client.login(username='imp_t', password='pw')
        self.assertEqual(self.client.get(reverse('manage_import')).status_code, 403)

class GoalAndLibraryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('gl', password='pw')
        self.room = Classroom.objects.create(owner=self.user, name='Y3C3')
        self.student = Student.objects.create(classroom=self.room, name='Amy')
        self.client.login(username='gl', password='pw')

    def test_goal_set_and_progress(self):
        self.client.post(reverse('action'), {'action': 'goal_set', 'class': self.room.pk, 'words': '1000', 'deadline': ''})
        self.assertEqual(ClassGoal.objects.get(classroom=self.room).words, 1000)
        book = make_book('goal-book', words=500)
        self.client.post(reverse('action'), {'action': 'record_add', 'class': self.room.pk, 'student': self.student.pk, 'book': book.pk, 'date': date.today().isoformat(), 'minutes': '10'})
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.context['goal_percent'], 50)

    def test_book_add_and_category_filter(self):
        response = self.client.post(reverse('book_add'), {'title': 'New Book', 'series': '', 'category': 'graded', 'lexile': 'BR100L', 'words': '300'})
        self.assertRedirects(response, reverse('library'))
        book = Book.objects.get(title='New Book')
        self.assertEqual(book.category, 'graded'); self.assertEqual(book.lexile, 'BR100L'); self.assertEqual(book.words, 300)
        self.assertContains(self.client.get(reverse('library') + '?category=graded'), 'New Book')
        self.assertNotContains(self.client.get(reverse('library') + '?category=bridge'), 'New Book')

class I18nTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('i18n', password='pw')
        self.client.login(username='i18n', password='pw')

    def test_english_rankings_page(self):
        response = self.client.get(reverse('ranks'), headers={'accept-language': 'en'})
        self.assertContains(response, 'Reading Rankings')

    def test_percent_and_placeholder_messages_translate(self):
        room = Classroom.objects.create(owner=self.user, name='G3', grade=3)
        ClassGoal.objects.create(classroom=room, words=5000)
        en = {'accept-language': 'en'}
        response = self.client.get(reverse('quiz_start'), headers=en)
        self.assertContains(response, 'Pass at 60%')
        response = self.client.get(reverse('dashboard'), {'class': room.pk}, headers=en)
        self.assertContains(response, 'Goal 5000 words')
        self.assertContains(response, '(0%)')
        self.assertContains(response, 'Grade 3')
