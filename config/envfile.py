import os

def load_env(path):
    """Populate os.environ from a KEY=VALUE file; real environment variables win."""
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)
