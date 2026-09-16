"""批量为书籍生成阅读理解题库的管理命令。

本命令严格复用应用既有的出题逻辑——:func:`reading.services.quizgen.generate_questions`
（与 ``reading.views.library._generate`` 使用完全相同的提示词、参数与校验），
把每本书的「素材」交给 AI 服务生成题目草稿，校验通过后写入 ``Book.quiz_data``，
并同步回 ``library-data.json``（``seed_library`` 的数据源，避免下次播种时被覆盖清空）。

素材来源优先级与既有逻辑保持一致：

1. ``--material-dir`` 目录下与书籍匹配的 ``.txt`` 文件（按 ``source_id`` 或
   归一化书名匹配），对应书籍表单里「上传/粘贴正文」的通道；
2. 书籍自身的 ``synopsis`` 简介，对应 ``_generate`` 中 ``or book.synopsis`` 的回退。

若某本书两者皆无，则**跳过**该书并记录原因——绝不凭空编造题目，这与
``quizgen`` 系统提示词「只能依据素材出题、不得杜撰」的硬性规则一致。
"""

import json
import re
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from reading.models import CATEGORY_CHOICES, Book
from reading.services import quizgen

# 生成结果默认回写的题库数据文件（seed_library 的读取来源）。
LIBRARY_DATA = 'library-data.json'


def _slug(title):
    """将书名归一化为仅含小写字母与数字的键，用于匹配素材文件。

    与 ``reading.views.library._slug`` 行为一致，保证同一本书在表单与
    本命令中匹配到相同的素材文件命名。

    Args:
        title (str | None): 原始书名。

    Returns:
        str: 去除所有非字母数字字符并转小写后的结果。
    """
    return re.sub(r'[^a-z0-9]', '', (title or '').lower())


def _read_text(path):
    """按项目既有约定读取纯文本素材文件。

    解码优先 ``utf-8-sig``，失败时回退 ``gbk``，与
    :func:`reading.services.quizgen.read_material` 的解码策略保持一致。

    Args:
        path (Path): 待读取的 ``.txt`` 文件路径。

    Returns:
        str: 去首尾空白后的文件文本内容。
    """
    raw = path.read_bytes()
    try:
        return raw.decode('utf-8-sig').strip()
    except UnicodeDecodeError:
        return raw.decode('gbk', 'replace').strip()


def _find_material_file(material_dir, book):
    """在素材目录中查找与书籍对应的 ``.txt`` 文件。

    依次尝试以 ``source_id`` 与归一化书名命名的文件，命中即返回。

    Args:
        material_dir (Path | None): 素材目录；为 ``None`` 时不查找。
        book (Book): 目标书籍。

    Returns:
        Path | None: 命中的素材文件路径；未配置目录或未找到时返回 ``None``。
    """
    if not material_dir:
        return None
    for stem in (book.source_id, _slug(book.title)):
        if not stem:
            continue
        candidate = material_dir / f'{stem}.txt'
        if candidate.is_file():
            return candidate
    return None


class Command(BaseCommand):
    """``python manage.py generate_quizzes``：批量生成题库。"""

    help = 'Generate reading-comprehension quizzes for books via the existing AI quizgen logic (never fabricates without material).'

    def add_arguments(self, parser):
        """声明命令行参数。

        Args:
            parser (ArgumentParser): 命令的参数解析器。
        """
        parser.add_argument('--only-missing', action='store_true',
                            help='Only process books whose quiz_data is still empty.')
        parser.add_argument('--source-id', default='',
                            help='Process a single book by its source_id.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Stop after processing at most N books (0 = no limit).')
        parser.add_argument('--material-dir', default='',
                            help='Directory of .txt story texts, named "<source_id>.txt" or "<slugified title>.txt".')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would happen without calling the AI service or saving anything.')
        parser.add_argument('--no-json', action='store_true',
                            help='Do not write generated quizzes back into library-data.json (DB only).')

    def handle(self, *args, **options):
        """执行批量出题。

        按参数筛出目标书籍，逐本取素材并调用既有出题逻辑；无素材者跳过、
        出错者记录并继续，最终打印统计摘要。

        Args:
            *args: 位置参数（未使用）。
            **options (dict): 由 :meth:`add_arguments` 解析出的命令选项。
        """
        if not settings.QUIZGEN_ENABLED:
            self.stderr.write(self.style.ERROR(
                'AI question generation is not configured. Set QUIZGEN_BASE_URL / QUIZGEN_API_KEY / QUIZGEN_MODEL in .env first.'))
            return

        material_dir = Path(options['material_dir']).expanduser() if options['material_dir'] else None
        if material_dir and not material_dir.is_dir():
            self.stderr.write(self.style.ERROR(f'--material-dir is not a directory: {material_dir}'))
            return

        books = Book.objects.all().order_by('series', 'series_order', 'title')
        if options['source_id']:
            books = books.filter(source_id=options['source_id'])
        elif options['only_missing']:
            books = books.filter(quiz_data=[])
        if options['limit']:
            books = books[:options['limit']]

        json_path = Path(settings.BASE_DIR) / LIBRARY_DATA
        data = json.loads(json_path.read_text(encoding='utf-8-sig')) if (json_path.is_file() and not options['no_json']) else None

        generated = skipped = failed = 0
        for book in books:
            material_file = _find_material_file(material_dir, book)
            material = _read_text(material_file) if material_file else (book.synopsis or '').strip()
            if not material:
                skipped += 1
                self.stdout.write(f'SKIP  {book.source_id} | {book.title} (no material: add a synopsis or a .txt in --material-dir)')
                continue
            if options['dry_run']:
                generated += 1
                origin = material_file.name if material_file else 'synopsis'
                self.stdout.write(f'WOULD GENERATE  {book.source_id} | {book.title} (material: {origin}, {len(material)} chars)')
                continue
            try:
                questions = quizgen.generate_questions(
                    title=book.title, series=book.series, atos=book.atos, words=book.words,
                    category_label=dict(CATEGORY_CHOICES).get(book.category, ''), material=material)
            except quizgen.QuizGenError as error:
                failed += 1
                self.stdout.write(self.style.WARNING(f'FAIL  {book.source_id} | {book.title}: {error}'))
                continue
            book.quiz_data = questions
            book.save(update_fields=['quiz_data'])
            if data is not None:
                data.setdefault('quizzes', {})[book.title] = questions
            generated += 1
            self.stdout.write(self.style.SUCCESS(f'OK    {book.source_id} | {book.title}: {len(questions)} questions'))

        if data is not None and generated and not options['dry_run']:
            json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            self.stdout.write(f'Wrote generated quizzes back to {LIBRARY_DATA}.')

        self.stdout.write(self.style.SUCCESS(
            f'Done. {"Would generate" if options["dry_run"] else "Generated"}: {generated}; '
            f'skipped (no material): {skipped}; failed: {failed}.'))
