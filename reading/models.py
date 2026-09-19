"""阅见（Reading Garden）应用的 Django 数据模型定义。

本模块集中定义了阅读打卡系统的核心数据库模型，涵盖用户角色、班级、
学生、书籍、阅读记录、班级目标、测验记录以及书架收藏等业务实体。

模块同时提供若干用于表单/模型 choices 字段的常量与辅助函数，
所有面向用户展示的文本均通过 ``gettext_lazy`` 支持国际化翻译。

Typical usage example::

    from reading.models import Student, Classroom

    classroom = Classroom.objects.create(owner=user, name='三班', grade=3)
    student = Student.objects.create(classroom=classroom, name='小明')
"""

from django.contrib.auth.hashers import check_password as hash_check, make_password
from django.contrib.auth.models import User
from django.db import models
from django.utils.translation import gettext_lazy as _

# 系统内置的员工角色标识。
ROLE_TEACHER = 'teacher'
ROLE_MANAGER = 'manager'
ROLE_PARENT = 'parent'

# Profile.role 字段的可选值，(存储值, 展示文本) 元组列表。
ROLE_CHOICES = [
    (ROLE_TEACHER, _('Teacher')),
    (ROLE_MANAGER, _('Manager')),
    (ROLE_PARENT, _('Parent')),
]


def grade_choices():
    """构建年级字段的可选项列表。

    返回 1~12 年级的 ``(值, 展示文本)`` 元组，展示文本已国际化，
    用作 :class:`Classroom` 模型 ``grade`` 字段的 ``choices``。

    Returns:
        list[tuple[int, str]]: 形如 ``[(1, '一年级'), ..., (12, '十二年级')]``
        的选项列表，其中文本随当前语言环境翻译。
    """
    return [(g, _('Grade %(g)s') % {'g': g}) for g in range(1, 13)]


# Book.category 字段的可选值，按阅读难度分级归类书籍。
CATEGORY_CHOICES = [
    ('graded', _('Graded readers')),
    ('bridge', _('Bridge books')),
    ('early_chapter', _('Early chapter books')),
    ('middle_chapter', _('Middle chapter books')),
    ('upper_chapter', _('Upper chapter books')),
]


class Organization(models.Model):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=80, unique=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Membership(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='memberships')
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=10, choices=[
        (ROLE_TEACHER, _('Teacher')), (ROLE_MANAGER, _('Manager')),
    ], default=ROLE_TEACHER)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'organization')]


