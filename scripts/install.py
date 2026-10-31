"""Install files only; service activation is a separate deployment action."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile

from build import build, ROOT


def directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def atomic_write(path, data, mode=0o600):
    descriptor, temporary_name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def render(value, replacements):
    if isinstance(value, str):
        for marker, replacement in replacements.items():
            value = value.replace(marker, replacement)
        return value
    if isinstance(value, list):
        return [render(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: render(item, replacements) for key, item in value.items()}
    return value


def install(home, python):
    os.umask(0o077)
    home = home.expanduser().resolve()
    python = str(Path(python).expanduser().resolve())
    runtime = home / '.local/libexec/local-agentic-triage'
    data = home / 'Library/Application Support/local-agentic-triage'
    logs = home / 'Library/Logs/local-agentic-triage'
    agents = home / 'Library/LaunchAgents'
    cache = home / 'Library/Caches/local-agentic-triage'
    for path in (runtime, data, cache, logs, agents):
        directory(path)
    build(runtime / 'local-agentic-triage.pyz', python)
    config = data / 'config.toml'
    if not config.exists():
        escaped_home = json.dumps(str(home), ensure_ascii=False)[1:-1]
        content = (ROOT / 'config.example.toml').read_text().replace('@HOME@', escaped_home)
        atomic_write(config, content.encode())
    config.chmod(0o600)
    replacements = {'@HOME@': str(home), '@PYTHON@': python}
    for template in (ROOT / 'launchd').glob('*.plist'):
        info = plistlib.loads(template.read_bytes())
        info = render(info, replacements)
        atomic_write(agents / template.name, plistlib.dumps(info))
    for name in ('poll.log', 'report.log'):
        path = logs / name
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path.home())
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args()
    install(args.home, args.python)
