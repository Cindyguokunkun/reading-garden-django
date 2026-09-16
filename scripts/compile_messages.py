"""Compile the project's simple gettext PO files without a system gettext install."""
import ast
import struct
from pathlib import Path


def compile_po(source: Path, target: Path):
    messages = {}
    msgid = msgstr = None
    active = None

    def store():
        if msgid is not None and msgstr is not None:
            messages[msgid] = msgstr

    for raw in source.read_text(encoding='utf-8-sig').splitlines() + ['']:
        line = raw.strip()
        if line.startswith('msgid '):
            store(); msgid = ast.literal_eval(line[6:]); msgstr = None; active = 'id'
        elif line.startswith('msgstr '):
            msgstr = ast.literal_eval(line[7:]); active = 'str'
        elif line.startswith('"'):
            value = ast.literal_eval(line)
            if active == 'id': msgid += value
            elif active == 'str': msgstr += value
        elif not line:
            store(); msgid = msgstr = None; active = None
    keys = sorted(messages)
    ids = b'\0'.join(k.encode('utf-8') for k in keys) + b'\0'
    strs = b'\0'.join(messages[k].encode('utf-8') for k in keys) + b'\0'
    n = len(keys); header = 7 * 4; ids_table = header; strs_table = ids_table + n * 8
    ids_offset = strs_table + n * 8; strs_offset = ids_offset + len(ids)
    out = [struct.pack('<7I', 0x950412DE, 0, n, ids_table, strs_table, 0, 0)]
    offset = 0
    for key in keys:
        data = key.encode('utf-8'); out.append(struct.pack('<2I', len(data), ids_offset + offset)); offset += len(data) + 1
    offset = 0
    for key in keys:
        data = messages[key].encode('utf-8'); out.append(struct.pack('<2I', len(data), strs_offset + offset)); offset += len(data) + 1
    out.extend([ids, strs]); target.write_bytes(b''.join(out))


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    for po in root.glob('locale/*/LC_MESSAGES/django.po'):
        compile_po(po, po.with_suffix('.mo'))
        print(f'Compiled {po.relative_to(root)}')