class Profile(models.Model):
    """员工账号的扩展信息，与 Django 内置 User 一对一关联。

    用于区分登录后台的用户角色（教师或管理员）。

    Attributes:
        user (models.OneToOneField): 关联的 Django 认证用户，
            可通过 ``user.profile`` 反向访问。
        role (models.CharField): 角色标识，取值见 :data:`ROLE_CHOICES`，
            默认为教师。
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_TEACHER)
    approved = models.BooleanField(default=True)
    name_en = models.CharField(max_length=100, blank=True)

    def __str__(self):
        """返回便于调试与后台展示的字符串表示。

        Returns:
            str: 形如 ``'username:role'`` 的字符串。
        """
        return f'{self.user.username}:{self.role}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.approved and self.role in (ROLE_TEACHER, ROLE_MANAGER):
            organization = Organization.objects.first()
            if organization:
                Membership.objects.get_or_create(
                    user=self.user, organization=organization,
                    defaults={'role': self.role, 'active': self.user.is_active},
                )


class Classroom(models.Model):
    """班级模型，归属于某位员工用户。

    Attributes:
        owner (models.ForeignKey): 班级的所属者（教师/管理员用户）。
        name (models.CharField): 班级名称，同一 owner 下不可重复。
        grade (models.PositiveSmallIntegerField): 年级，取值 1~12，
            已建立数据库索引。
        created_at (models.DateTimeField): 创建时间，自动填充。
    """

    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='classrooms', null=True)
    name = models.CharField(max_length=100)
    grade = models.PositiveSmallIntegerField(choices=grade_choices, default=1, db_index=True)
    section = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """模型元数据。

        Attributes:
            unique_together (list): 约束 (owner, name) 组合唯一，
                即同一用户下班级名称不可重复。
        """

        unique_together = [('owner', 'name')]

    def __str__(self):
        """返回班级名称作为字符串表示。

        Returns:
            str: 班级名称。
        """
        return self.name

    def save(self, *args, **kwargs):
        if not self.organization_id:
            membership = Membership.objects.filter(user=self.owner, active=True).first()
            self.organization = membership.organization if membership else Organization.objects.first()
        if self.section:
            self.name = f'Y{self.grade}C{self.section}'
        super().save(*args, **kwargs)


class Student(models.Model):
    """学生模型，归属于某个班级。

    学生使用独立的邮箱+密码登录体系（区别于 Django 内置用户），
    密码以哈希形式存储。

    Attributes:
        classroom (models.ForeignKey): 学生所属班级，
            可通过 ``classroom.students`` 反向访问。
        name (models.CharField): 学生姓名，同一班级下不可重复。
        name_en (models.CharField): 英文名，可为空。
        email (models.EmailField): 登录邮箱，全局唯一，可为空。
        password_hash (models.CharField): 学生登录密码哈希值，明文密码不入库。
        parent_password_hash (models.CharField): 家长登录密码哈希值，
            与学生密码相互独立（家长用「学生姓名 + 家长密码」登录），
            明文密码不入库。
        parent_1_name (models.CharField): 家长一姓名，可为空。
        parent_2_name (models.CharField): 家长二姓名，可为空。
        created_at (models.DateTimeField): 创建时间，自动填充。
    """

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name='students')
    name = models.CharField(max_length=100)
    name_en = models.CharField(max_length=100, blank=True)
    email = models.EmailField(unique=True, null=True, blank=True)
    password_hash = models.CharField(max_length=128, blank=True)
    parent_password_hash = models.CharField(max_length=128, blank=True)
    login_id = models.CharField(max_length=24, unique=True, null=True, blank=True)
    bind_code = models.CharField(max_length=12, unique=True, null=True, blank=True)
    active = models.BooleanField(default=True, db_index=True)
    parent_1_name = models.CharField(max_length=100, blank=True)
    parent_2_name = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """模型元数据。

        Attributes:
            unique_together (list): 约束 (classroom, name) 组合唯一，
                即同一班级内学生姓名不可重复。
        """

        unique_together = [('classroom', 'name')]

    def __str__(self):
        """返回学生姓名作为字符串表示。

        Returns:
            str: 学生姓名。
        """
        return self.name

    def set_password(self, raw):
        """将明文密码哈希后保存到 ``password_hash`` 字段。

        注意：本方法只修改内存中的实例，不会自动写库，
        调用后需执行 ``save()`` 才会持久化。

        Args:
            raw (str): 用户输入的明文密码。
        """
        self.password_hash = make_password(raw)

    def check_password(self, raw):
        """校验明文密码是否与已存储的学生密码哈希匹配。

        Args:
            raw (str): 待校验的明文密码。

        Returns:
            bool: 当已设置密码哈希且与 ``raw`` 匹配时返回 ``True``，
            否则返回 ``False``。
        """
        return bool(self.password_hash) and hash_check(raw, self.password_hash)

    def set_parent_password(self, raw):
        """将家长登录的明文密码哈希后保存到 ``parent_password_hash`` 字段。

        与学生自身的 :meth:`set_password` 相互独立，用于「学生姓名 +
        家长密码」的家长登录通道。

        注意：本方法只修改内存中的实例，不会自动写库，
        调用后需执行 ``save()`` 才会持久化。

        Args:
            raw (str): 家长输入的明文密码。
        """
        self.parent_password_hash = make_password(raw)

    def check_parent_password(self, raw):
        """校验明文密码是否与已存储的家长密码哈希匹配。

        Args:
            raw (str): 待校验的明文密码。

        Returns:
            bool: 当已设置家长密码哈希且与 ``raw`` 匹配时返回 ``True``，
            否则返回 ``False``。
        """
        return bool(self.parent_password_hash) and hash_check(raw, self.parent_password_hash)

    @property
    def has_credentials(self):
        """判断该学生是否已具备独立登录凭证。

        Returns:
            bool: 当邮箱与密码哈希均已设置时返回 ``True``。
        """
        return bool(self.login_id and self.password_hash)


class TeacherInvite(models.Model):
    code = models.CharField(max_length=24, unique=True)
    label = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='teacher_invites')
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='teacher_invites', null=True)
    active = models.BooleanField(default=True)
    max_uses = models.PositiveIntegerField(default=20)
    uses = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def available(self):
        return self.active and self.uses < self.max_uses

    def save(self, *args, **kwargs):
        if not self.organization_id:
            membership = Membership.objects.filter(user=self.created_by, active=True).first()
            self.organization = membership.organization if membership else Organization.objects.first()
        super().save(*args, **kwargs)


class ParentStudentLink(models.Model):
    parent = models.ForeignKey(User, on_delete=models.CASCADE, related_name='student_links')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='parent_links')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('parent', 'student')]


class AccountAudit(models.Model):
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='account_actions')
    target = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='account_changes')
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='account_audits', null=True)
    action = models.CharField(max_length=40)
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class Book(models.Model):
    """书籍模型，是阅读与测验的核心内容载体。

    书籍信息可能来自外部数据源（如 AR BookFinder），
    ``source_id`` 用于去重与溯源。

    Attributes:
        source_id (models.CharField): 数据源唯一标识，全局唯一。
        title (models.CharField): 书名。
        author (models.CharField): 作者，可为空。
        series (models.CharField): 所属系列，已建立索引。
        level (models.CharField): 分级标识，可为空。
        kind (models.CharField): 内容类型，默认为 ``'book'``。
        category (models.CharField): 书籍分类，取值见
            :data:`CATEGORY_CHOICES`，可为空且已建立索引。
        lexile (models.CharField): 蓝思值（Lexile），可为空。
        atos (models.DecimalField): ATOS 阅读难度值，可为空。
        synopsis (models.TextField): 内容简介，可为空。
        words (models.PositiveIntegerField): 总字数，可为空。
        source (models.URLField): 原始数据来源链接，可为空。
        cover (models.URLField): 封面图片链接，可为空。
        quiz_data (models.JSONField): 测验题目数据，默认为空列表。
    """

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='books', null=True, blank=True)
    source_id = models.CharField(max_length=120, unique=True)
    title = models.CharField(max_length=300)
    author = models.CharField(max_length=200, blank=True)
    series = models.CharField(max_length=200, db_index=True)
    series_order = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
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

    def __str__(self):
        """返回书名作为字符串表示。

        Returns:
            str: 书名。
        """
        return self.title


class ReadingRecord(models.Model):
    """阅读记录，记录学生阅读某本书的打卡与测验结果。

    Attributes:
        student (models.ForeignKey): 关联学生，
            可通过 ``student.records`` 反向访问。
        book (models.ForeignKey): 关联书籍，使用 PROTECT 删除策略，
            即存在阅读记录时书籍不可被删除。
        read_date (models.DateField): 阅读日期。
        words (models.PositiveIntegerField): 本次阅读字数，默认 0。
        minutes (models.PositiveIntegerField): 阅读时长（分钟），可为空。
        quiz_score (models.PositiveSmallIntegerField): 测验得分，可为空。
        passed (models.BooleanField): 是否通过，默认 ``True``。
        created_at (models.DateTimeField): 创建时间，自动填充。
    """

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='records')
    book = models.ForeignKey(Book, on_delete=models.PROTECT)
    read_date = models.DateField()
    words = models.PositiveIntegerField(default=0)
    minutes = models.PositiveIntegerField(null=True, blank=True)
    quiz_score = models.PositiveSmallIntegerField(null=True, blank=True)
    passed = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """模型元数据。

        Attributes:
            ordering (list): 默认排序，先按阅读日期、再按创建时间倒序，
                使最新记录排在最前。
        """

        ordering = ['-read_date', '-created_at']


class ClassGoal(models.Model):
    """班级阅读目标，与班级一对一关联。

    Attributes:
        classroom (models.OneToOneField): 关联班级，
            可通过 ``classroom.goal`` 反向访问。
        words (models.PositiveIntegerField): 目标阅读总字数。
        deadline (models.DateField): 目标截止日期，可为空。
    """

    classroom = models.OneToOneField(Classroom, on_delete=models.CASCADE, related_name='goal')
    words = models.PositiveIntegerField()
    deadline = models.DateField(null=True, blank=True)


class StudentGoal(models.Model):
    """A student's personal cumulative word target."""

    student = models.OneToOneField(Student, on_delete=models.CASCADE, related_name='personal_goal')
    words = models.PositiveIntegerField()
    updated_at = models.DateTimeField(auto_now=True)


