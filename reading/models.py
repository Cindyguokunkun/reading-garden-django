from django.contrib.auth.models import User
from django.db import models

class Classroom(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: unique_together = [('owner','name')]
    def __str__(self): return self.name

class Student(models.Model):
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name='students')
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: unique_together = [('classroom','name')]
    def __str__(self): return self.name

class Book(models.Model):
    source_id = models.CharField(max_length=120, unique=True)
    title = models.CharField(max_length=300)
    series = models.CharField(max_length=200, db_index=True)
    level = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=20, default='book')
    words = models.PositiveIntegerField(null=True, blank=True)
    source = models.URLField(max_length=500, blank=True)
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

class Goal(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    words = models.PositiveIntegerField()
    class Meta: unique_together = [('owner','words')]

class QuizAttempt(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    score = models.PositiveSmallIntegerField()
    passed = models.BooleanField()
    answers = models.JSONField(default=list)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['-completed_at']
