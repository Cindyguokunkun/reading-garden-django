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

    def test_book_form_tools_translate(self):
        response = self.client.get(reverse('book_add'), headers={'accept-language': 'en'})
        self.assertContains(response, 'Look up on AR BookFinder')
        self.assertContains(response, 'Synopsis')
        make_book('i18n-card')
        self.assertContains(self.client.get(reverse('library'), headers={'accept-language': 'en'}), 'Level')

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from config.envfile import load_env

class EnvLoaderTests(TestCase):
    def env_file(self, text):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / '.env'
        path.write_text(text, encoding='utf-8-sig')
        return path

    def watch(self, *keys):
        for key in keys:
            os.environ.pop(key, None)
            self.addCleanup(os.environ.pop, key, None)

    def test_parses_values_skips_junk_and_strips_quotes(self):
        self.watch('RG_TEST_A', 'RG_TEST_B', 'RG_TEST_C', 'RG_TEST_D')
        load_env(self.env_file('RG_TEST_A=1\n# comment\n\nRG_TEST_B = "quoted"\nRG_TEST_C=\'single\'\nno equals here\nRG_TEST_D=http://127.0.0.1:7897\n'))
        self.assertEqual(os.environ['RG_TEST_A'], '1')
        self.assertEqual(os.environ['RG_TEST_B'], 'quoted')
        self.assertEqual(os.environ['RG_TEST_C'], 'single')
        self.assertEqual(os.environ['RG_TEST_D'], 'http://127.0.0.1:7897')

    def test_real_environment_wins(self):
        self.watch('RG_TEST_E')
        os.environ['RG_TEST_E'] = 'from-shell'
        load_env(self.env_file('RG_TEST_E=from-file\n'))
        self.assertEqual(os.environ['RG_TEST_E'], 'from-shell')

    def test_missing_or_empty_file_is_silent(self):
        self.watch('RG_TEST_A')
        load_env(Path('no-such-folder') / '.env')
        load_env(self.env_file(''))
        self.assertNotIn('RG_TEST_A', os.environ)

from decimal import Decimal
from unittest import mock
from django.test import override_settings
from reading.services import arbookfinder

class AtosBandTests(TestCase):
    def test_bands(self):
        expected = [('0.1', 'graded'), ('1.4', 'graded'), ('1.5', 'bridge'), ('2.4', 'bridge'), ('2.5', 'early_chapter'),
            ('3.4', 'early_chapter'), ('3.5', 'middle_chapter'), ('4.9', 'middle_chapter'), ('5.0', 'upper_chapter'), ('13.5', 'upper_chapter')]
        for raw, category in expected:
            self.assertEqual(arbookfinder.atos_category(Decimal(raw)), category, raw)

    def test_missing_level_has_no_category(self):
        self.assertEqual(arbookfinder.atos_category(None), '')

ARF_ROW = ('<a href="bookdetail.aspx?q=%(q)s&l=EN&slid=780055052" id="book-title">%(title)s</a><br>\n'
    '  <p>%(author)s<br>\n  AR Quiz No. %(q)s EN %(fiction)s<br>\n'
    '  <a href="popups/searchresultsquizinfo_US.aspx" onClick="window.open(\'popups/searchresultsquizinfo_US.aspx\'); return false;">'
    '<img src="images/question.gif" alt="Accelerated Reader Quiz Information" border="0"></a>&nbsp;'
    'IL: <strong>%(il)s</strong> - BL: <strong>%(bl)s</strong> - AR Pts: <strong>%(pts)s</strong><br>\n'
    '  <a href="popups/searchresultsquiztypes_US.aspx"><img src="images/question.gif" alt="Accelerated Reader Quiz Type Information" border="0"></a>'
    '&nbsp;AR Quiz Types: <strong>RP</strong>, <strong>RV</strong><br>\n  %(series)s</p>\n'
    '  <table><tr><td><input type="image" src="images/sr_addtobb_v2_USRLEN.png" alt="Add To AR BookBag"></td></tr></table>\n')

ARF_RESULTS = '<html><body><table>' + ''.join([
    ARF_ROW % {'q': '178400', 'title': "#103 Mike the Monkey Builds a Treehouse", 'author': "Kid's English", 'fiction': 'Fiction', 'il': 'LG', 'bl': '3.3', 'pts': '0.5', 'series': "Kid's English #103"},
    ARF_ROW % {'q': '166532', 'title': '100 Hungry Monkeys!', 'author': 'Sebe, Masayuki', 'fiction': 'Fiction', 'il': 'LG', 'bl': '2.0', 'pts': '0.5', 'series': ''},
    ARF_ROW % {'q': '147183', 'title': 'Albert II: The 1st Monkey in Space', 'author': 'Dunn, Joeming', 'fiction': 'Nonfiction', 'il': 'MG', 'bl': '5.8', 'pts': '0.5', 'series': ''},
]) + '</table></body></html>'

ARF_DETAIL = ('<table class="detail-table"><tr align="left" valign="top"><td width="120" align="center">'
    '<img id="ctl00_ContentPlaceHolder1_ucBookDetail_imgBookCover" title="#103 Mike the Monkey Builds a Treehouse" src="https://coverscans.renlearn.com/9788966823758.jpg" /></td>'
    '<td align="left"><strong><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblBookTitle">#103 Mike the Monkey Builds a Treehouse</span></strong><br />'
    '<span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblAuthor">Kid\'s English</span><br />'
    '<span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblQuizNumberTitle">AR Quiz No.</span> '
    '<span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblQuizNumber">178400</span> '
    '<span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblLanguageCode">EN</span><br /><br />'
    '<span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblBookSummary">A monkey class builds a treehouse together.</span><br />'
    '<table width="95%" border="0" cellspacing="0" cellpadding="4">'
    '<tr bgcolor="#D9D9D9"><td width="140"><strong><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblATOSName">ATOS Book Level: </span></strong></td>'
    '<td><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblBookLevel">3.3</span> </td></tr>'
    '<tr><td width="140"><strong><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblInterestLevelTitle">Interest Level:</span> </strong></td>'
    '<td><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblInterestLevel">Lower Grades (LG K-3)</span></td></tr>'
    '<tr bgcolor="#D9D9D9"><td width="140"><strong><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblPointsTitle">AR Points:</span> </strong></td>'
    '<td><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblPoints">0.5</span></td></tr>'
    '<tr><td width="140"><strong><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblWordCountTitle">Word Count:</span> </strong></td>'
    '<td><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblWordCount">921</span></td></tr>'
    '<tr bgcolor="#D9D9D9"><td width="140"><strong><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblFictionNonFictionTitle">Fiction/Nonfiction</span></strong></td>'
    '<td><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblFictionNonFiction">Fiction</span></td></tr>'
    '<tr><td width="140"><strong><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblTopicTitle">Topic - Subtopic:</span> </strong></td>'
    '<td><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblTopicLabel"></span></td></tr>'
    '<tr bgcolor="#D9D9D9"><td width="140"><strong><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblSeriesTitle">Series:</span> </strong></td>'
    '<td><span id="ctl00_ContentPlaceHolder1_ucBookDetail_lblSeriesLabel">Kid\'s English; </span> </td></tr>'
    '</table><br /></td></tr></table>')

