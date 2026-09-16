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

    def test_wrong_answers_can_be_reviewed(self):
        response=self.client.post(reverse('quiz_start'),{'class':self.room.pk,'student':self.student.pk,'book':self.book.pk})
        attempt_id=int(response.url.strip('/').split('/')[-1])
        questions=self.client.session[f'quiz_{attempt_id}']
        answers={f'q{i}':str((q['answer']+1)%4) for i,q in enumerate(questions)}
        response=self.client.post(reverse('quiz_take',args=[attempt_id]),answers)
        self.assertContains(response,'Review Answers')
        self.assertContains(response,'Correct answer:')
        self.assertEqual(ReadingRecord.objects.count(),0)

    def test_submitted_quiz_cannot_be_rescored(self):
        response=self.client.post(reverse('quiz_start'),{'class':self.room.pk,'student':self.student.pk,'book':self.book.pk})
        attempt_id=int(response.url.strip('/').split('/')[-1])
        questions=self.client.session[f'quiz_{attempt_id}']
        correct_answers={f'q{i}':str(q['answer']) for i,q in enumerate(questions)}
        self.client.post(reverse('quiz_take',args=[attempt_id]),correct_answers)
        wrong_answers={f'q{i}':str((q['answer']+1)%4) for i,q in enumerate(questions)}
        response=self.client.post(reverse('quiz_take',args=[attempt_id]),wrong_answers)
        self.assertContains(response,'100%')
        self.assertEqual(ReadingRecord.objects.count(),1)

    def test_excel_export(self):
        response=self.client.get(reverse('export_excel')+f'?class={self.room.pk}')
        self.assertEqual(response.status_code,200)
        self.assertIn('spreadsheetml',response['Content-Type'])

    def test_teacher_can_create_student_login(self):
        response=self.client.post(reverse('action'),{'action':'student_account_create','class':self.room.pk,'student':self.student.pk,'username':'amy01','password':'reader123'})
        self.assertEqual(response.status_code,302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.user.username,'amy01')
        self.client.logout()
        self.assertTrue(self.client.login(username='amy01',password='reader123'))
        response=self.client.get(reverse('dashboard'))
        self.assertContains(response,'Amy的阅读花园')

    def test_student_cannot_use_teacher_actions_or_export(self):
        student_user=User.objects.create_user('amy01',password='reader123')
        self.student.user=student_user; self.student.save(update_fields=['user'])
        self.client.logout(); self.client.login(username='amy01',password='reader123')
        self.assertEqual(self.client.post(reverse('action'),{'action':'class_add','name':'Bad'}).status_code,403)
        self.assertEqual(self.client.get(reverse('export_excel')).status_code,403)

    def test_student_can_start_own_quiz(self):
        student_user=User.objects.create_user('amy01',password='reader123')
        self.student.user=student_user; self.student.save(update_fields=['user'])
        self.client.logout(); self.client.login(username='amy01',password='reader123')
        response=self.client.post(reverse('quiz_start'),{'book':self.book.pk})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.student.quizattempt_set.count(),1)
