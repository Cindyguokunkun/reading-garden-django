"""Compile .po files under locale/ into .mo without GNU gettext (Windows)."""
import io
import struct
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def unquote(token):
    token = token.strip()
    if not (token.startswith('"') and token.endswith('"')):
        raise ValueError(f'bad po string: {token}')
    body = token[1:-1]
    out, i = [], 0
    mapping = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', '"': '"'}
    while i < len(body):
        c = body[i]
        if c == '\\' and i + 1 < len(body):
            out.append(mapping.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)

def parse_po(path):
    entries, msgid, msgstr, section = {}, None, '', None
    def flush():
        if msgid is not None:
            entries[msgid] = msgstr
    for raw in io.open(path, encoding='utf-8'):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('msgid '):
            flush()
            msgid, msgstr, section = unquote(line[len('msgid '):]), '', 'id'
        elif line.startswith('msgstr '):
            msgstr, section = unquote(line[len('msgstr '):]), 'str'
        elif line.startswith('"'):
            piece = unquote(line)
            if section == 'id':
                msgid += piece
            else:
                msgstr += piece
    flush()
    return entries

def write_mo(entries, path):
    keys = sorted(entries)
    ids, strs = b'', b''
    offsets = []
    for key in keys:
        value = entries[key]
        kb, vb = key.encode('utf-8'), value.encode('utf-8')
        offsets.append((len(ids), len(kb), len(strs), len(vb)))
        ids += kb + b'\0'
        strs += vb + b'\0'
    n = len(keys)
    keys_start = 28 + 16 * n
    values_start = keys_start + len(ids)
    out = struct.pack('<Iiiiiii', 0x950412de, 0, n, 28, 28 + 8 * n, 0, 0)
    for ko, kl, vo, vl in offsets:
        out += struct.pack('<ii', kl, ko + keys_start)
    for ko, kl, vo, vl in offsets:
        out += struct.pack('<ii', vl, vo + values_start)
    out += ids + strs
    path.write_bytes(out)

def main():
    compiled = 0
    for po in (BASE_DIR / 'locale').glob('*/LC_MESSAGES/*.po'):
        mo = po.with_suffix('.mo')
        write_mo(parse_po(po), mo)
        print(f'{po.relative_to(BASE_DIR)} -> {mo.name}')
        compiled += 1
    if not compiled:
        print('no .po files found', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
