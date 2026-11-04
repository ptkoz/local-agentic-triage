"""Hand raw eduVULCAN mail to the local agentic-vulcan orchestrator."""
import logging
import os
import tempfile
from pathlib import Path

VULCAN_SENDER = 'noreply@vulcan.net.pl'
log = logging.getLogger('local_agentic_triage')


def forward(uid, raw, config):
    """Best effort: log a fixed result and never raise."""
    try:
        home = Path.home()
        app = home / 'Library/Application Support/agentic-vulcan'
        with tempfile.TemporaryFile() as input_file:
            input_file.write(raw)
            input_file.seek(0)
            launcher = str(home / '.local/bin/agentic-vulcan-orchestrator')
            argv = [launcher, '--config', str(app / 'config.json')]
            actions = [
                (os.POSIX_SPAWN_DUP2, input_file.fileno(), 0),
                (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0o600),
                (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0o600),
            ]
            os.posix_spawn(launcher, argv, os.environ.copy(), file_actions=actions, setsid=True)
        log.info('agentic_vulcan_started uid=%s result=started', uid)
        return
    except Exception:
        pass
    log.error('agentic_vulcan_failed uid=%s result=failed', uid)
