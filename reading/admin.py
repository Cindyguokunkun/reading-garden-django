from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from .models import (AccountAudit, Book, ClassGoal, Classroom, ParentStudentLink, Profile,
                     QuizAttempt, ReadingRecord, Student, TeacherInvite)

class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False

class ProfileUserAdmin(UserAdmin):
    inlines = [ProfileInline]

admin.site.unregister(User)
admin.site.register(User, ProfileUserAdmin)

@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ['title','author','series','category','lexile','atos','words']
    list_filter = ['category','series']
    search_fields = ['title','author','series']

admin.site.register([Classroom, Student, ReadingRecord, ClassGoal, QuizAttempt,
                     TeacherInvite, ParentStudentLink])
admin.site.register(AccountAudit)