class ArfParseTests(TestCase):
    def test_result_rows(self):
        rows = arbookfinder.parse_result_rows(ARF_RESULTS)
        self.assertEqual([r['title'] for r in rows], ['#103 Mike the Monkey Builds a Treehouse', '100 Hungry Monkeys!', 'Albert II: The 1st Monkey in Space'])
        first = rows[0]
        self.assertEqual(first['quiz_no'], '178400')
        self.assertEqual(first['url'], 'https://www.arbookfind.com/bookdetailprint.aspx?l=EN&q=178400')
        self.assertEqual(first['atos'], Decimal('3.3'))
        self.assertEqual(first['points'], Decimal('0.5'))
        self.assertEqual(first['interest_level'], 'LG')
        self.assertEqual(first['fiction'], 'Fiction')
        self.assertEqual(rows[2]['fiction'], 'Nonfiction')
        self.assertEqual(rows[1]['author'], 'Sebe, Masayuki')

    def test_result_rows_are_capped(self):
        row = ARF_ROW % {'q': '166532', 'title': '100 Hungry Monkeys!', 'author': 'Sebe, Masayuki', 'fiction': 'Fiction', 'il': 'LG', 'bl': '2.0', 'pts': '0.5', 'series': ''}
        self.assertEqual(len(arbookfinder.parse_result_rows(row * 12)), arbookfinder.MAX_CANDIDATES)

    def test_unparsable_results_yield_nothing(self):
        for markup in ['', '<html><body><p>No results matched your search.</p></body></html>', ARF_RESULTS[:120]]:
            rows = arbookfinder.parse_result_rows(markup)
            self.assertEqual([row for row in rows if row['quiz_no']], [])

    def test_detail_fields(self):
        detail = arbookfinder.parse_detail(ARF_DETAIL)
        self.assertEqual(detail['title'], '#103 Mike the Monkey Builds a Treehouse')
        self.assertEqual(detail['quiz_no'], '178400')
        self.assertEqual(detail['synopsis'], 'A monkey class builds a treehouse together.')
        self.assertEqual(detail['atos'], Decimal('3.3'))
        self.assertEqual(detail['points'], Decimal('0.5'))
        self.assertEqual(detail['words'], 921)
        self.assertEqual(detail['interest_level'], 'Lower Grades (LG K-3)')
        self.assertEqual(detail['fiction'], 'Fiction')
        self.assertEqual(detail['series'], "Kid's English")
        self.assertIsNone(detail['topics'])

    def test_detail_word_count_thousands_separator(self):
        self.assertEqual(arbookfinder.parse_detail(ARF_DETAIL.replace('>921<', '>4,499<'))['words'], 4499)

    def test_unparsable_detail_yields_nones(self):
        for markup in ['', '<html><body>Book not found</body></html>', ARF_DETAIL[:400]]:
            detail = arbookfinder.parse_detail(markup)
            self.assertIsNone(detail['atos'])
            self.assertIsNone(detail['words'])
            self.assertIsNone(detail['synopsis'])

    @override_settings(ARF_THROTTLE=0)
    def test_fetch_detail_rejects_error_pages(self):
        with mock.patch.object(arbookfinder.http, 'fetch', return_value=(200, '<html></html>', 'https://www.arbookfind.com/bookfindererror.aspx')):
            self.assertRaises(arbookfinder.ArfError, arbookfinder.fetch_detail, 'https://www.arbookfind.com/bookdetailprint.aspx?l=EN&q=178400')
        with mock.patch.object(arbookfinder.http, 'fetch', return_value=(200, '<html></html>', 'https://www.arbookfind.com/bookdetailprint.aspx?l=EN&q=178400')):
            self.assertRaises(arbookfinder.ArfError, arbookfinder.fetch_detail, 'https://www.arbookfind.com/bookdetailprint.aspx?l=EN&q=178400')
        self.assertRaises(arbookfinder.ArfError, arbookfinder.fetch_detail, '')
        with mock.patch.object(arbookfinder.http, 'fetch') as fetch:
            self.assertRaises(arbookfinder.ArfError, arbookfinder.fetch_detail, 'http://169.254.169.254/latest/meta-data/')
        fetch.assert_not_called()

    @override_settings(ARF_THROTTLE=0)
    def test_fetch_detail_returns_parsed_page(self):
        url = 'https://www.arbookfind.com/bookdetailprint.aspx?l=EN&q=178400'
        with mock.patch.object(arbookfinder.http, 'fetch', return_value=(200, ARF_DETAIL, url)):
            detail = arbookfinder.fetch_detail(url)
        self.assertEqual(detail['words'], 921)
        self.assertEqual(detail['url'], url)

import json
from django.utils import translation
from reading.services import http as service_http
from reading.services import quizgen
from .models import CATEGORY_CHOICES

QUIZGEN_SETTINGS = dict(QUIZGEN_ENABLED=True, QUIZGEN_BASE_URL='https://ai.example.com/v1/', QUIZGEN_API_KEY='sk-test-secret',
    QUIZGEN_MODEL='test-model', QUIZGEN_PROXY='', QUIZGEN_TIMEOUT=5, QUIZGEN_QUESTIONS=10, QUIZGEN_MIN_QUESTIONS=5,
    QUIZGEN_MATERIAL_CHARS=8000, QUIZGEN_JSON_MODE=True)

def golden(count=1, **overrides):
    item = {'prompt': 'Who builds the treehouse?', 'options': ['The monkey class', 'A farmer', 'Two birds', 'A robot'], 'answer': 0}
    item.update(overrides)
    return [dict(item) for _ in range(count)]

class QuizGenParseTests(TestCase):
    def test_bare_array(self):
        self.assertEqual(quizgen.extract_json('[{"prompt": "A?", "options": ["a", "b"], "answer": 0}]'), [{'prompt': 'A?', 'options': ['a', 'b'], 'answer': 0}])

    def test_fenced_json(self):
        self.assertEqual(quizgen.extract_json('```json\n[1, 2, 3]\n```'), [1, 2, 3])
        self.assertEqual(quizgen.extract_json('```\n[1, 2]\n```'), [1, 2])

    def test_prose_around_the_json(self):
        text = 'Sure! Here are the questions you asked for:\n[{"prompt": "A [tricky] one?", "options": ["a", "b"], "answer": 1}]\nHope this helps.'
        self.assertEqual(quizgen.extract_json(text), [{'prompt': 'A [tricky] one?', 'options': ['a', 'b'], 'answer': 1}])

    def test_dict_wrapping_questions(self):
        parsed = quizgen.extract_json('{"questions": [{"prompt": "A?", "options": ["a", "b"], "answer": 0}]}')
        questions, errors = quizgen.validate_questions(parsed)
        self.assertEqual(errors, [])
        self.assertEqual(len(questions), 1)

    def test_an_unexpected_wrapper_key_is_still_read(self):
        parsed = quizgen.extract_json('{"items": [{"prompt": "A?", "options": ["a", "b"], "answer": 1}], "note": "done"}')
        questions, errors = quizgen.validate_questions(parsed)
        self.assertEqual(errors, [])
        self.assertEqual(questions, [{'prompt': 'A?', 'options': ['a', 'b'], 'answer': 1}])

    def test_garbage_input_yields_nothing(self):
        for text in ['', None, 'no json here', '[{"prompt": ', '[1, 2', '{"questions":']:
            self.assertIsNone(quizgen.extract_json(text), text)
        self.assertEqual(quizgen.extract_json('{"questions": "nope"}'), {'questions': 'nope'})

