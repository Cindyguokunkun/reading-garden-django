from io import BytesIO
from django.utils.translation import gettext as _
from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from openpyxl import Workbook, load_workbook
from ..models import Classroom, Student
from ..personas import get_role, manager_required

HEADERS = ['年级 Grade', '班级名称 Class', '教师用户名 Teacher username', '学生姓名 Student name',
           '英文名 English name', '邮箱 Email', '初始密码 Initial password', '家长1 Parent 1', '家长2 Parent 2']

def _xlsx_response(rows, filename):
    wb = Workbook(); ws = wb.active; ws.title = 'import'
    for row in rows: ws.append(row)
    out = BytesIO(); wb.save(out)
    response = HttpResponse(out.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

def _clean(value):
    return str(value).strip() if value is not None else ''

@manager_required
def manage_import(request):
    if request.GET.get('template') == '1':
        return _xlsx_response([HEADERS, [3, 'Y3C3', 'teacher', '王小明', 'Xiaoming Wang', 'xm@example.com', 'read1234', '王妈妈', '']], 'import-template.xlsx')
    errors = []; imported = 0
    if request.method == 'POST':
        upload = request.FILES.get('file')
        if not upload:
            errors.append(_('请选择要导入的 Excel 文件'))
        else:
            try:
                ws = load_workbook(upload, read_only=True).active
                raw_rows = list(ws.iter_rows(min_row=2, values_only=True))
            except Exception:
                raw_rows = []; errors.append(_('无法读取 Excel 文件，请使用模板格式（.xlsx）'))
            prepared = []; emails_in_file = {}
            for number, row in enumerate(raw_rows, start=2):
                cells = (list(row) + [None] * 9)[:9]
                if all(_clean(c) == '' for c in cells): continue
                grade_s, class_name, teacher_s, name, name_en, email, password, p1, p2 = (_clean(c) for c in cells)
                row_errors = []
                try:
                    grade = int(float(grade_s))
                    if not 1 <= grade <= 12: row_errors.append(_('年级必须在 1-12 之间'))
                except ValueError:
                    grade = None; row_errors.append(_('年级必须是数字（1-12）'))
                if not class_name: row_errors.append(_('缺少班级名称'))
                teacher = User.objects.filter(username=teacher_s).first() if teacher_s else None
                if not teacher: row_errors.append(_('教师用户名 %s 不存在') % (teacher_s or _('(空)')))
                elif teacher.is_superuser or get_role(teacher) != 'teacher': row_errors.append(_('%s 不是老师账号') % teacher_s)
                if not name: row_errors.append(_('缺少学生姓名'))
                if email:
                    email = email.lower()
                    if '@' not in email: row_errors.append(_('邮箱格式不正确'))
                    if not password: row_errors.append(_('提供了邮箱就必须提供初始密码'))
                    holder = Student.objects.filter(email=email).exclude(classroom__name=class_name, name=name).first()
                    if holder or email in emails_in_file: row_errors.append(_('邮箱 %s 已被占用') % email)
                    emails_in_file[email] = number
                if row_errors:
                    errors.append(_('第 %s 行：') % number + '；'.join(row_errors))
                else:
                    prepared.append({'grade': grade, 'class_name': class_name, 'teacher': teacher, 'name': name,
                                     'name_en': name_en, 'email': email, 'password': password, 'p1': p1, 'p2': p2})
            if not errors and prepared:
                with transaction.atomic():
                    for p in prepared:
                        classroom, _created = Classroom.objects.get_or_create(owner=p['teacher'], name=p['class_name'], defaults={'grade': p['grade']})
                        if classroom.grade != p['grade']:
                            classroom.grade = p['grade']; classroom.save()
                        student, _created = Student.objects.get_or_create(classroom=classroom, name=p['name'])
                        student.name_en = p['name_en']; student.parent_1_name = p['p1']; student.parent_2_name = p['p2']
                        if p['email']:
                            student.email = p['email']; student.set_password(p['password'])
                        student.save()
                imported = len(prepared)
            elif errors:
                prepared = []
    return render(request, 'reading/manage_import.html', {'errors': errors, 'imported': imported, 'headers': HEADERS})
