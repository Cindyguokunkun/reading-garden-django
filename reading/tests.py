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
