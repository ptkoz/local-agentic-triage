"""Hand raw eduVULCAN mail to the local agentic-vulcan orchestrator."""
import logging
import subprocess
from pathlib import Path

VULCAN_SENDER = 'noreply@vulcan.net.pl'
log = logging.getLogger('local_agentic_triage')


def forward(uid, raw, config):
    """Best effort: log a fixed result and never raise."""
    try:
        home = Path.home()
        app = home / 'Library/Application Support/agentic-vulcan'
        result = subprocess.run(
            [str(home / '.local/bin/agentic-vulcan-orchestrator'), '--config', str(app / 'config.json')],
            input=raw, shell=False, capture_output=True)
        if result.returncode == 0:
            log.info('agentic_vulcan_completed uid=%s result=delivered', uid)
            return
    except Exception:
        pass
    log.error('agentic_vulcan_failed uid=%s result=failed', uid)
