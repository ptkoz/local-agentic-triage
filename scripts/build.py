"""Build a stdlib-only zipapp, without caches or local runtime data."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import zipapp

ROOT = Path(__file__).resolve().parents[1]


def build(output, python=sys.executable):
    os.umask(0o077)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f'.{output.name}.',
                                                   dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        zipapp.create_archive(
            ROOT / 'src', temporary, interpreter=python,
            main='local_agentic_triage.cli:entry',
            filter=lambda path: '__pycache__' not in path.parts and path.suffix != '.pyc')
        temporary.chmod(0o600)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'dist/local-agentic-triage.pyz')
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args()
    build(args.output, args.python)
