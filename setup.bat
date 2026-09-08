@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe python -m venv .venv
.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
.venv\Scripts\python.exe manage.py makemigrations reading
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py seed_library
.venv\Scripts\python.exe manage.py shell -c "from django.contrib.auth.models import User; User.objects.filter(username='teacher').exists() or User.objects.create_superuser('teacher','','reading123')"
echo Setup complete. Username: teacher  Password: reading123
pause
