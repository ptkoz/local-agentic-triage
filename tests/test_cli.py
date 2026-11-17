import logging
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch
from local_agentic_triage.common.lock import Lock, Busy
from local_agentic_triage.cli import entry, main
from helpers import config_text


class CLITests(unittest.TestCase):
    def test_entry_configures_logs_with_date_and_time(self):
        with patch('local_agentic_triage.cli.logging.basicConfig') as basic_config, \
             patch('local_agentic_triage.cli.main', return_value=0):
            with self.assertRaises(SystemExit) as stopped:
                entry()
        self.assertEqual(stopped.exception.code, 0)
        basic_config.assert_called_once_with(
            level=logging.INFO,
            format='%(asctime)s %(levelname)s %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )

    def test_lock(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'lock'
            with Lock(p):
                with self.assertRaises(Busy):
                    with Lock(p):
                        pass
            with Lock(p):
                pass

    def test_mail_poll_command(self):
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / 'config.toml'
            config.write_text(config_text(poll_lock=f'{d}/poll.lock'))
            box = Mock()
            with patch('local_agentic_triage.cli.Mailbox.connect', return_value=box), \
                 patch('local_agentic_triage.cli.poll', return_value=0) as p:
                self.assertEqual(main(['--config', str(config), 'mail', 'poll']), 0)
                p.assert_called_once()
                box.close.assert_called_once()
                self.assertIs(p.call_args.kwargs['runner'], subprocess.run)

    def test_safe_error(self):
        with patch('local_agentic_triage.cli.load', side_effect=ValueError('sender subject secret')):
            with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
                self.assertEqual(main(['mail', 'poll']), 1)
            self.assertNotIn('secret', str(logs.output))

    def test_mail_diagnose_avoids_store_lock_and_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / 'config.toml'
            config.write_text(config_text(poll_lock=f'{d}/poll.lock'))
            box = Mock()
            with patch('local_agentic_triage.cli.Mailbox.connect', return_value=box) as connect, \
                 patch('local_agentic_triage.cli.diagnose', return_value=1) as diagnose, \
                 patch('local_agentic_triage.cli.Lock') as lock:
                self.assertEqual(main(['--config', str(config), 'mail', 'diagnose', '--uid', '9', '--uidvalidity', '77']), 1)
                diagnose.assert_called_once_with(box, ANY, '9', expected_validity='77')
                with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
                    for invalid in ('private value', '9\nforged', '0', '1' * 100):
                        self.assertEqual(main(['--config', str(config), 'mail', 'diagnose', '--uid', invalid]), 1)
                    self.assertEqual(main(['--config', str(config), 'mail', 'diagnose', '--uid', '9', '--uidvalidity', 'private\nvalue']), 1)
                output = '\n'.join(logs.output)
                self.assertNotIn('private', output)
                self.assertNotIn('forged', output)
            self.assertEqual(connect.call_count, 1)
            box.close.assert_called_once()
            lock.assert_not_called()

    def test_config_validate_is_side_effect_free(self):
        with tempfile.TemporaryDirectory() as d:
            executable = Path(d) / 'tool'
            executable.write_text('#!/bin/sh\n')
            executable.chmod(0o700)
            config = Path(d) / 'config.toml'
            config.write_text(config_text(himalaya=str(executable), hermes=str(executable),
                                          poll_lock=f'{d}/poll.lock'))
            previous = os.umask(0o022)
            try:
                with patch('local_agentic_triage.cli.Mailbox.connect') as mailbox, \
                     patch('local_agentic_triage.cli.subprocess.run') as run:
                    self.assertEqual(main(['--config', str(config), 'config', 'validate']), 0)
                    self.assertEqual(os.umask(0o022), 0o022)
            finally:
                os.umask(previous)
            mailbox.assert_not_called()
            run.assert_not_called()
            executable.chmod(0o600)
            self.assertEqual(main(['--config', str(config), 'config', 'validate']), 1)
