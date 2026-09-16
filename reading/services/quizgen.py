"""基于 AI 的阅读理解题目生成与校验服务。

本模块将书籍文本（上传文件、粘贴文本或简介）连同元信息组装为提示词，
调用兼容 OpenAI Chat Completions 接口的 AI 服务生成多选题草稿，并对
返回内容做健壮的 JSON 提取与严格的题目结构校验，最终产出可直接进入
题目编辑器的题目数据。AI 生成的题目一律视为草稿，需人工确认后再保存。
"""

import json
import re
from django.conf import settings
from django.utils.translation import gettext as _
from . import http

# 上传素材文件的大小上限（2 MB）。
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

# 允许上传的纯文本文件后缀。
TEXT_SUFFIXES = ('.txt', '.text')

# 题目选项数量与文本长度的校验边界。
MIN_OPTIONS = 2
MAX_OPTIONS = 6
MAX_PROMPT_CHARS = 1000
MAX_OPTION_CHARS = 300

# 系统提示词：约束模型只依据素材出题、输出严格的 JSON 结构、
# 使用比原文更简单的英文，并以字面理解题为主。
SYSTEM_PROMPT = (
    'You write multiple-choice reading-comprehension questions for children learning English. '
    'Reply with one JSON object and nothing else, shaped exactly {"questions": [...]}, where every element of "questions" '
    'is an object with exactly the keys '
    '"prompt" (string), "options" (array of exactly 4 strings) and "answer" (integer 0-3, the index of the correct option). '
    'No markdown, no code fences, no text before or after the JSON.\n'
    'Hard rules:\n'
    '1. Every question must be answerable from the material alone. Even if you know this book, never use a name, event or fact '
    'that is not in the material: the material may be an adaptation that differs from the original.\n'
    '2. Exactly 4 options, all different, all plausible, exactly one correct. Never offer "all of the above" or "none of the above".\n'
    '3. Write prompts and options in simpler English than the book: short sentences under 25 words, common vocabulary.\n'
    '4. Ask mostly literal comprehension questions and at most one inference question. No trick questions.')


class QuizGenError(Exception):
    """题目生成或校验失败的异常，消息文案可直接展示给用户。"""

    pass


def read_material(uploaded=None, pasted=''):
    """读取并规整出题素材文本，来源为上传文件或粘贴内容。

    上传文件须为纯文本后缀且不超过大小上限；解码优先按 utf-8-sig，
    失败时回退 gbk。最终按配置的字符上限截断。

    Args:
        uploaded: 上传的文件对象（含 ``name``、``size``、``read()``），
            为 ``None`` 时改用粘贴文本。
        pasted (str): 用户粘贴的素材文本，仅在无上传文件时使用。

    Returns:
        str: 去首尾空白、并截断到 ``settings.QUIZGEN_MATERIAL_CHARS``
        以内的素材文本。

    Raises:
        QuizGenError: 上传文件后缀不受支持，或超过 2 MB 大小上限。
    """
    if uploaded is not None:
        name = (getattr(uploaded, 'name', '') or '').lower()
        if name and not name.endswith(TEXT_SUFFIXES):
            raise QuizGenError(_('Upload a plain .txt file, or paste the text into the box.'))
        if getattr(uploaded, 'size', 0) > MAX_UPLOAD_BYTES:
            raise QuizGenError(_('That file is larger than 2 MB. Please upload a shorter text.'))
        raw = uploaded.read()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('gbk', 'replace')
    else:
        text = pasted or ''
    return text.strip()[:settings.QUIZGEN_MATERIAL_CHARS]


def build_messages(*, title, series='', atos=None, category_label='', words=None, material='', n=10):
    """组装发送给 AI 服务的对话消息（system + user）。

    将书籍标题、系列、难度等元信息拼为可读的抬头，并把素材作为唯一
    事实来源包裹其中，要求模型据此生成 ``n`` 道题。

    Args:
        title (str): 书名。
        series (str): 系列名，可为空。
        atos (Decimal | None): ATOS 难度值，可为空。
        category_label (str): 分类展示文本（来自 CATEGORY_CHOICES 的惰性翻译）。
        words (int | None): 字数，可为空。
        material (str): 出题素材正文。
        n (int): 期望生成的题目数量，默认 10。

    Returns:
        list[dict]: 含两条消息的列表——``{'role': 'system', ...}`` 与
        ``{'role': 'user', ...}``，可直接放入请求体的 ``messages``。
    """
    # category_label comes from CATEGORY_CHOICES, so it is a lazy translation proxy: join() needs a real str.
    level = ' / '.join(part for part in [
        f'ATOS {atos}' if atos is not None else '', str(category_label or ''), f'{words} words' if words else ''] if part)
    lines = [f'Title: {title}']
    if series: lines.append(f'Series: {series}')
    if level: lines.append(f'Level: {level}')
    lines.append('Material, the only source of truth:')
    lines.append('"""\n' + material + '\n"""')
    lines.append(f'Write {n} questions about this material. If the material cannot support {n} questions, return fewer rather than inventing content.')
    return [{'role': 'system', 'content': SYSTEM_PROMPT}, {'role': 'user', 'content': '\n'.join(lines)}]


