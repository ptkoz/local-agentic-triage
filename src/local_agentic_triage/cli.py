import argparse
import logging
import os
import subprocess
from pathlib import Path
from local_agentic_triage.common.config import DEFAULT_PATH, load
from local_agentic_triage.common.lock import Lock, Busy
from local_agentic_triage.mail.imap import Mailbox, uid_value
from local_agentic_triage.mail.pipeline import diagnose, poll

log = logging.getLogger('local_agentic_triage')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='local-agentic-triage')
    parser.add_argument('--config', type=Path, default=DEFAULT_PATH)
    commands = parser.add_subparsers(dest='command', required=True)
    config_command = commands.add_parser('config')
    config_command.add_argument('action', choices=['validate'])
    mail = commands.add_parser('mail')
    mail.add_argument('action', choices=['poll', 'diagnose'])
    mail.add_argument('--uid')
    mail.add_argument('--uidvalidity')
    args = parser.parse_args(argv)
    try:
        config = load(args.config)
        if args.command == 'config':
            for executable in (config.himalaya, config.hermes):
                path = Path(executable)
                if not path.is_file() or not os.access(path, os.X_OK):
                    raise ValueError('configured executable unavailable')
            log.info('config_valid')
            return 0
        if args.command == 'mail' and args.action == 'diagnose':
            if args.uid is None:
                raise ValueError('diagnosis_uid_required')
            uid = uid_value(args.uid)
            expected_validity = uid_value(args.uidvalidity) if args.uidvalidity is not None else None
            mailbox = Mailbox.connect(config)
            try:
                return diagnose(mailbox, config, uid, expected_validity=expected_validity)
            finally:
                mailbox.close()
        # mail poll
        Path(config.poll_lock).expanduser().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with Lock(Path(config.poll_lock).expanduser()):
            mailbox = Mailbox.connect(config)
            try:
                return poll(mailbox, config, runner=subprocess.run)
            finally:
                mailbox.close()
    except Busy:
        log.info('run_locked')
        return 75
    except Exception:
        log.error('command_failed')
        return 1


def entry():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')
    raise SystemExit(main())
