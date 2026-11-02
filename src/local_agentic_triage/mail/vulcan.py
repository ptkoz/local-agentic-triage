"""Hand raw mail to the local agentic-vulcan; success is its exact delivery receipt."""
import subprocess
from pathlib import Path

from local_agentic_triage.classifier.core import strict_json
from local_agentic_triage.common.diagnostics import CommandError


def forward(raw, config):
    home = Path.home()
    app = home / 'Library/Application Support/agentic-vulcan'
    try:
        result = subprocess.run(
            [str(home / '.local/bin/agentic-vulcan'), 'process', '--config', str(app / 'config.json'),
             '--state', str(app / 'state')],
            input=raw, shell=False, capture_output=True, timeout=config.timeout)
        if result.returncode == 0:
            receipt = strict_json(result.stdout)
            if receipt == {'code': 'DELIVERED', 'deliveries': 1} and type(receipt['deliveries']) is int:
                return
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    raise CommandError('agentic_vulcan_failed')