def extract_json(text):
    """从 AI 返回的文本中尽力提取出 JSON 对象或数组。

    依次尝试：整体直接解析 → 解析 ``` 代码围栏内的内容 → 通过括号配对
    扫描定位首个可解析的 ``[...]`` 或 ``{...}`` 片段。以容忍模型夹带
    多余文本或markdown 的情况。

    Args:
        text (str | None): AI 返回的原始文本。

    Returns:
        Any | None: 解析出的 JSON 值（通常为 dict 或 list）；
        文本为空或所有尝试均失败时返回 ``None``。
    """
    text = (text or '').strip()
    if not text: return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    fenced = re.search(r'```[a-zA-Z]*\s*(.*?)```', text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except ValueError:
            pass
    for opener, closer in (('[', ']'), ('{', '}')):
        start = text.find(opener)
        while start != -1:
            depth, quoted, escaped = 0, False, False
            for index in range(start, len(text)):
                char = text[index]
                if quoted:
                    if escaped: escaped = False
                    elif char == '\\': escaped = True
                    elif char == '"': quoted = False
                elif char == '"': quoted = True
                elif char == opener: depth += 1
                elif char == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:index + 1])
                        except ValueError:
                            break
            start = text.find(opener, start + 1)
    return None


def _answer_index(value):
    """将模型给出的答案值归一化为整数索引。

    兼容 int、整数值的 float 与纯数字字符串；布尔值被显式排除
    （因 ``bool`` 是 ``int`` 子类，需先拦截）。

    Args:
        value: 原始答案值。

    Returns:
        int | None: 归一化后的答案索引；无法识别为整数时返回 ``None``。
    """
    if isinstance(value, bool): return None
    if isinstance(value, int): return value
    if isinstance(value, float): return int(value) if value.is_integer() else None
    if isinstance(value, str) and value.strip().isdecimal(): return int(value.strip())
    return None


def validate_questions(raw, *, min_questions=1, max_questions=12):
    """校验并规整 AI 返回的题目数据，产出可用题目与错误列表。

    兼容模型未按要求使用 ``questions`` 键的情况（回退取首个列表值）。
    逐题校验题干、选项数量/长度/去重与答案索引合法性，跳过不合规题目
    并记录对应错误；仅当可用题目数达到下限时才视为成功。

    Args:
        raw: 从 :func:`extract_json` 得到的原始结构，通常为 dict 或 list。
        min_questions (int): 可用题目数量下限，低于则整体判为失败，默认 1。
        max_questions (int): 最多采纳的题目数量，默认 12。

    Returns:
        tuple[list[dict], list[str]]: ``(questions, errors)``。``questions``
        为规整后的题目列表（每项含 ``prompt``、``options``、``answer``），
        失败时为空列表；``errors`` 为面向用户的错误文案列表。
    """
    if isinstance(raw, dict):
        # The model is asked for {"questions": [...]}, but it sometimes picks its own key for the array.
        raw = raw['questions'] if isinstance(raw.get('questions'), list) else next((value for value in raw.values() if isinstance(value, list)), None)
    if not isinstance(raw, list): return [], [_('The AI reply was not a list of questions.')]
    questions, errors = [], []
    for number, item in enumerate(raw[:max_questions], start=1):
        if not isinstance(item, dict):
            errors.append(_('Question %(number)s: not a question object.') % {'number': number}); continue
        prompt = item.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(_('Question %(number)s: the question text is empty.') % {'number': number}); continue
        if len(prompt.strip()) > MAX_PROMPT_CHARS:
            errors.append(_('Question %(number)s: the question text is too long.') % {'number': number}); continue
        options = item.get('options')
        if not isinstance(options, list) or not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
            errors.append(_('Question %(number)s: needs between 2 and 6 options.') % {'number': number}); continue
        choices = [option.strip() for option in options if isinstance(option, str) and option.strip()]
        if len(choices) != len(options) or any(len(choice) > MAX_OPTION_CHARS for choice in choices):
            errors.append(_('Question %(number)s: every option must be short text.') % {'number': number}); continue
        if len({choice.casefold() for choice in choices}) != len(choices):
            errors.append(_('Question %(number)s: two options are the same.') % {'number': number}); continue
        answer = _answer_index(item.get('answer'))
        if answer is None or not 0 <= answer < len(choices):
            errors.append(_('Question %(number)s: the correct answer is not one of the options.') % {'number': number}); continue
        questions.append({'prompt': prompt.strip(), 'options': choices, 'answer': answer})
    if len(questions) < min_questions:
        errors.append(_('Only %(count)s usable questions came back, at least %(minimum)s are needed.') % {'count': len(questions), 'minimum': min_questions})
        return [], errors
    return questions, errors


