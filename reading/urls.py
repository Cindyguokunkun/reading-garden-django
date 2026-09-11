from django.urls import path
from . import views
from .views import auth, library, manage, ranks

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('action/', views.action, name='action'),
    path('quiz/', views.quiz_start, name='quiz_start'),
    path('quiz/<int:attempt_id>/', views.quiz_take, name='quiz_take'),
    path('export.xlsx', views.export_excel, name='export_excel'),
    path('ranks/', ranks.ranks, name='ranks'),
    path('library/', library.library, name='library'),
    path('library/add/', library.book_add, name='book_add'),
    path('library/<int:pk>/edit/', library.book_edit, name='book_edit'),
    path('manage/import/', manage.manage_import, name='manage_import'),
    path('login/', auth.login_hub, name='login'),
    path('login/staff/', auth.staff_login, name='staff_login'),
    path('logout/', auth.logout, name='logout'),
    path('student/login/', auth.student_login, name='student_login'),
    path('student/login/<int:classroom_id>/', auth.student_pick, name='student_pick'),
    path('student/', auth.student_home, name='student_home'),
    path('parent/login/', auth.parent_login, name='parent_login'),
    path('parent/', auth.parent_home, name='parent_home'),
]
