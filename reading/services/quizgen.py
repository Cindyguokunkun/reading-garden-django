import json
import re
from django.conf import settings
from django.utils.translation import gettext as _
from . import http

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
TEXT_SUFFIXES = ('.txt', '.text')
MIN_OPTIONS = 2
MAX_OPTIONS = 6
MAX_PROMPT_CHARS = 1000
MAX_OPTION_CHARS = 300

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
    pass

def read_material(uploaded=None, pasted=''):
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
    if isinstance(value, bool): return None
    if isinstance(value, int): return value
    if isinstance(value, float): return int(value) if value.is_integer() else None
    if isinstance(value, str) and value.strip().isdecimal(): return int(value.strip())
    return None

def validate_questions(raw, *, min_questions=1, max_questions=12):
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
    if status in (401, 403): return _('The AI service refused the API key in .env.')
    if status == 429: return _('The AI service is busy and rate limited this request. Try again later.')
    if status == 404: return _('The AI service URL in .env does not exist. Check QUIZGEN_BASE_URL.')
    return _('The AI service could not answer this request.')

def generate_questions(*, title, series='', atos=None, category_label='', words=None, material='', n=None):
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