def _status_message(status):
    """将 AI 服务的 HTTP 错误状态码映射为用户可读的提示文案。

    Args:
        status (int | None): :class:`http.HttpError` 携带的 HTTP 状态码。

    Returns:
        str: 针对 401/403、429、404 等常见状态的本地化提示；
        其他状态返回通用失败文案。
    """
    if status in (401, 403): return _('The AI service refused the API key in .env.')
    if status == 429: return _('The AI service is busy and rate limited this request. Try again later.')
    if status == 404: return _('The AI service URL in .env does not exist. Check QUIZGEN_BASE_URL.')
    return _('The AI service could not answer this request.')


def generate_questions(*, title, series='', atos=None, category_label='', words=None, material='', n=None):
    """调用 AI 服务为书籍生成阅读理解题草稿。

    组装提示词、按配置发起 Chat Completions 请求，解析并校验返回内容。
    题目数量未指定时取配置默认值；启用 JSON 模式时会附加
    ``response_format`` 约束。

    Args:
        title (str): 书名。
        series (str): 系列名，可为空。
        atos (Decimal | None): ATOS 难度值，可为空。
        category_label (str): 分类展示文本。
        words (int | None): 字数，可为空。
        material (str): 出题素材正文，不可为空。
        n (int | None): 期望题目数量，``None`` 时取 ``settings.QUIZGEN_QUESTIONS``。

    Returns:
        list[dict]: 校验通过的题目列表，每项含 ``prompt``、``options`` 与
        ``answer``。

    Raises:
        QuizGenError: 未启用/未配置出题功能、素材为空、请求超时、
            服务返回错误状态、响应不可解析、无题目或可用题目不足下限时。
    """
    if not settings.QUIZGEN_ENABLED:
        raise QuizGenError(_('AI question generation is not set up yet. Add the keys to .env to use it.'))
    if not (material or '').strip():
        raise QuizGenError(_('Paste the book text or its synopsis first: AI questions need source material.'))
    count = n or settings.QUIZGEN_QUESTIONS
    payload = {'model': settings.QUIZGEN_MODEL, 'temperature': 0.2, 'max_tokens': 3000,
        'messages': build_messages(title=title, series=series, atos=atos, category_label=category_label, words=words, material=material, n=count)}
    if settings.QUIZGEN_JSON_MODE: payload['response_format'] = {'type': 'json_object'}
    opener = http.build_opener(settings.QUIZGEN_PROXY, cookies=False)
    url = settings.QUIZGEN_BASE_URL.rstrip('/') + '/chat/completions'
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + settings.QUIZGEN_API_KEY}
    try:
        _status, text, _final = http.fetch(opener, url, data=json.dumps(payload).encode(), headers=headers, timeout=settings.QUIZGEN_TIMEOUT)
    except http.HttpTimeout:
        raise QuizGenError(_('Generating the questions took too long. Try again, or ask for fewer questions in .env.')) from None
    except http.HttpError as error:
        raise QuizGenError(_status_message(error.status)) from None
    try:
        body = json.loads(text)
    except ValueError:
        raise QuizGenError(_('The AI service sent back an unreadable reply.')) from None
    choices = body.get('choices') if isinstance(body, dict) else None
    content = (choices[0].get('message') or {}).get('content') if choices else None
    if not isinstance(content, str) or not content.strip():
        raise QuizGenError(_('The AI service sent back no questions.'))
    questions, errors = validate_questions(extract_json(content), min_questions=settings.QUIZGEN_MIN_QUESTIONS)
    # errors[-1] is the generic "too few usable questions" count; the reasons before it are what the teacher can act on.
    if not questions:
        raise QuizGenError(' '.join(str(error) for error in errors[:3]) if errors else _('The AI service sent back no questions.'))
    return questions
