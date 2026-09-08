from django.contrib import admin
from .models import Book, Classroom, Goal, QuizAttempt, ReadingRecord, Student
admin.site.register([Book, Classroom, Student, ReadingRecord, Goal, QuizAttempt])
