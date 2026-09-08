@echo off
cd /d "%~dp0"
start "Reading Garden Django" http://127.0.0.1:8000
.venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
