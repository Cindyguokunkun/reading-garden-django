from django.urls import path
from . import views
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('action/', views.action, name='action'),
    path('quiz/', views.quiz_start, name='quiz_start'),
    path('quiz/<int:attempt_id>/', views.quiz_take, name='quiz_take'),
    path('export.xlsx', views.export_excel, name='export_excel'),
]
