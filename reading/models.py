from django.contrib.auth.hashers import check_password as hash_check, make_password
from django.contrib.auth.models import User
from django.db import models
from django.utils.translation import gettext_lazy as _

ROLE_TEACHER = 'teacher'
ROLE_MANAGER = 'manager'
ROLE_CHOICES = [(ROLE_TEACHER, _('Teacher')), (ROLE_MANAGER, _('Manager'))]

def grade_choices():
    return [(g, _('Grade %(g)s') % {'g': g}) for g in range(1, 13)]

CATEGORY_CHOICES = [
    ('graded', _('Graded readers')),
    ('bridge', _('Bridge books')),
    ('early_chapter', _('Early chapter books')),
    ('middle_chapter', _('Middle chapter books')),
    ('upper_chapter', _('Upper chapter books')),
]

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_TEACHER)
    def __str__(self): return f'{self.user.username}:{self.role}'

class Classroom(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    grade = models.PositiveSmallIntegerField(choices=grade_choices, default=1, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: unique_together = [('owner','name')]
    def __str__(self): return self.name

class Student(models.Model):
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name='students')
    name = models.CharField(max_length=100)
    name_en = models.CharField(max_length=100, blank=True)
    email = models.EmailField(unique=True, null=True, blank=True)
    password_hash = models.CharField(max_length=128, blank=True)
    parent_1_name = models.CharField(max_length=100, blank=True)
    parent_2_name = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: unique_together = [('classroom','name')]
    def __str__(self): return self.name
    def set_password(self, raw): self.password_hash = make_password(raw)
    def check_password(self, raw): return bool(self.password_hash) and hash_check(raw, self.password_hash)
    @property
    def has_credentials(self): return bool(self.email and self.password_hash)

class Book(models.Model):
    source_id = models.CharField(max_length=120, unique=True)
    title = models.CharField(max_length=300)
    author = models.CharField(max_length=200, blank=True)
    series = models.CharField(max_length=200, db_index=True)
    level = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=20, default='book')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, blank=True, db_index=True)
    lexile = models.CharField(max_length=12, blank=True)
    atos = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    synopsis = models.TextField(blank=True)
    words = models.PositiveIntegerField(null=True, blank=True)
    source = models.URLField(max_length=500, blank=True)
    cover = models.URLField(max_length=500, blank=True)
    quiz_data = models.JSONField(default=list, blank=True)
    def __str__(self): return self.title

class ReadingRecord(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='records')
    book = models.ForeignKey(Book, on_delete=models.PROTECT)
    read_date = models.DateField()
    words = models.PositiveIntegerField(default=0)
    minutes = models.PositiveIntegerField(null=True, blank=True)
    quiz_score = models.PositiveSmallIntegerField(null=True, blank=True)
    passed = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['-read_date','-created_at']

class ClassGoal(models.Model):
    classroom = models.OneToOneField(Classroom, on_delete=models.CASCADE, related_name='goal')
    words = models.PositiveIntegerField()
    deadline = models.DateField(null=True, blank=True)

class QuizAttempt(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    score = models.PositiveSmallIntegerField()
    passed = models.BooleanField()
    submitted = models.BooleanField(default=False)
    answers = models.JSONField(default=list)
    questions = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['-completed_at']