class QuizAttempt(models.Model):
    """测验作答记录，保存学生对某本书的一次测验过程与结果。

    Attributes:
        student (models.ForeignKey): 作答学生。
        book (models.ForeignKey): 测验对应的书籍。
        score (models.PositiveSmallIntegerField): 得分。
        passed (models.BooleanField): 是否通过。
        submitted (models.BooleanField): 是否已提交，默认 ``False``。
        answers (models.JSONField): 学生提交的答案，默认为空列表。
        questions (models.JSONField): 本次测验的题目快照，默认为空列表。
        started_at (models.DateTimeField): 开始作答时间。
        completed_at (models.DateTimeField): 完成时间，自动填充。
    """

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    score = models.PositiveSmallIntegerField()
    passed = models.BooleanField()
    submitted = models.BooleanField(default=False)
    answers = models.JSONField(default=list)
    questions = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """模型元数据。

        Attributes:
            ordering (list): 默认按完成时间倒序排列。
        """

        ordering = ['-completed_at']


class ShelfItem(models.Model):
    """书架收藏项，记录学生收藏的书籍。

    Attributes:
        student (models.ForeignKey): 关联学生，
            可通过 ``student.shelf`` 反向访问。
        book (models.ForeignKey): 被收藏的书籍。
        created_at (models.DateTimeField): 收藏时间，自动填充。
    """

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='shelf')
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """模型元数据。

        Attributes:
            unique_together (list): 约束 (student, book) 组合唯一，
                即同一学生不可重复收藏同一本书。
            ordering (list): 默认按收藏时间倒序排列。
        """

        unique_together = [('student', 'book')]
        ordering = ['-created_at']
