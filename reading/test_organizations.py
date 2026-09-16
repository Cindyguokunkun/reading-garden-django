from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import date

from .models import (Book, Classroom, Membership, Organization, Profile, ReadingRecord,
                     QuizAttempt, Student, ROLE_MANAGER, ROLE_TEACHER)


class OrganizationIsolationTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.get(slug='beijing-aidi-school')
        self.b = Organization.objects.create(name='Second School', slug='second-school')
        self.manager_a = User.objects.create_user('manager-a', password='manager-pass')
        Profile.objects.create(user=self.manager_a, role=ROLE_MANAGER)
        self.manager_b = User.objects.create_user('manager-b', password='manager-pass')
        Profile.objects.create(user=self.manager_b, role=ROLE_MANAGER)
        Membership.objects.create(user=self.manager_b, organization=self.b, role=ROLE_MANAGER)
        self.room_a = Classroom.objects.create(owner=self.manager_a, organization=self.a, name='A Class')
        self.room_b = Classroom.objects.create(owner=self.manager_b, organization=self.b, name='B Class')
        self.amy = Student.objects.create(classroom=self.room_a, name='Amy')
        self.bob = Student.objects.create(classroom=self.room_b, name='Bob')
        self.public = Book.objects.create(source_id='public-book', title='Public', series='Test', quiz_data=[])
        self.private_b = Book.objects.create(source_id='private-b', title='Secret B', series='Test',
                                             organization=self.b, quiz_data=[])
        ReadingRecord.objects.create(student=self.amy, book=self.public, read_date=date.today(), words=100, passed=True)
        ReadingRecord.objects.create(student=self.bob, book=self.public, read_date=date.today(), words=200, passed=True)

    def login_a(self):
        self.client.login(username='manager-a', password='manager-pass')
        session = self.client.session
        session['organization_id'] = self.a.pk
        session.save()

    def test_school_rank_and_student_pages_do_not_leak_other_school(self):
        self.login_a()
        ranks = self.client.get(reverse('ranks') + '?tier=school')
        self.assertContains(ranks, 'Amy')
        self.assertNotContains(ranks, 'Bob')
        accounts = self.client.get(reverse('student_accounts'))
        self.assertContains(accounts, 'Amy')
        self.assertNotContains(accounts, 'Bob')

    def test_private_books_are_visible_only_to_their_school(self):
        self.login_a()
        page = self.client.get(reverse('library'))
        self.assertContains(page, 'Public')
        self.assertNotContains(page, 'Secret B')
        self.assertEqual(self.client.get(reverse('book_detail', args=[self.private_b.pk])).status_code, 404)

    def test_manager_cannot_open_another_school_quiz_attempt(self):
        book = Book.objects.create(
            source_id='quiz-b', title='Quiz B', series='Test', organization=self.b,
            quiz_data=[{'prompt': 'Q?', 'options': ['A', 'B'], 'answer': 0}],
        )
        attempt = QuizAttempt.objects.create(
            student=self.bob, book=book, score=0, passed=False, started_at=timezone.now()
        )
        self.login_a()
        self.assertEqual(self.client.get(reverse('quiz_take', args=[attempt.pk])).status_code, 403)

    def test_user_cannot_switch_to_an_unjoined_school(self):
        self.login_a()
        response = self.client.post(reverse('organization_switch'), {'organization': self.b.pk})
        self.assertEqual(response.status_code, 404)