class ValidateQuestionsTests(TestCase):
    def test_golden_sample_is_normalized(self):
        questions, errors = quizgen.validate_questions(golden(difficulty='easy'))
        self.assertEqual(errors, [])
        self.assertEqual(questions, [{'prompt': 'Who builds the treehouse?', 'options': ['The monkey class', 'A farmer', 'Two birds', 'A robot'], 'answer': 0}])

    def test_bad_items_are_dropped_with_reasons(self):
        bad = [
            'not an object',
            {'prompt': '', 'options': ['a', 'b'], 'answer': 0},
            {'prompt': 'x' * 1001, 'options': ['a', 'b'], 'answer': 0},
            {'prompt': 'One option?', 'options': ['only'], 'answer': 0},
            {'prompt': 'Seven options?', 'options': list('abcdefg'), 'answer': 0},
            {'prompt': 'Duplicates?', 'options': ['Cat', 'cat', 'Dog'], 'answer': 0},
            {'prompt': 'Empty option?', 'options': ['a', '  '], 'answer': 0},
            {'prompt': 'Out of range?', 'options': ['a', 'b'], 'answer': 2},
            {'prompt': 'Negative?', 'options': ['a', 'b'], 'answer': -1},
            {'prompt': 'Boolean answer?', 'options': ['a', 'b'], 'answer': True},
            {'prompt': 'Float answer?', 'options': ['a', 'b'], 'answer': 0.5},
            {'prompt': 'No answer key?', 'options': ['a', 'b']},
        ]
        with translation.override('en'):
            questions, errors = quizgen.validate_questions(bad + golden(2), min_questions=1, max_questions=20)
            self.assertTrue(all(error.startswith('Question ') for error in errors))
        self.assertEqual(len(errors), len(bad))
        self.assertEqual(questions, golden(2))

    def test_answers_are_coerced_to_int(self):
        questions, errors = quizgen.validate_questions([{'prompt': 'A?', 'options': ['a', 'b', 'c'], 'answer': '2'}, {'prompt': 'B?', 'options': ['a', 'b', 'c'], 'answer': 1.0}])
        self.assertEqual(errors, [])
        self.assertEqual([question['answer'] for question in questions], [2, 1])
        self.assertTrue(all(isinstance(question['answer'], int) for question in questions))

    def test_too_few_questions_fails_wholesale(self):
        with translation.override('en'):
            questions, errors = quizgen.validate_questions(golden(4), min_questions=5)
            self.assertEqual(questions, [])
            self.assertIn('at least 5', errors[-1])
        questions, errors = quizgen.validate_questions(golden(5), min_questions=5)
        self.assertEqual(len(questions), 5)
        self.assertEqual(errors, [])

    def test_not_a_list_fails(self):
        for raw in [None, 'questions', {'nope': []}, {'questions': 'nope'}]:
            questions, errors = quizgen.validate_questions(raw)
            self.assertEqual(questions, [])
            self.assertEqual(len(errors), 1)

    def test_extra_questions_are_truncated(self):
        questions, errors = quizgen.validate_questions(golden(20), max_questions=12)
        self.assertEqual(errors, [])
        self.assertEqual(len(questions), 12)

class MaterialTests(TestCase):
    def test_uploaded_text_file_is_decoded(self):
        self.assertEqual(quizgen.read_material(SimpleUploadedFile('book.txt', 'Monkey Me runs fast.'.encode('utf-8-sig'))), 'Monkey Me runs fast.')
        self.assertEqual(quizgen.read_material(SimpleUploadedFile('book.txt', '小明读英语书'.encode('gbk'))), '小明读英语书')

    def test_pasted_text_is_used_when_nothing_is_uploaded(self):
        self.assertEqual(quizgen.read_material(None, '  pasted text  '), 'pasted text')
        self.assertEqual(quizgen.read_material(), '')

    @override_settings(QUIZGEN_MATERIAL_CHARS=10)
    def test_material_is_truncated(self):
        self.assertEqual(quizgen.read_material(None, 'x' * 50), 'x' * 10)

    def test_wrong_file_type_is_refused(self):
        self.assertRaises(quizgen.QuizGenError, quizgen.read_material, SimpleUploadedFile('book.docx', b'PK\x03\x04'))

    def test_oversized_file_is_refused(self):
        self.assertRaises(quizgen.QuizGenError, quizgen.read_material, SimpleUploadedFile('book.txt', b'x' * (quizgen.MAX_UPLOAD_BYTES + 1)))

