import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from helpers import make_config
from local_agentic_triage.common.diagnostics import CommandError
from local_agentic_triage.mail import mime
from local_agentic_triage.mail.pipeline import poll
from local_agentic_triage.mail.vulcan import forward
from local_agentic_triage.reporting.store import Store

RECEIPT = b'{"code":"DELIVERED","deliveries":1}'
RAW = b'From: Dziennik <noreply@vulcan.net.pl>\r\nDate: Sat, 31 Oct 2026 01:27:00 +0100\r\n\r\nprivate body\xff\rtrailer'


def result(stdout=RECEIPT, code=0):
    return subprocess.CompletedProcess([], code, stdout, b'private stderr')


class VulcanTests(unittest.TestCase):
    def test_forward_runs_fixed_launcher_with_exact_bytes(self):
        config = make_config()
        with tempfile.TemporaryDirectory() as home, patch.dict('os.environ', {'HOME': home}), \
             patch('subprocess.run', return_value=result()) as run:
            forward(RAW, config)
        app = f'{home}/Library/Application Support/agentic-vulcan'
        run.assert_called_once_with(
            [f'{home}/.local/bin/agentic-vulcan', 'process', '--config', f'{app}/config.json', '--state', f'{app}/state'],
            input=RAW, shell=False, capture_output=True, timeout=config.timeout)

    def test_forward_requires_exact_receipt(self):
        bad = (result(code=3), result(b'private output'), result(b'{}'), result(b'{"code":"DELIVERED","deliveries":true}'),
               result(b'{"code":"DELIVERED","deliveries":1.0}'), result(b'{"code":"DELIVERED","deliveries":2}'),
               result(b'{"code":"DELIVERED","deliveries":1,"extra":1}'), result(b'\xff'),
               subprocess.TimeoutExpired('private', 1), OSError('private'))
        for outcome in bad:
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            with self.subTest(outcome=repr(outcome)[:40]), patch('subprocess.run', **kwargs), \
                 self.assertRaisesRegex(CommandError, '^agentic_vulcan_failed$'):
                forward(RAW, make_config())

    def run_poll(self, uids, outcome=None, destination='leave'):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db'); box = Mock(validity='77')
            box.candidates.return_value = ['9']; box.source_key.return_value = 'key'
            box.raw.return_value = RAW; box.vulcan_uids.return_value = uids
            classifier = Mock(return_value={'destination': destination, 'summary': 'summary' if destination == 'archive' else None})
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome or result()}
            with patch('subprocess.run', **kwargs) as run, self.assertLogs('local_agentic_triage', level='INFO') as logs:
                status = poll(box, store, make_config(), classifier, Mock(return_value={'status': 'ok', 'results': []}),
                              extractor=lambda mail: [])
            store.close()
        return status, box, classifier, run, '\n'.join(logs.output)

    def test_matching_envelope_forwards_raw_bytes_and_logs_success(self):
        status, box, classifier, run, logs = self.run_poll({'9'})
        self.assertEqual(status, 0)
        self.assertEqual(run.call_args.kwargs['input'], RAW)
        box.vulcan_uids.assert_called_once_with(['9'])
        self.assertIn('INFO:local_agentic_triage:agentic_vulcan_completed uid=9 result=delivered', logs)
        self.assertNotIn('private', logs)

    def test_nonmatching_envelope_does_not_invoke(self):
        status, box, classifier, run, logs = self.run_poll(set())
        self.assertEqual(status, 0)
        run.assert_not_called(); classifier.assert_called_once()
        self.assertNotIn('agentic_vulcan', logs)

    def test_failure_logs_fixed_result_and_triage_continues(self):
        for outcome in (result(code=3), result(b'private output'), subprocess.TimeoutExpired('private', 1), OSError('private')):
            for destination in ('leave', 'archive'):
                with self.subTest(outcome=repr(outcome)[:30], destination=destination):
                    status, box, classifier, run, logs = self.run_poll({'9'}, outcome, destination)
                    self.assertEqual(status, 0)
                    self.assertIn('ERROR:local_agentic_triage:agentic_vulcan_failed uid=9 result=failed', logs)
                    self.assertNotIn('private', logs); self.assertNotIn('agentic_vulcan_completed', logs)
                    classifier.assert_called_once(); getattr(box, destination).assert_called_once_with('9')

    def test_lookup_failure_logs_fixed_result_and_triage_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db'); box = Mock(validity='77')
            box.candidates.return_value = ['9']; box.source_key.return_value = 'key'; box.raw.return_value = RAW
            box.vulcan_uids.side_effect = ValueError('private')
            classifier = Mock(return_value={'destination': 'leave', 'summary': None})
            with patch('subprocess.run') as run, self.assertLogs('local_agentic_triage', level='INFO') as logs:
                self.assertEqual(poll(box, store, make_config(), classifier, Mock(return_value={'status': 'ok', 'results': []}),
                                      extractor=lambda mail: []), 0)
            store.close()
        self.assertIn('agentic_vulcan_failed result=lookup', '\n'.join(logs.output))
        self.assertNotIn('private', '\n'.join(logs.output))
        run.assert_not_called(); classifier.assert_called_once()

    def test_sender_is_not_parsed_locally(self):
        self.assertFalse(hasattr(mime, 'vulcan_sender'))
