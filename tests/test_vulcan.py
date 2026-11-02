import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from helpers import make_config
from local_agentic_triage.mail.pipeline import poll
from local_agentic_triage.mail.vulcan import forward
from local_agentic_triage.reporting.store import Store

RECEIPT = b'{"code":"DELIVERED","deliveries":1}'
DATE = b'Date: Sat, 31 Oct 2026 01:27:00 +0100\r\n\r\nprivate body\xff\rtrailer'


def raw_from(sender):
    return b'From: ' + sender + b'\r\n' + DATE


def result(stdout=RECEIPT, code=0):
    return subprocess.CompletedProcess([], code, stdout, b'private stderr')


class VulcanTests(unittest.TestCase):
    def forward(self, outcome=None, raw=b'raw'):
        kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome or result()}
        with patch('subprocess.run', **kwargs) as run, self.assertLogs('local_agentic_triage', level='INFO') as logs:
            self.assertIsNone(forward('9', raw, make_config()))
        return run, '\n'.join(logs.output)

    def test_forward_runs_fixed_launcher_with_exact_bytes_and_logs_success(self):
        config = make_config(); raw = raw_from(b'noreply@vulcan.net.pl')
        with tempfile.TemporaryDirectory() as home, patch.dict('os.environ', {'HOME': home}), \
             patch('subprocess.run', return_value=result()) as run, self.assertLogs('local_agentic_triage', level='INFO') as logs:
            forward('9', raw, config)
        app = f'{home}/Library/Application Support/agentic-vulcan'
        run.assert_called_once_with(
            [f'{home}/.local/bin/agentic-vulcan', 'process', '--config', f'{app}/config.json', '--state', f'{app}/state'],
            input=raw, shell=False, capture_output=True, timeout=config.timeout)
        self.assertEqual(logs.output, ['INFO:local_agentic_triage:agentic_vulcan_completed uid=9 result=delivered'])

    def test_forward_requires_exact_receipt_and_absorbs_failures(self):
        bad = (result(code=3), result(b'private output'), result(b'{}'), result(b'{"code":"DELIVERED","deliveries":true}'),
               result(b'{"code":"DELIVERED","deliveries":1.0}'), result(b'{"code":"DELIVERED","deliveries":2}'),
               result(b'{"code":"DELIVERED","deliveries":1,"extra":1}'), result(b'\xff'),
               subprocess.TimeoutExpired('private', 1), OSError('private'), RuntimeError('private'))
        for outcome in bad:
            with self.subTest(outcome=repr(outcome)[:40]):
                run, logs = self.forward(outcome)
                self.assertEqual(logs, 'ERROR:local_agentic_triage:agentic_vulcan_failed uid=9 result=failed')

    def run_poll(self, sender, outcome=None, destination='leave'):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db'); box = Mock(validity='77'); raw = raw_from(sender)
            box.candidates.return_value = ['9']; box.source_key.return_value = 'key'; box.raw.return_value = raw
            classifier = Mock(return_value={'destination': destination, 'summary': 'summary' if destination == 'archive' else None})
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome or result()}
            with patch('subprocess.run', **kwargs) as run, self.assertLogs('local_agentic_triage', level='INFO') as logs:
                status = poll(box, store, make_config(), classifier, Mock(return_value={'status': 'ok', 'results': []}),
                              extractor=lambda mail: [])
            store.close()
        return status, box, classifier, run, raw, '\n'.join(logs.output)

    def test_matching_parsed_sender_forwards_exact_raw_after_classification(self):
        for sender in (b'noreply@vulcan.net.pl', b'NoReply@VULCAN.NET.PL',
                       b'Dziennik VULCAN <noreply@vulcan.net.pl>'):
            for destination in ('leave', 'archive'):
                with self.subTest(sender=sender, destination=destination):
                    status, box, classifier, run, raw, logs = self.run_poll(sender, destination=destination)
                    self.assertEqual(status, 0)
                    self.assertEqual(run.call_args.kwargs['input'], raw)
                    classifier.assert_called_once(); getattr(box, destination).assert_called_once_with('9')
                    self.assertIn('INFO:local_agentic_triage:agentic_vulcan_completed uid=9 result=delivered', logs)
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
        for outcome in (result(code=3), result(b'private output'), subprocess.TimeoutExpired('private', 1), OSError('private')):
            for destination in ('leave', 'archive'):
                with self.subTest(outcome=repr(outcome)[:30], destination=destination):
                    status, box, classifier, run, raw, logs = self.run_poll(b'noreply@vulcan.net.pl', outcome, destination)
                    self.assertEqual(status, 0)
                    self.assertIn('ERROR:local_agentic_triage:agentic_vulcan_failed uid=9 result=failed', logs)
                    self.assertNotIn('private', logs); self.assertNotIn('agentic_vulcan_completed', logs)
                    classifier.assert_called_once(); getattr(box, destination).assert_called_once_with('9')
