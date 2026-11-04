import os
import sys
import tempfile
import time
import unittest
import warnings
import gc
from pathlib import Path
from unittest.mock import Mock, patch

from helpers import make_config
from local_agentic_triage.mail.pipeline import poll
from local_agentic_triage.mail.vulcan import forward
from local_agentic_triage.reporting.store import Store

DATE = b'Date: Sat, 31 Oct 2026 01:27:00 +0100\r\n\r\nprivate body\xff\rtrailer'


def raw_from(sender):
    return b'From: ' + sender + b'\r\n' + DATE


class VulcanTests(unittest.TestCase):
    def forward(self, outcome=None, raw=b'raw'):
        def spawn(*args, **kwargs):
            if isinstance(outcome, Exception): raise outcome
            return 123
        with patch('os.posix_spawn', side_effect=spawn) as run, self.assertLogs('local_agentic_triage', level='INFO') as logs:
            self.assertIsNone(forward('9', raw, make_config()))
        return run, '\n'.join(logs.output)

    def test_forward_spawns_detached_orchestrator_with_exact_bytes_and_logs_start(self):
        config = make_config(); raw = raw_from(b'noreply@vulcan.net.pl')
        captured = []
        def spawn(*args, **kwargs):
            descriptor = kwargs['file_actions'][0][1]
            captured.append(os.pread(descriptor, len(raw) + 1, 0))
            return 123
        with tempfile.TemporaryDirectory() as home, patch.dict('os.environ', {'HOME': home}), \
             patch('os.posix_spawn', side_effect=spawn) as run, self.assertLogs('local_agentic_triage', level='INFO') as logs:
            forward('9', raw, config)
        app = f'{home}/Library/Application Support/agentic-vulcan'
        launcher = f'{home}/.local/bin/agentic-vulcan-orchestrator'
        args, kwargs = run.call_args
        self.assertEqual(args[:2], (launcher, [launcher, '--config', f'{app}/config.json']))
        self.assertEqual(args[2]['HOME'], home)
        self.assertEqual(kwargs['file_actions'][0][0], os.POSIX_SPAWN_DUP2)
        self.assertEqual(kwargs['file_actions'][0][2], 0)
        self.assertEqual(kwargs['file_actions'][1:], [
            (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0o600),
            (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0o600)])
        self.assertIs(kwargs['setsid'], True)
        self.assertEqual(captured, [raw])
        self.assertEqual(logs.output, ['INFO:local_agentic_triage:agentic_vulcan_started uid=9 result=started'])

    def test_forward_does_not_wait_and_absorbs_spawn_failures(self):
        with patch('os.waitpid') as waitpid:
            run, logs = self.forward()
        run.assert_called_once(); waitpid.assert_not_called()
        self.assertEqual(logs, 'INFO:local_agentic_triage:agentic_vulcan_started uid=9 result=started')
        for outcome in (OSError('private'), RuntimeError('private')):
            with self.subTest(outcome=repr(outcome)[:40]):
                run, logs = self.forward(outcome)
                self.assertEqual(logs, 'ERROR:local_agentic_triage:agentic_vulcan_failed uid=9 result=failed')

    def test_real_child_reads_exact_bytes_after_forward_returns(self):
        raw = b'private\x00mail\xff' * 8192
        with tempfile.TemporaryDirectory() as home:
            root = Path(home); launcher = root / '.local/bin/agentic-vulcan-orchestrator'
            launcher.parent.mkdir(parents=True)
            launcher.write_text(
                f'#!{sys.executable}\n'
                'import os, pathlib, sys, time\n'
                'home=pathlib.Path(os.environ["HOME"])\n'
                'gate=home/"gate"\n'
                'for _ in range(300):\n'
                '    if gate.exists(): break\n'
                '    time.sleep(0.01)\n'
                'else: raise SystemExit(3)\n'
                '(home/"received").write_bytes(sys.stdin.buffer.read())\n')
            launcher.chmod(0o700)
            started = time.monotonic()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                with patch.dict('os.environ', {'HOME': home}), self.assertLogs('local_agentic_triage', level='INFO'):
                    forward('9', raw, make_config())
                gc.collect()
            self.assertFalse([warning for warning in caught if warning.category is ResourceWarning])
            self.assertLess(time.monotonic() - started, 1)
            self.assertFalse((root / 'received').exists())
            (root / 'gate').touch()
            for _ in range(300):
                if (root / 'received').exists(): break
                time.sleep(0.01)
            self.assertEqual((root / 'received').read_bytes(), raw)

    def run_poll(self, sender, outcome=None, destination='leave'):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db'); box = Mock(validity='77'); raw = raw_from(sender)
            box.candidates.return_value = ['9']; box.source_key.return_value = 'key'; box.raw.return_value = raw
            classifier = Mock(return_value={'destination': destination, 'summary': 'summary' if destination == 'archive' else None})
            captured = []
            def spawn(*args, **kwargs):
                descriptor = kwargs['file_actions'][0][1]
                captured.append(os.pread(descriptor, len(raw) + 1, 0))
                if isinstance(outcome, Exception): raise outcome
                return 123
            with patch('os.posix_spawn', side_effect=spawn) as run, self.assertLogs('local_agentic_triage', level='INFO') as logs:
                status = poll(box, store, make_config(), classifier, Mock(return_value={'status': 'ok', 'results': []}),
                              extractor=lambda mail: [])
            run.stdin_bytes = captured
            store.close()
        return status, box, classifier, run, raw, '\n'.join(logs.output)

    def test_matching_parsed_sender_forwards_exact_raw_after_classification(self):
        for sender in (b'noreply@vulcan.net.pl', b'NoReply@VULCAN.NET.PL',
                       b'Dziennik VULCAN <noreply@vulcan.net.pl>'):
            for destination in ('leave', 'archive'):
                with self.subTest(sender=sender, destination=destination):
                    status, box, classifier, run, raw, logs = self.run_poll(sender, destination=destination)
                    self.assertEqual(status, 0)
                    self.assertEqual(run.stdin_bytes, [raw])
                    classifier.assert_called_once(); getattr(box, destination).assert_called_once_with('9')
                    self.assertIn('INFO:local_agentic_triage:agentic_vulcan_started uid=9 result=started', logs)
                    self.assertNotIn('private', logs)

    def test_nonmatching_sender_does_not_invoke(self):
        for sender in (b'noreply@vulcan.net.pl.evil.invalid', b'xnoreply@vulcan.net.pl', b'other@example.invalid',
                       b'Dziennik <noreply@vulcan.net.pl.evil.invalid>'):
            with self.subTest(sender=sender):
                status, box, classifier, run, raw, logs = self.run_poll(sender)
                self.assertEqual(status, 0)
                run.assert_not_called(); classifier.assert_called_once()
                self.assertNotIn('agentic_vulcan', logs)

    def test_failure_logs_fixed_result_and_triage_continues(self):
        for outcome in (OSError('private'),):
            for destination in ('leave', 'archive'):
                with self.subTest(outcome=repr(outcome)[:30], destination=destination):
                    status, box, classifier, run, raw, logs = self.run_poll(b'noreply@vulcan.net.pl', outcome, destination)
                    self.assertEqual(status, 0)
                    self.assertIn('ERROR:local_agentic_triage:agentic_vulcan_failed uid=9 result=failed', logs)
                    self.assertNotIn('private', logs); self.assertNotIn('agentic_vulcan_started', logs)
                    classifier.assert_called_once(); getattr(box, destination).assert_called_once_with('9')