class QuizGenCallTests(TestCase):
    def call(self, **kwargs):
        defaults = dict(title='Monkey Me', series='Monkey Me', atos=Decimal('2.4'), category_label='Bridge books', words=4499, material='The text.', n=10)
        defaults.update(kwargs)
        return quizgen.generate_questions(**defaults)

    def reply(self, questions):
        return (200, json.dumps({'choices': [{'message': {'content': json.dumps(questions)}}]}), 'https://ai.example.com/v1/chat/completions')

    @override_settings(QUIZGEN_ENABLED=False)
    def test_disabled_configuration_is_refused_without_a_request(self):
        with mock.patch.object(service_http, 'fetch') as fetch:
            self.assertRaises(quizgen.QuizGenError, self.call)
        fetch.assert_not_called()

    @override_settings(**QUIZGEN_SETTINGS)
    def test_missing_material_is_refused_without_a_request(self):
        with mock.patch.object(service_http, 'fetch') as fetch:
            self.assertRaises(quizgen.QuizGenError, self.call, material='   ')
        fetch.assert_not_called()

    @override_settings(**QUIZGEN_SETTINGS)
    def test_questions_are_returned_and_the_key_is_sent_as_a_header(self):
        with mock.patch.object(service_http, 'fetch', return_value=self.reply(golden(10))) as fetch:
            questions = self.call()
        self.assertEqual(len(questions), 10)
        self.assertEqual(questions[0]['prompt'], 'Who builds the treehouse?')
        headers = fetch.call_args.kwargs['headers']
        self.assertEqual(headers['Authorization'], 'Bearer sk-test-secret')
        payload = json.loads(fetch.call_args.kwargs['data'].decode())
        self.assertEqual(payload['model'], 'test-model')
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertEqual([message['role'] for message in payload['messages']], ['system', 'user'])
        self.assertIn('The text.', payload['messages'][1]['content'])
        self.assertIn('ATOS 2.4', payload['messages'][1]['content'])

    @override_settings(**QUIZGEN_SETTINGS)
    def test_a_lazy_category_label_reaches_the_prompt_as_text(self):
        with translation.override('en'):
            with mock.patch.object(service_http, 'fetch', return_value=self.reply(golden(10))) as fetch:
                self.assertEqual(len(self.call(category_label=dict(CATEGORY_CHOICES)['graded'], atos=None)), 10)
        content = json.loads(fetch.call_args.kwargs['data'].decode())['messages'][1]['content']
        self.assertIn('Level: Graded readers / 4499 words', content)

    @override_settings(**QUIZGEN_SETTINGS)
    def test_failures_are_friendly_and_never_leak_the_key(self):
        cases = [(service_http.HttpError('HTTP 401', status=401), 'API key'), (service_http.HttpError('HTTP 429', status=429), 'rate limited'),
            (service_http.HttpTimeout('timeout'), 'took too long'), (service_http.HttpError('timed out'), 'could not answer')]
        for error, fragment in cases:
            with translation.override('en'):
                with mock.patch.object(service_http, 'fetch', side_effect=error):
                    with self.assertRaises(quizgen.QuizGenError) as caught:
                        self.call()
                self.assertIn(fragment, str(caught.exception))
                self.assertNotIn('sk-test-secret', str(caught.exception))

    @override_settings(**QUIZGEN_SETTINGS)
    def test_unreadable_or_empty_replies_are_refused(self):
        for reply in [(200, 'not json', ''), (200, json.dumps({'choices': [{'message': {'content': 'sorry'}}]}), ''), (200, json.dumps({'choices': []}), '')]:
            with mock.patch.object(service_http, 'fetch', return_value=reply):
                self.assertRaises(quizgen.QuizGenError, self.call)

    @override_settings(**QUIZGEN_SETTINGS)
    def test_too_few_valid_questions_is_refused(self):
        with translation.override('en'):
            with mock.patch.object(service_http, 'fetch', return_value=self.reply(golden(3))):
                with self.assertRaises(quizgen.QuizGenError) as caught:
                    self.call()
            self.assertIn('at least 5', str(caught.exception))

    @override_settings(**QUIZGEN_SETTINGS)
    def test_broken_items_report_a_reason_not_just_the_count(self):
        with translation.override('en'):
            with mock.patch.object(service_http, 'fetch', return_value=self.reply(golden(6, prompt='   '))):
                with self.assertRaises(quizgen.QuizGenError) as caught:
                    self.call()
            self.assertIn('the question text is empty', str(caught.exception))

import time
from reading.views import library as library_views

ARF_CANDIDATE = {'title': 'Monkey Me and the Golden Monkey', 'url': 'https://www.arbookfind.com/bookdetailprint.aspx?l=EN&q=164360',
    'author': 'Roland, Timothy', 'quiz_no': '164360', 'interest_level': 'LG', 'atos': Decimal('2.4'), 'points': Decimal('1.0'), 'fiction': 'Fiction'}

ARF_DETAIL_DATA = dict(ARF_CANDIDATE, synopsis='A monkey takes over the class for a day.', words=4499,
    interest_level='Lower Grades (LG K-3)', series='Monkey Me', topics=None)

def book_form(**extra):
    data = {'title': 'Monkey Me and the Golden Monkey', 'author': '', 'cover': '', 'series': '', 'category': '', 'lexile': '', 'words': '', 'level': '', 'synopsis': '', 'atos': '', 'material': ''}
    data.update(extra)
    return data

def question_form(questions, **extra):
    data = {}
    for index, question in enumerate(questions):
        data['q%d_prompt' % index] = question['prompt']
        for slot in range(6):
            data['q%d_opt%d' % (index, slot)] = question['options'][slot] if slot < len(question['options']) else ''
        data['q%d_answer' % index] = str(question['answer'])
    data.update(extra)
    return data

def notices(response):
    return list(response.context['messages'])

class BookToolTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('lib', password='pw')
        self.room = Classroom.objects.create(owner=self.teacher, name='Y3C3', grade=3)
        self.student = Student.objects.create(classroom=self.room, name='Amy')
        self.client.login(username='lib', password='pw')

    def test_lookup_lists_candidates(self):
        with mock.patch.object(arbookfinder, 'search_candidates', return_value=[ARF_CANDIDATE]) as search:
            response = self.client.post(reverse('book_tool'), book_form(action='lookup'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['candidates'], [ARF_CANDIDATE])
        self.assertContains(response, 'c0_url')
        search.assert_called_once_with('Monkey Me and the Golden Monkey')

    def test_lookup_failure_keeps_the_form_usable(self):
        with mock.patch.object(arbookfinder, 'search_candidates', side_effect=arbookfinder.ArfError('unexpected search page')):
            response = self.client.post(reverse('book_tool'), book_form(action='lookup', series='Monkey Me'), headers={'accept-language': 'en'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['book'].title, 'Monkey Me and the Golden Monkey')
        self.assertEqual(response.context['book'].series, 'Monkey Me')
        self.assertEqual(response.context['candidates'], [])
        self.assertContains(response, 'AR BookFinder')

    def test_lookup_without_a_title_never_calls_the_service(self):
        with mock.patch.object(arbookfinder, 'search_candidates') as search:
            response = self.client.post(reverse('book_tool'), book_form(action='lookup', title='   '))
        self.assertEqual(response.status_code, 200)
        search.assert_not_called()
        self.assertEqual(len(notices(response)), 1)
        self.assertEqual(notices(response)[0].level_tag, 'error')

    def test_pick_fills_a_new_book_without_saving(self):
        with mock.patch.object(arbookfinder, 'fetch_detail', return_value=ARF_DETAIL_DATA) as detail:
            response = self.client.post(reverse('book_tool'), book_form(action='pick:0', c0_url=ARF_CANDIDATE['url']))
        detail.assert_called_once_with(ARF_CANDIDATE['url'])
        book = response.context['book']
        self.assertEqual(book.atos, Decimal('2.4'))
        self.assertEqual(book.words, 4499)
        self.assertEqual(book.category, 'bridge')
        self.assertEqual(book.series, 'Monkey Me')
        self.assertEqual(book.level, 'Lower Grades (LG K-3)')
        self.assertEqual(book.synopsis, 'A monkey takes over the class for a day.')
        self.assertEqual(book.source, ARF_CANDIDATE['url'])
        self.assertEqual(response.context['words_choice'], '')
        self.assertEqual(Book.objects.count(), 0)

    def test_pick_offers_a_choice_when_the_word_count_differs(self):
        existing = make_book('mm-existing', words=4423, quiz=False, title='Monkey Me and the Golden Monkey')
        with mock.patch.object(arbookfinder, 'fetch_detail', return_value=ARF_DETAIL_DATA):
            response = self.client.post(reverse('book_tool'), book_form(action='pick:0', pk=existing.pk, words='4423', c0_url=ARF_CANDIDATE['url']))
        self.assertEqual(response.context['words_choice'], 'db')
        self.assertEqual(response.context['words_arf'], 4499)
        self.assertEqual(response.context['book'].words, 4423)
        self.assertContains(response, 'words_choice')
        for choice, expected in (('arf', 4499), ('db', 4423)):
            self.client.post(reverse('book_edit', args=[existing.pk]), book_form(pk=existing.pk, words='4423', words_choice=choice, words_arf='4499'))
            self.assertEqual(Book.objects.get(pk=existing.pk).words, expected)

    def test_pick_does_not_overwrite_a_category_the_teacher_set(self):
        existing = make_book('mm-graded', words=None, quiz=False, category='graded', series='Reading A-Z')
        with mock.patch.object(arbookfinder, 'fetch_detail', return_value=ARF_DETAIL_DATA):
            response = self.client.post(reverse('book_tool'), book_form(action='pick:0', pk=existing.pk, category='graded', series='Reading A-Z', c0_url=ARF_CANDIDATE['url']))
        self.assertEqual(response.context['book'].category, 'graded')
        self.assertEqual(response.context['book'].series, 'Reading A-Z')
        self.assertEqual(response.context['book'].atos, Decimal('2.4'))

    def test_pick_without_a_candidate_is_an_error(self):
        with mock.patch.object(arbookfinder, 'fetch_detail') as detail:
            response = self.client.post(reverse('book_tool'), book_form(action='pick'))
        self.assertEqual(response.status_code, 200)
        detail.assert_not_called()
        self.assertEqual(notices(response)[0].level_tag, 'error')

    @override_settings(**QUIZGEN_SETTINGS)
    def test_quizgen_returns_editable_rows(self):
        with mock.patch.object(quizgen, 'generate_questions', return_value=golden(10)) as generate:
            response = self.client.post(reverse('book_tool'), book_form(action='quizgen', material='The monkey runs.'))
        self.assertEqual(len(response.context['questions']), 10)
        self.assertEqual(len(response.context['questions'][0]['options']), 4)
        self.assertContains(response, 'q0_prompt')
        self.assertEqual(generate.call_args.kwargs['material'], 'The monkey runs.')
        self.assertEqual(response.context['material'], 'The monkey runs.')

    @override_settings(**QUIZGEN_SETTINGS)
    def test_quizgen_falls_back_to_the_synopsis(self):
        with mock.patch.object(quizgen, 'generate_questions', return_value=golden(10)) as generate:
            self.client.post(reverse('book_tool'), book_form(action='quizgen', synopsis='A monkey takes over the class.'))
        self.assertEqual(generate.call_args.kwargs['material'], 'A monkey takes over the class.')

    @override_settings(**QUIZGEN_SETTINGS)
    def test_quizgen_reads_an_uploaded_txt_file(self):
        upload = SimpleUploadedFile('chapter-1.txt', 'The monkey runs.'.encode(), content_type='text/plain')
        with mock.patch.object(quizgen, 'generate_questions', return_value=golden(10)) as generate:
            self.client.post(reverse('book_tool'), book_form(action='quizgen', material_file=upload))
        self.assertEqual(generate.call_args.kwargs['material'], 'The monkey runs.')

    @override_settings(QUIZGEN_ENABLED=False)
    def test_quizgen_is_refused_when_not_configured(self):
        response = self.client.post(reverse('book_tool'), book_form(action='quizgen', material='The monkey runs.'), headers={'accept-language': 'en'})
        self.assertEqual(response.context['questions'], None)
        self.assertEqual(notices(response)[0].level_tag, 'error')
        self.assertContains(response, '.env')

    @override_settings(**QUIZGEN_SETTINGS)
    def test_quizgen_without_material_is_refused(self):
        response = self.client.post(reverse('book_tool'), book_form(action='quizgen'), headers={'accept-language': 'en'})
        self.assertEqual(response.context['questions'], None)
        self.assertEqual(notices(response)[0].level_tag, 'error')
        self.assertContains(response, 'source material')

    @override_settings(**QUIZGEN_SETTINGS)
    def test_a_second_generation_is_refused_while_one_is_running(self):
        session = self.client.session
        session[library_views.IN_FLIGHT_KEY] = time.monotonic()
        session.save()
        with mock.patch.object(quizgen, 'generate_questions', return_value=golden(10)) as generate:
            response = self.client.post(reverse('book_tool'), book_form(action='quizgen', material='The monkey runs.'), headers={'accept-language': 'en'})
        generate.assert_not_called()
        self.assertEqual(notices(response)[0].level_tag, 'error')
        self.assertContains(response, 'another tab')
        self.assertIn(library_views.IN_FLIGHT_KEY, self.client.session)

    @override_settings(**QUIZGEN_SETTINGS)
    def test_a_service_failure_is_reported_and_clears_the_lock(self):
        with mock.patch.object(quizgen, 'generate_questions', side_effect=quizgen.QuizGenError('The AI service refused the API key in .env.')):
            response = self.client.post(reverse('book_tool'), book_form(action='quizgen', material='The monkey runs.'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'API key')
        self.assertNotIn(library_views.IN_FLIGHT_KEY, self.client.session)

    def test_editquiz_reveals_the_saved_questions(self):
        book = make_book('edit-me')
        response = self.client.post(reverse('book_tool'), book_form(action='editquiz', pk=book.pk, title=book.title))
        self.assertEqual(len(response.context['questions']), 10)
        self.assertContains(response, 'q9_prompt')
        self.assertContains(response, 'q0_drop')

    def test_editquiz_needs_a_saved_book(self):
        response = self.client.post(reverse('book_tool'), book_form(action='editquiz'))
        self.assertEqual(response.context['questions'], None)
        self.assertEqual(notices(response)[0].level_tag, 'error')

    def test_unknown_action_is_refused(self):
        response = self.client.post(reverse('book_tool'), book_form(action='delete-everything'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(notices(response)[0].level_tag, 'error')

    def test_get_returns_to_the_book_form_with_a_warning(self):
        book = make_book('reload-1', title='Reload Book')
        response = self.client.get(reverse('book_tool'), follow=True, HTTP_REFERER=f'http://testserver/library/{book.pk}/edit/')
        self.assertRedirects(response, reverse('book_edit', args=[book.pk]))
        self.assertEqual(notices(response)[0].level_tag, 'warning')

    def test_get_follows_only_the_referer_path_never_its_host(self):
        book = make_book('reload-2', title='Referer Book')
        response = self.client.get(reverse('book_tool'), HTTP_REFERER=f'http://evil.example.com/library/{book.pk}/edit/')
        self.assertEqual(response.url, reverse('book_edit', args=[book.pk]))

    def test_get_without_a_usable_referer_falls_back_to_the_library(self):
        for referer in [f'http://testserver{reverse("book_tool")}', 'http://testserver/ranks/', 'not a url']:
            response = self.client.get(reverse('book_tool'), follow=True, HTTP_REFERER=referer)
            self.assertRedirects(response, reverse('library'))
            self.assertEqual(notices(response)[0].level_tag, 'warning')
        self.assertRedirects(self.client.get(reverse('book_tool'), follow=True), reverse('library'))

    def test_anonymous_users_are_sent_to_the_login_page(self):
        self.client.logout()
        self.assertRedirects(self.client.post(reverse('book_tool'), book_form(action='lookup')), '/login/?next=/library/tool/')

    def test_students_cannot_use_the_tools(self):
        self.client.logout()
        self.client.post(reverse('student_pick', args=[self.room.pk]), {'student': self.student.pk})
        self.assertEqual(self.client.post(reverse('book_tool'), book_form(action='lookup')).status_code, 403)

class BookQuizSaveTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('quiz', password='pw')
        self.room = Classroom.objects.create(owner=self.teacher, name='Y3C3', grade=3)
        self.student = Student.objects.create(classroom=self.room, name='Amy')
        self.client.login(username='quiz', password='pw')

    def test_saved_questions_work_end_to_end(self):
        form = book_form(title='Generated Book', category='bridge', atos='2.4', words='4499')
        form.update(question_form(golden(10)))
        self.assertRedirects(self.client.post(reverse('book_add'), form), reverse('library'))
        book = Book.objects.get(title='Generated Book')
        self.assertEqual(book.quiz_data, golden(10))
        self.assertEqual(book.atos, Decimal('2.4'))
        self.assertEqual(book.words, 4499)
        response, _, questions = take_quiz(self.client, self.room, self.student, book, correct=True)
        self.assertContains(response, '100%')
        self.assertEqual(ReadingRecord.objects.get().words, 4499)
        self.assertEqual(len(questions), 10)

    def test_one_bad_answer_blocks_the_whole_save(self):
        form = book_form(title='Broken Book')
        form.update(question_form(golden(10), q0_answer='9'))
        response = self.client.post(reverse('book_add'), form)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Book.objects.count(), 0)
        self.assertEqual(len(response.context['errors']), 1)
        self.assertEqual(len(response.context['questions']), 10)
        self.assertContains(response, 'q0_prompt')

    def test_dropping_a_question_saves_the_rest(self):
        form = book_form(title='Nine Book')
        form.update(question_form(golden(10), q0_drop='on'))
        self.assertRedirects(self.client.post(reverse('book_add'), form), reverse('library'))
        self.assertEqual(len(Book.objects.get(title='Nine Book').quiz_data), 9)

    def test_dropping_every_question_saves_an_empty_quiz(self):
        form = book_form(title='No Quiz Book')
        form.update(question_form(golden(2), q0_drop='on', q1_drop='on'))
        self.assertRedirects(self.client.post(reverse('book_add'), form), reverse('library'))
        self.assertEqual(Book.objects.get(title='No Quiz Book').quiz_data, [])

    def test_saving_without_the_editor_leaves_the_quiz_alone(self):
        book = make_book('keep-quiz')
        self.client.post(reverse('book_edit', args=[book.pk]), book_form(pk=book.pk, title='Renamed Book', words='100'))
        book.refresh_from_db()
        self.assertEqual(book.title, 'Renamed Book')
        self.assertEqual(len(book.quiz_data), 10)

    def test_odd_option_counts_survive_and_score_correctly(self):
        three = {'prompt': 'Three options?', 'options': ['a', 'b', 'c'], 'answer': 2}
        six = {'prompt': 'Six options?', 'options': list('abcdef'), 'answer': 5}
        form = book_form(title='Odd Book', words='300')
        form.update(question_form(golden(8) + [three, six]))
        self.assertRedirects(self.client.post(reverse('book_add'), form), reverse('library'))
        book = Book.objects.get(title='Odd Book')
        self.assertEqual([len(question['options']) for question in book.quiz_data][-2:], [3, 6])
        response, _, _ = take_quiz(self.client, self.room, self.student, book, correct=True)
        self.assertContains(response, '100%')
        self.assertEqual(ReadingRecord.objects.get().words, 300)

    def test_editing_a_degenerate_question_is_forced_to_a_fix(self):
        book = make_book('degenerate', quiz=False)
        book.quiz_data = [{'prompt': 'Who is in this book?', 'options': ['Frog and Toad'], 'answer': 0}]
        book.save()
        response = self.client.post(reverse('book_tool'), book_form(action='editquiz', pk=book.pk, title=book.title))
        self.assertEqual(len(response.context['questions'][0]['options']), 4)
        form = book_form(pk=book.pk, title=book.title, words='100')
        form.update(question_form([{'prompt': 'Who is in this book?', 'options': ['Frog and Toad'], 'answer': 0}]))
        response = self.client.post(reverse('book_edit', args=[book.pk]), form)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['errors']), 1)
        book.refresh_from_db()
        self.assertEqual(len(book.quiz_data[0]['options']), 1)

from reading.services import covers

OL_PAYLOAD = {'docs': [
    {'title': 'Monkey Me and the Golden Monkey', 'author_name': ['Roland, Timothy', 'An Illustrator'], 'first_publish_year': 2011, 'cover_i': 12345678},
    {'title': 'No Cover Here', 'author_name': [], 'first_publish_year': 1999},
    {'title': '   ', 'cover_i': 7},
]}

GOOGLE_PAYLOAD = {'items': [
    {'volumeInfo': {'title': 'Monkey Me and the Golden Monkey', 'authors': ['Timothy Roland'], 'publishedDate': '2011-03-01',
        'imageLinks': {'thumbnail': 'http://books.google.com/books/content?id=monkey&zoom=1'}}},
    {'volumeInfo': {'title': 'No Thumbnail', 'authors': []}},
]}

COVER_URL = 'https://covers.openlibrary.org/b/id/12345678-M.jpg'
COVER_CANDIDATE = {'title': 'Monkey Me and the Golden Monkey', 'author': 'Roland, Timothy', 'year': 2011, 'provider': 'Open Library', 'cover': COVER_URL}

def reply(payload):
    return (200, json.dumps(payload), 'https://example.test/search')

class CoversParseTests(TestCase):
    def test_openlibrary_keeps_only_rows_with_a_cover(self):
        self.assertEqual(covers.parse_openlibrary(OL_PAYLOAD), [COVER_CANDIDATE])
        self.assertEqual(covers.parse_openlibrary({}), [])

    def test_openlibrary_rows_are_capped(self):
        self.assertEqual(len(covers.parse_openlibrary({'docs': [{'title': 'Monkey Me', 'cover_i': 1}] * 20})), covers.MAX_CANDIDATES)

    def test_google_thumbnails_are_upgraded_to_https(self):
        self.assertEqual(covers.parse_google(GOOGLE_PAYLOAD), [{'title': 'Monkey Me and the Golden Monkey', 'author': 'Timothy Roland', 'year': 2011,
            'provider': 'Google Books', 'cover': 'https://books.google.com/books/content?id=monkey&zoom=1'}])

    def test_google_rows_without_a_thumbnail_are_dropped(self):
        self.assertEqual(covers.parse_google({'items': [{'volumeInfo': {'title': 'No image'}}]}), [])
        self.assertEqual(covers.parse_google({}), [])

    @override_settings(COVERS_THROTTLE=0)
    def test_openlibrary_is_asked_first_and_only_once(self):
        with mock.patch.object(covers.http, 'fetch', return_value=reply(OL_PAYLOAD)) as fetch:
            self.assertEqual(covers.search_covers('Monkey Me and the Golden Monkey'), [COVER_CANDIDATE])
        fetch.assert_called_once()
        url = fetch.call_args.args[1]
        self.assertTrue(url.startswith(covers.OPENLIBRARY_URL))
        self.assertIn('cover_i', url)
        self.assertIn('limit=8', url)

    @override_settings(COVERS_THROTTLE=0)
    def test_a_refusing_service_falls_through_to_google(self):
        with mock.patch.object(covers.http, 'fetch', side_effect=[service_http.HttpError('HTTP 403', status=403), reply(GOOGLE_PAYLOAD)]) as fetch:
            rows = covers.search_covers('Monkey Me')
        self.assertEqual([row['provider'] for row in rows], ['Google Books'])
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(fetch.call_args.args[1].startswith(covers.GOOGLE_URL))
        self.assertIn('intitle%3AMonkey', fetch.call_args.args[1])

    @override_settings(COVERS_THROTTLE=0)
    def test_no_usable_answer_from_either_service_is_a_cover_error(self):
        cases = [[service_http.HttpError('HTTP 403', status=403), service_http.HttpError('HTTP 500', status=500)],
            [(200, 'not json', ''), (200, '{"items": []}', '')],
            [service_http.HttpTimeout('timeout'), reply({'docs': []})]]
        for side_effect in cases:
            with mock.patch.object(covers.http, 'fetch', side_effect=side_effect):
                self.assertRaises(covers.CoverError, covers.search_covers, 'Monkey Me')

    @override_settings(COVERS_THROTTLE=0)
    def test_an_empty_title_never_reaches_the_network(self):
        with mock.patch.object(covers.http, 'fetch') as fetch:
            for title in ('', '   ', None):
                self.assertRaises(covers.CoverError, covers.search_covers, title)
        fetch.assert_not_called()

    def test_only_the_two_cover_services_pass_the_guard(self):
        for url in (COVER_URL, 'https://books.google.com/books/content?id=x', 'https://encrypted-tbn0.gstatic.com/images?q=tbn:x'):
            self.assertEqual(covers.assert_cover_url(url), url)
        for url in ('', None, '   ', 'http://covers.openlibrary.org/b/id/1-M.jpg', 'https://evil.example.com/x.jpg',
                    'https://covers.openlibrary.org.evil.example.com/x.jpg', 'javascript:alert(1)', '/static/x.jpg'):
            self.assertRaises(covers.CoverError, covers.assert_cover_url, url)

class BookCoverToolTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('cov', password='pw')
        Classroom.objects.create(owner=self.teacher, name='Y3C3', grade=3)
        self.client.login(username='cov', password='pw')

    def test_cover_lookup_lists_candidates_for_the_picker(self):
        with mock.patch.object(covers, 'search_covers', return_value=[COVER_CANDIDATE]) as search:
            response = self.client.post(reverse('book_tool'), book_form(action='cover'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['cover_candidates'], [COVER_CANDIDATE])
        self.assertContains(response, 'v0_cover')
        self.assertContains(response, 'coverpick:0')
        search.assert_called_once_with('Monkey Me and the Golden Monkey')

    def test_cover_lookup_without_a_title_never_calls_the_service(self):
        with mock.patch.object(covers, 'search_covers') as search:
            response = self.client.post(reverse('book_tool'), book_form(action='cover', title='   '))
        search.assert_not_called()
        self.assertEqual(response.context['cover_candidates'], [])
        self.assertEqual(notices(response)[0].level_tag, 'error')

    def test_a_failed_cover_lookup_keeps_the_form_usable(self):
        with mock.patch.object(covers, 'search_covers', side_effect=covers.CoverError('no cover found')):
            response = self.client.post(reverse('book_tool'), book_form(action='cover', series='Monkey Me'), headers={'accept-language': 'en'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['book'].title, 'Monkey Me and the Golden Monkey')
        self.assertEqual(response.context['book'].series, 'Monkey Me')
        self.assertEqual(response.context['cover_candidates'], [])
        self.assertEqual(notices(response)[0].level_tag, 'error')
        self.assertContains(response, 'Neither cover service')

    def test_choosing_a_cover_fills_the_field_without_saving(self):
        response = self.client.post(reverse('book_tool'), book_form(action='coverpick:0', v0_cover=COVER_URL))
        self.assertEqual(response.context['book'].cover, COVER_URL)
        self.assertEqual(Book.objects.count(), 0)
        self.assertEqual(notices(response)[0].level_tag, 'success')
        self.assertContains(response, 'coverprev')

    def test_a_foreign_cover_address_is_refused_and_the_saved_one_survives(self):
        book = make_book('cover-guard', quiz=False)
        book.cover = COVER_URL; book.save()
        response = self.client.post(reverse('book_tool'), book_form(action='coverpick:0', pk=book.pk, cover=COVER_URL, v0_cover='https://evil.example.com/x.jpg'), headers={'accept-language': 'en'})
        self.assertEqual(response.context['book'].cover, COVER_URL)
        self.assertEqual(notices(response)[0].level_tag, 'error')
        self.assertContains(response, 'not from Open Library')
        book.refresh_from_db()
        self.assertEqual(book.cover, COVER_URL)

    def test_author_and_cover_are_saved_and_shown(self):
        self.assertRedirects(self.client.post(reverse('book_add'), book_form(author='Roland, Timothy', cover=COVER_URL)), reverse('library'))
        book = Book.objects.get()
        self.assertEqual(book.author, 'Roland, Timothy')
        self.assertEqual(book.cover, COVER_URL)
        self.assertContains(self.client.get(reverse('library')), 'Roland, Timothy')
        form = self.client.get(reverse('book_edit', args=[book.pk]))
        self.assertContains(form, 'name="author"')
        self.assertContains(form, 'coverprev')
        self.assertContains(form, '自动查找封面')

    def test_the_ar_pick_fills_the_author_only_when_it_is_blank(self):
        with mock.patch.object(arbookfinder, 'fetch_detail', return_value=ARF_DETAIL_DATA):
            response = self.client.post(reverse('book_tool'), book_form(action='pick:0', c0_url=ARF_CANDIDATE['url']))
        self.assertEqual(response.context['book'].author, 'Roland, Timothy')
        book = make_book('has-author', quiz=False)
        book.author = 'Someone Else'; book.save()
        with mock.patch.object(arbookfinder, 'fetch_detail', return_value=ARF_DETAIL_DATA):
            response = self.client.post(reverse('book_tool'), book_form(action='pick:0', pk=book.pk, author='Someone Else', c0_url=ARF_CANDIDATE['url']))
        self.assertEqual(response.context['book'].author, 'Someone Else')
        book.refresh_from_db()
        self.assertEqual(book.author, 'Someone Else')

def fill(book, **fields):
    """make_book only forwards four columns, so the rest are set here."""
    for name, value in fields.items(): setattr(book, name, value)
    book.save()
    return book

class BookDetailTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('detail', password='pw')
        self.room = Classroom.objects.create(owner=self.teacher, name='Y3C3', grade=3)
        self.student = Student.objects.create(classroom=self.room, name='Amy', email='amy@example.com')
        self.student.set_password('amypw'); self.student.save()
        self.book = fill(make_book('detail-main', title='Monkey Me and the Golden Monkey', series='Monkey Me', words=1200),
            author='Roland, Timothy', cover=COVER_URL, category='early_chapter', atos=Decimal('3.3'), synopsis='A monkey class builds a treehouse.')
        self.sibling = make_book('detail-sibling', title='Monkey Me and the Shark', series='Monkey Me', quiz=False)
        self.solo = make_book('detail-solo', title='Solo Book', series='', quiz=False)
        self.standalone = make_book('detail-standalone', title='Labelled Solo', series=str(translation.gettext('Standalone')), quiz=False)
        self.client.login(username='detail', password='pw')

    def _persona(self, kind):
        self.client.logout()
        if kind == 'student':
            self.client.post(reverse('student_pick', args=[self.room.pk]), {'student': self.student.pk})
        else:
            self.client.post(reverse('parent_login'), {'email': 'amy@example.com', 'password': 'amypw'})

    def test_the_detail_page_shows_the_cover_author_and_series_mates(self):
        response = self.client.get(reverse('book_detail', args=[self.book.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Monkey Me and the Golden Monkey')
        self.assertContains(response, 'Roland, Timothy')
        self.assertContains(response, COVER_URL)
        self.assertContains(response, 'A monkey class builds a treehouse.')
        self.assertEqual(list(response.context['siblings']), [self.sibling])
        self.assertContains(response, '同系列')
        self.assertContains(response, reverse('book_detail', args=[self.sibling.pk]))

    def test_a_book_on_its_own_has_no_series_section(self):
        for book in (self.solo, self.standalone):
            response = self.client.get(reverse('book_detail', args=[book.pk]))
            self.assertEqual(list(response.context['siblings']), [])
            self.assertNotContains(response, '同系列')

    def test_staff_see_the_edit_button_and_the_quiz_link(self):
        response = self.client.get(reverse('book_detail', args=[self.book.pk]))
        self.assertContains(response, reverse('book_edit', args=[self.book.pk]))
        self.assertContains(response, '开始测评')
        self.assertContains(response, f'{reverse("quiz_start")}?book={self.book.pk}')

    def test_a_student_sees_the_quiz_link_but_no_edit_button(self):
        self._persona('student')
        response = self.client.get(reverse('book_detail', args=[self.book.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '开始测评')
        self.assertNotContains(response, reverse('book_edit', args=[self.book.pk]))

    def test_a_parent_may_read_the_page_but_not_start_a_quiz(self):
        self._persona('parent')
        response = self.client.get(reverse('book_detail', args=[self.book.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Roland, Timothy')
        self.assertNotContains(response, '开始测评')

    def test_anonymous_visitors_are_sent_to_the_login_page(self):
        self.client.logout()
        self.assertRedirects(self.client.get(reverse('book_detail', args=[self.book.pk])), f'/login/?next=/library/{self.book.pk}/')

    def test_a_book_without_questions_offers_no_quiz(self):
        response = self.client.get(reverse('book_detail', args=[self.sibling.pk]))
        self.assertContains(response, '暂无测验')
        self.assertNotContains(response, '开始测评')

    def test_a_book_without_a_cover_falls_back_to_a_placeholder(self):
        response = self.client.get(reverse('book_detail', args=[self.solo.pk]))
        self.assertNotContains(response, '<img class="bookcover"')
        self.assertContains(response, 'placeholder')

    def test_an_unknown_book_is_not_found(self):
        self.assertEqual(self.client.get(reverse('book_detail', args=[999999])).status_code, 404)

    def test_the_quiz_link_preselects_the_book(self):
        response = self.client.get(reverse('quiz_start') + f'?book={self.book.pk}')
        self.assertEqual(response.context['chosen'], self.book.pk)
        self.assertContains(response, f'value="{self.book.pk}" selected')
        self.assertEqual(self.client.get(reverse('quiz_start') + '?book=abc').context['chosen'], None)
        self.assertNotContains(self.client.get(reverse('quiz_start') + '?book=abc'), f'value="{self.book.pk}" selected')

class LibraryCardTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('cards', password='pw')
        self.room = Classroom.objects.create(owner=self.teacher, name='Y3C3', grade=3)
        self.student = Student.objects.create(classroom=self.room, name='Amy')
        self.book = fill(make_book('card-main', title='Monkey Me and the Golden Monkey', series='Monkey Me', words=1200),
            author='Roland, Timothy', cover=COVER_URL, category='early_chapter', atos=Decimal('3.3'))
        self.solo = make_book('card-solo', title='Solo Book', series='', quiz=False)
        self.client.login(username='cards', password='pw')

    def test_the_list_is_a_grid_of_cards_linking_to_the_detail_page(self):
        response = self.client.get(reverse('library'))
        self.assertContains(response, 'bookgrid')
        self.assertNotContains(response, '<table')
        self.assertContains(response, reverse('book_detail', args=[self.book.pk]))
        self.assertContains(response, 'Roland, Timothy')
        self.assertContains(response, COVER_URL)
        self.assertContains(response, 'placeholder')
        self.assertContains(response, reverse('book_edit', args=[self.book.pk]))

    def test_the_english_cards_keep_the_level_label(self):
        self.assertContains(self.client.get(reverse('library'), headers={'accept-language': 'en'}), 'Level')

    def test_a_student_sees_cards_without_the_edit_link(self):
        self.client.logout()
        self.client.post(reverse('student_pick', args=[self.room.pk]), {'student': self.student.pk})
        response = self.client.get(reverse('library'))
        self.assertContains(response, 'bookgrid')
        self.assertNotContains(response, reverse('book_edit', args=[self.book.pk]))

    def test_search_and_category_still_narrow_the_cards(self):
        self.assertContains(self.client.get(reverse('library') + '?q=Monkey'), 'Monkey Me and the Golden Monkey')
        self.assertNotContains(self.client.get(reverse('library') + '?q=Monkey'), 'Solo Book')
        self.assertContains(self.client.get(reverse('library') + '?category=early_chapter'), 'Monkey Me and the Golden Monkey')
        self.assertNotContains(self.client.get(reverse('library') + '?category=early_chapter'), 'Solo Book')

    def test_a_search_without_a_match_says_so(self):
        response = self.client.get(reverse('library') + '?q=zzz')
        self.assertNotContains(response, 'bookgrid')
        self.assertContains(response, '没有匹配的书')
