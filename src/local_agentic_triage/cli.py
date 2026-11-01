import argparse
import logging
import os
import subprocess
from pathlib import Path
from local_agentic_triage.common.config import DEFAULT_PATH,load
from local_agentic_triage.common.lock import Lock,Busy
from local_agentic_triage.mail.imap import Mailbox
from local_agentic_triage.mail.imap import uid_value
from local_agentic_triage.mail.pipeline import diagnose,poll
from local_agentic_triage.reporting.store import Store
from local_agentic_triage.reporting.report import send

log=logging.getLogger('local_agentic_triage')

def main(argv=None):
    parser=argparse.ArgumentParser(prog='local-agentic-triage')
    parser.add_argument('--config',type=Path,default=DEFAULT_PATH)
    commands=parser.add_subparsers(dest='command',required=True)
    config_command=commands.add_parser('config'); config_command.add_argument('action',choices=['validate'])
    mail=commands.add_parser('mail'); mail.add_argument('action',choices=['poll','diagnose']); mail.add_argument('--uid'); mail.add_argument('--uidvalidity')
    report=commands.add_parser('report'); report.add_argument('action',choices=['send']); report.add_argument('--deliver',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='report' and not args.deliver:
        log.error('report_delivery_disabled'); return 2
    try:
        config=load(args.config)
        if args.command=='config':
            for executable in (config.himalaya,config.hermes):
                path=Path(executable)
                if not path.is_file() or not os.access(path,os.X_OK):
                    raise ValueError('configured executable unavailable')
            log.info('config_valid')
            return 0
        if args.command=='mail' and args.action=='diagnose':
            if args.uid is None:
                raise ValueError('diagnosis_uid_required')
            uid=uid_value(args.uid)
            expected_validity=uid_value(args.uidvalidity) if args.uidvalidity is not None else None
            mailbox=Mailbox.connect(config)
            try: return diagnose(mailbox,config,uid,expected_validity=expected_validity)
            finally: mailbox.close()
        lock_path=config.poll_lock if args.command=='mail' else config.report_lock
        for path in (config.database,lock_path,config.delivery_lock): Path(path).expanduser().parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with Lock(Path(lock_path).expanduser()):
            store=Store(Path(config.database).expanduser())
            try:
                if args.command=='report': send(store,config,runner=subprocess.run); return 0
                mailbox=Mailbox.connect(config)
                try: return poll(mailbox,store,config,runner=subprocess.run)
                finally: mailbox.close()
            finally: store.close()
    except Busy:
        log.info('run_locked'); return 75
    except Exception:
        log.error('command_failed'); return 1

def entry():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    raise SystemExit(main())
