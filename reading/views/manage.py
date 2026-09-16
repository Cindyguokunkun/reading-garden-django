"""管理员批量导入视图。

本模块供管理员通过上传 Excel（.xlsx）批量创建教师账号、班级与学生，
并提供标准导入模板下载。所有导入均采用「先全量校验、再事务写入」的策略：
任意一行出错则整体不写库，并将逐行错误回显给用户。
"""

from io import BytesIO
from django.utils.translation import gettext as _
from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from openpyxl import Workbook, load_workbook
from ..models import AccountAudit, Classroom, Membership, Profile, Student, ROLE_TEACHER
from ..personas import get_persona, manager_required

# 学生导入模板与解析共用的列顺序表头（中英对照）。
HEADERS = ['学号 Student ID', '年级 Grade', '班级名称 Class', '教师用户名 Teacher username',
           '中文姓名 Name', '英文名 English name', '密码 Password']

# 教师导入模板与解析共用的列顺序表头（中英对照）。
TEACHER_HEADERS = ['教师姓名 Teacher name']

# 批量导入使用的默认初始密码：教师登录密码、家长登录密码。
TEACHER_DEFAULT_PASSWORD = '000000'
PARENT_DEFAULT_PASSWORD = 'P000000'


def _xlsx_response(rows, filename):
    """将二维行数据打包为 .xlsx 文件并构造下载响应。

    Args:
        rows (list[list]): 需要写入表格的行数据，首个元素通常为表头。
        filename (str): 下载时使用的文件名。

    Returns:
        HttpResponse: 内容为 xlsx 二进制、附带 ``Content-Disposition``
        下载头的响应。
    """
    wb = Workbook(); ws = wb.active; ws.title = 'import'
    for row in rows: ws.append(row)
    out = BytesIO(); wb.save(out)
    response = HttpResponse(out.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _clean(value):
    """将单元格值规整为去除首尾空白的字符串。

    Args:
        value: 单元格原始值，可能为 ``None`` 或任意类型。

    Returns:
        str: 转为字符串并 strip 后的结果；``None`` 归一化为空字符串。
    """
    return str(value).strip() if value is not None else ''


def _unique_username(base, taken):
    """为教师用户名生成全局唯一的取值，重名时追加数字后缀。

    用户名默认等于教师姓名；为使批量导入不因重名失败，若 ``王小明``
    已被占用，则依次尝试 ``王小明2``、``王小明3``……直到空闲。

    Args:
        base (str): 期望使用的用户名（通常等于教师姓名）。
        taken (set): 本次导入中已占用用户名（小写）的集合，会被就地更新，
            用于同一批表格内的去重。

    Returns:
        str: 全局唯一、可安全用于创建账号的用户名。
    """
    name = base; suffix = 2
    while name.lower() in taken or User.objects.filter(username__iexact=name).exists():
        name = f'{base}{suffix}'; suffix += 1
    taken.add(name.lower())
    return name


def _unique_student_name(base, exclude_pk=None, taken=None):
    """为学生姓名生成全局唯一的取值，重名时追加数字后缀。

    为使姓名也能安全地用作登录名（学生与家长均可用姓名登录），导入时
    保证姓名在全体学生中唯一：若 ``张三`` 已被占用，则依次尝试
    ``张三2``、``张三3``……直到空闲。

    Args:
        base (str): 期望使用的原始姓名。
        exclude_pk (int | None): 需要排除的学生主键。更新已有学生时传入其
            主键，避免与自身姓名冲突。
        taken (set | None): 本次导入中已占用姓名（小写）的集合，会被就地
            更新，用于同一批表格内的去重。

    Returns:
        str: 全局唯一、可用于登录的姓名。
    """
    if taken is None: taken = set()
    others = Student.objects.all() if exclude_pk is None else Student.objects.exclude(pk=exclude_pk)
    name = base; suffix = 2
    while name.lower() in taken or others.filter(name__iexact=name).exists():
        name = f'{base}{suffix}'; suffix += 1
    taken.add(name.lower())
    return name


@manager_required
def manage_teachers(request):
    """管理员批量导入教师视图：下载模板或上传 Excel 批量创建教师账号。

    流程说明：

    - GET 且 ``template=1`` 时，返回带示例行的教师导入模板下载。
    - POST 时读取上传的工作簿（从第 2 行起为数据），每行只取第一列
      「教师姓名」。用户名等于姓名，重名时自动追加数字后缀（如
      ``王小明2``）；初始密码统一为 :data:`TEACHER_DEFAULT_PASSWORD`，
      教师登录后可在「修改密码」入口自行修改。
    - 仅当无任何错误且存在有效数据时，才在单个事务内创建账号：建为
      已审批、在职的教师，并只归属管理员所在学校；否则回显错误、不写库。

    Args:
        request (HttpRequest): 当前请求对象，需为管理员身份。
            GET 可选 ``template``；POST 需含上传文件 ``file``。

    Returns:
        HttpResponse: 模板下载响应，或渲染 ``reading/manage_teachers.html``
        的响应（携带 ``errors`` 错误列表、``created`` 已创建用户名列表、
        ``headers`` 表头与 ``default_password`` 初始密码）。
    """
    organization = get_persona(request).organization
    if request.GET.get('template') == '1':
        return _xlsx_response([TEACHER_HEADERS, ['王小明']], 'teacher-import-template.xlsx')
    errors = []; created = []
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
            prepared = []; taken = set()
            for number, row in enumerate(raw_rows, start=2):
                name = _clean((list(row) + [None])[0])
                if not name: continue
                if len(name) > 150:
                    errors.append(_('第 %s 行：') % number + _('教师姓名过长')); continue
                prepared.append({'name': name, 'username': _unique_username(name, taken)})
            if not errors and prepared:
                with transaction.atomic():
                    for p in prepared:
                        user = User.objects.create_user(
                            username=p['username'], password=TEACHER_DEFAULT_PASSWORD,
                            first_name=p['name'])
                        Profile.objects.create(user=user, role=ROLE_TEACHER, approved=True)
                        Membership.objects.get_or_create(
                            user=user, organization=organization,
                            defaults={'role': ROLE_TEACHER, 'active': True})
                        # 确保新建教师只归属管理员当前所在学校。
                        Membership.objects.filter(user=user).exclude(organization=organization).delete()
                        AccountAudit.objects.create(
                            actor=request.user, target=user, organization=organization,
                            action='batch_create_teacher', detail=p['username'])
                        created.append(p['username'])
            elif errors:
                prepared = []
    return render(request, 'reading/manage_teachers.html', {
        'errors': errors, 'created': created, 'headers': TEACHER_HEADERS,
        'default_password': TEACHER_DEFAULT_PASSWORD,
    })


@manager_required
def manage_import(request):
    """管理员批量导入视图：下载模板或上传 Excel 导入班级与学生。

    流程说明：

    - GET 且 ``template=1`` 时，返回带示例行的学生导入模板下载。
    - POST 时读取上传的工作簿（从第 2 行起为数据），按列顺序解析：
      学号、年级、班级名称、教师用户名、中文姓名、英文名、密码。
      逐行校验：学号与中文姓名必填、学号在表格内不可重复且不超过 24
      字符、年级为 1~12 的数字、教师用户名须存在且为本校在职教师、
      密码必填；中文姓名若与已有学生重名，则自动追加数字后缀（如
      ``张三2``）以保证全局唯一，从而姓名也可作为登录名使用。
    - 仅当无任何错误且存在有效数据时，才在单个事务内按学号创建或更新
      学生：写入班级、姓名、英文名与学生登录密码，并统一设置家长初始
      密码；否则回显所有错误、不写库。

    Args:
        request (HttpRequest): 当前请求对象，需为管理员身份。
            GET 可选 ``template``；POST 需含上传文件 ``file``。

    Returns:
        HttpResponse: 模板下载响应，或渲染 ``reading/manage_import.html``
        的响应（携带 ``errors`` 错误列表、``imported`` 成功条数、
        ``headers`` 表头与 ``parent_password`` 家长初始密码）。
    """
    organization = get_persona(request).organization
    if request.GET.get('template') == '1':
        return _xlsx_response(
            [HEADERS, ['S00001', 3, 'Y3C3', 'teacher', '王小明', 'Xiaoming Wang', 'read1234']],
            'student-import-template.xlsx')
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
            prepared = []; ids_in_file = set(); names_taken = set()
            for number, row in enumerate(raw_rows, start=2):
                cells = (list(row) + [None] * 7)[:7]
                if all(_clean(c) == '' for c in cells): continue
                login_id, grade_s, class_name, teacher_s, name, name_en, password = (_clean(c) for c in cells)
                row_errors = []
                if not login_id:
                    row_errors.append(_('缺少学号'))
                elif len(login_id) > 24:
                    row_errors.append(_('学号不能超过 24 个字符'))
                elif login_id.lower() in ids_in_file:
                    row_errors.append(_('学号 %s 在表格中重复') % login_id)
                else:
                    ids_in_file.add(login_id.lower())
                try:
                    grade = int(float(grade_s))
                    if not 1 <= grade <= 12: row_errors.append(_('年级必须在 1-12 之间'))
                except ValueError:
                    grade = None; row_errors.append(_('年级必须是数字（1-12）'))
                if not class_name: row_errors.append(_('缺少班级名称'))
                teacher = User.objects.filter(username=teacher_s).first() if teacher_s else None
                if not teacher: row_errors.append(_('教师用户名 %s 不存在') % (teacher_s or _('(空)')))
                elif not Membership.objects.filter(user=teacher, organization=organization,
                                                   role='teacher', active=True).exists():
                    row_errors.append(_('%s 不是本校老师账号') % teacher_s)
                if not name: row_errors.append(_('缺少学生姓名'))
                if not password: row_errors.append(_('缺少学生密码'))
                if row_errors:
                    errors.append(_('第 %s 行：') % number + '；'.join(row_errors))
                else:
                    existing = Student.objects.filter(login_id__iexact=login_id).first()
                    prepared.append({
                        'login_id': login_id, 'grade': grade, 'class_name': class_name, 'teacher': teacher,
                        'name': _unique_student_name(name, exclude_pk=existing.pk if existing else None,
                                                     taken=names_taken),
                        'name_en': name_en, 'password': password, 'existing': existing,
                    })
            if not errors and prepared:
                with transaction.atomic():
                    for p in prepared:
                        classroom, _created = Classroom.objects.get_or_create(
                            owner=p['teacher'], organization=organization, name=p['class_name'],
                            defaults={'grade': p['grade']})
                        if classroom.grade != p['grade']:
                            classroom.grade = p['grade']; classroom.save()
                        student = p['existing'] or Student()
                        student.classroom = classroom
                        student.login_id = p['login_id']
                        student.name = p['name']; student.name_en = p['name_en']
                        student.active = True
                        student.set_password(p['password'])
                        student.set_parent_password(PARENT_DEFAULT_PASSWORD)
                        student.save()
                imported = len(prepared)
            elif errors:
                prepared = []
    return render(request, 'reading/manage_import.html', {
        'errors': errors, 'imported': imported, 'headers': HEADERS,
        'parent_password': PARENT_DEFAULT_PASSWORD,
    })
