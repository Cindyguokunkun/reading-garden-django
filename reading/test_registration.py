from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import (AccountAudit, Classroom, ParentStudentLink, Profile, Student,
                     TeacherInvite, ROLE_MANAGER, ROLE_PARENT, ROLE_TEACHER)


class RegistrationTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user('manager', password='manager-pass')
        Profile.objects.create(user=self.manager, role=ROLE_MANAGER)
        self.invite = TeacherInvite.objects.create(
            code='SCHOOL2026', label='English team', created_by=self.manager, max_uses=2
        )

    def test_teacher_registration_waits_for_manager_approval(self):
        response = self.client.post(reverse('teacher_register'), {
            'full_name': 'Ms Wang', 'username': 'mswang', 'email': 'wang@example.com',
            'invite_code': 'school2026', 'password': 'strong-pass', 'confirm': 'strong-pass',
        })
        self.assertEqual(response.status_code, 200)
        teacher = User.objects.get(username='mswang')
        self.assertFalse(teacher.is_active)
        self.assertFalse(teacher.profile.approved)
        self.assertEqual(teacher.profile.role, ROLE_TEACHER)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.uses, 1)
        self.assertFalse(self.client.login(username='mswang', password='strong-pass'))

        self.client.login(username='manager', password='manager-pass')
        self.client.post(reverse('account_approvals'), {
            'action': 'approve', 'profile': teacher.profile.pk,
        })
        teacher.refresh_from_db()
        self.assertTrue(teacher.is_active)
        self.assertTrue(teacher.profile.approved)

    def test_invalid_teacher_invite_creates_nothing(self):
        self.client.post(reverse('teacher_register'), {
            'full_name': 'Ms Li', 'username': 'msli', 'email': 'li@example.com',
            'invite_code': 'WRONG', 'password': 'strong-pass', 'confirm': 'strong-pass',
        })
        self.assertFalse(User.objects.filter(username='msli').exists())

    def test_parent_registers_and_binds_a_child(self):
        teacher = User.objects.create_user('teacher', password='teacher-pass')
        Profile.objects.create(user=teacher, role=ROLE_TEACHER)
        room = Classroom.objects.create(owner=teacher, name='Class 1')
        child = Student.objects.create(classroom=room, name='Amy', login_id='S00001', bind_code='CHILD123')

        response = self.client.post(reverse('parent_register'), {
            'full_name': 'Amy Parent', 'username': 'amyparent', 'email': 'parent@example.com',
            'bind_code': 'child123', 'password': 'strong-pass', 'confirm': 'strong-pass',
        })
        self.assertRedirects(response, reverse('parent_home'))
        parent = User.objects.get(username='amyparent')
        self.assertEqual(parent.profile.role, ROLE_PARENT)
        self.assertTrue(ParentStudentLink.objects.filter(parent=parent, student=child).exists())
        self.assertContains(self.client.get(reverse('parent_home')), 'Amy')

    def test_teacher_can_reset_only_own_students_password(self):
        teacher = User.objects.create_user('teacher', password='teacher-pass')
        Profile.objects.create(user=teacher, role=ROLE_TEACHER)
        room = Classroom.objects.create(owner=teacher, name='Class 1')
        child = Student.objects.create(classroom=room, name='Amy')
        other = User.objects.create_user('other', password='other-pass')
        Profile.objects.create(user=other, role=ROLE_TEACHER)
        outsider = Student.objects.create(classroom=Classroom.objects.create(owner=other, name='Other'), name='Bob')
        self.client.login(username='teacher', password='teacher-pass')

        response = self.client.post(reverse('student_account_action', args=[child.pk]), {'action': 'password'})
        self.assertRedirects(response, reverse('student_accounts'))
        child.refresh_from_db()
        self.assertTrue(child.login_id)
        self.assertTrue(child.bind_code)
        self.assertTrue(child.password_hash)
        self.assertEqual(self.client.post(reverse('student_account_action', args=[outsider.pk]), {'action': 'password'}).status_code, 404)

    def test_manager_can_promote_and_suspend_a_teacher_with_password(self):
        teacher = User.objects.create_user('teacher', password='teacher-pass')
        profile = Profile.objects.create(user=teacher, role=ROLE_TEACHER, approved=True)
        self.client.login(username='manager', password='manager-pass')
        self.client.post(reverse('account_approvals'), {
            'action': 'promote', 'profile': profile.pk, 'current_password': 'manager-pass',
        })
        profile.refresh_from_db()
        self.assertEqual(profile.role, ROLE_MANAGER)
        self.assertTrue(AccountAudit.objects.filter(target=teacher, action='promote_manager').exists())

        self.client.post(reverse('account_approvals'), {
            'action': 'toggle_active', 'profile': profile.pk, 'current_password': 'manager-pass',
        })
        teacher.refresh_from_db()
        self.assertFalse(teacher.is_active)

    def test_wrong_manager_password_changes_nothing(self):
        teacher = User.objects.create_user('teacher', password='teacher-pass')
        profile = Profile.objects.create(user=teacher, role=ROLE_TEACHER, approved=True)
        self.client.login(username='manager', password='manager-pass')
        self.client.post(reverse('account_approvals'), {
            'action': 'promote', 'profile': profile.pk, 'current_password': 'wrong',
        })
        profile.refresh_from_db()
        self.assertEqual(profile.role, ROLE_TEACHER)

    def test_manager_cannot_demote_or_disable_self(self):
        self.client.login(username='manager', password='manager-pass')
        for action in ('demote', 'toggle_active'):
            self.client.post(reverse('account_approvals'), {
                'action': action, 'profile': self.manager.profile.pk,
                'current_password': 'manager-pass',
            })
        self.manager.refresh_from_db()
        self.manager.profile.refresh_from_db()
        self.assertTrue(self.manager.is_active)
        self.assertEqual(self.manager.profile.role, ROLE_MANAGER)
