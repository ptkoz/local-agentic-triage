"""Hand raw eduVULCAN mail to the local agentic-vulcan; success is its exact delivery receipt."""
import logging
import os
import subprocess
from pathlib import Path

from local_agentic_triage.classifier.core import strict_json

VULCAN_SENDER = 'noreply@vulcan.net.pl'
log = logging.getLogger('local_agentic_triage')


def forward(uid, raw, config):
    """Best effort: log a fixed result and never raise."""
    try:
        home = Path.home()
        app = home / 'Library/Application Support/agentic-vulcan'
        result = subprocess.run(
            [str(home / '.local/bin/agentic-vulcan'), 'process', '--config', str(app / 'config.json'),
             '--state', str(app / 'state')],
            input=raw, shell=False, capture_output=True, timeout=config.timeout,
            env={**os.environ, 'PATH': str(Path(config.hermes).parent) + os.pathsep + os.environ.get('PATH', '')})
        if result.returncode == 0:
            receipt = strict_json(result.stdout)
            if receipt == {'code': 'DELIVERED', 'deliveries': 1} and type(receipt['deliveries']) is int:
                log.info('agentic_vulcan_completed uid=%s result=delivered', uid)
                return
    except Exception:
        pass
    log.error('agentic_vulcan_failed uid=%s result=failed', uid)
