"""Check the hand-written catalogs against the msgids Django looks up at runtime.

Windows has no gettext tools here, so locale/*/LC_MESSAGES/django.po is edited by
hand and compiled with scripts/po2mo.py. Two runtime rules are easy to get wrong
by hand, and both fail silently by falling back to the msgid:

* ``{% trans %}`` and ``{% blocktrans %}`` double literal percent signs before the
  catalog lookup, so a template message containing ``%`` must be catalogued as ``%%``.
* ``{% blocktrans %}`` placeholders are ``%(name)s`` in the msgid, never ``{{ name }}``.

Run: .venv/Scripts/python.exe scripts/check_i18n.py
Exits 1 when a message is missing from a catalog or an entry is never looked up.
"""
import os
import re
import sys
from pathlib import Path

import django

BASE_DIR = Path(__file__).resolve().parent.parent
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, str(BASE_DIR))
django.setup()

from django.template import Template
from django.templatetags.i18n import BlockTranslateNode, TranslateNode

CATALOGS = {
    'en': BASE_DIR / 'locale/en/LC_MESSAGES/django.po',
    'zh_Hans': BASE_DIR / 'locale/zh_Hans/LC_MESSAGES/django.po',
}


def template_msgids(path):
    """Runtime msgids for every trans/blocktrans in a template."""
    try:
        nodelist = Template(path.read_text(encoding='utf-8')).nodelist
    except Exception as exc:
        return [], [f'{path.name}: {exc}']
    ids = []
    for node in nodelist.get_nodes_by_type((TranslateNode, BlockTranslateNode)):
        if isinstance(node, TranslateNode):
            var = node.filter_expression.var
            ids.append(str(getattr(var, 'literal', var)).replace('%', '%%'))
        else:
            ids.append(node.render_token_list(node.singular)[0])
            if node.plural:
                ids.append(node.render_token_list(node.plural)[0])
    return ids, []


def python_msgids():
    """English msgids wrapped in gettext calls in Python source."""
    ids = set()
    pattern = r"""(?:gettext(?:_lazy)?|_)\(\s*['"](.+?)['"]\s*\)"""
    for path in BASE_DIR.glob('reading/**/*.py'):
        ids |= set(re.findall(pattern, path.read_text(encoding='utf-8')))
    return ids


def catalog_msgids(path):
    return set(re.findall(r'^msgid "(.*)"$', path.read_text(encoding='utf-8'), re.M)) - {''}


def main():
    used = {}
    errors = []
    for path in sorted((BASE_DIR / 'templates').glob('**/*.html')):
        ids, errs = template_msgids(path)
        errors += errs
        for msgid in ids:
            used.setdefault(msgid, set()).add(path.name)
    for msgid in python_msgids():
        used.setdefault(msgid, set()).add('python')

    catalogs = {name: catalog_msgids(path) for name, path in CATALOGS.items()}
    # Chinese source strings are translated by the en catalog; English ones by zh_Hans.
    missing = {
        'en': sorted(m for m in used if not m.isascii() and m not in catalogs['en']),
        'zh_Hans': sorted(m for m in used if m.isascii() and m and m not in catalogs['zh_Hans']),
    }
    stale = {name: sorted(ids - set(used)) for name, ids in catalogs.items()}

    for err in errors:
        print(f'TEMPLATE ERROR {err}')
    print(f'{len(used)} messages used by templates and Python code')
    problems = 0
    for name in CATALOGS:
        for label, items in (('missing from', missing[name]), ('never looked up in', stale[name])):
            if items:
                problems += len(items)
                print(f'\n{label} locale/{name} ({len(items)}):')
                for msgid in items:
                    where = f'  <- {sorted(used[msgid])}' if msgid in used else ''
                    print(f'  {msgid!r}{where}')
    print('\nOK: catalogs match runtime lookups' if not problems and not errors else f'\n{problems} problem(s)')
    return 1 if problems or errors else 0


if __name__ == '__main__':
    sys.exit(main())
