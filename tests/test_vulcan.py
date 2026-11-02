import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from helpers import make_config
from local_agentic_triage.mail.pipeline import poll
from local_agentic_triage.reporting.store import Store


class VulcanTests(unittest.TestCase):
    raw = (b'From: Dziennik \xc5\xbbak <NoReply@VULCAN.NET.PL>\r\n'
           b'X-Localized: wiadomo\xc5\x9b\xc4\x87\r\n'
           b'Date: Sat, 31 Oct 2026 01:27:00 +0100\r\n\r\nprivate body\xff\rtrailer')

    def test_trusted_sender_forwards_exact_bytes_then_continues(self):
        for destination in ('leave', 'archive'):
            with self.subTest(destination=destination), tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory) / 'db')
                box = Mock(validity='77')
                box.candidates.return_value = ['9']; box.source_key.return_value = 'key'
                box.raw.return_value = self.raw
                classifier = Mock(return_value={'destination': destination, 'summary': 'summary' if destination == 'archive' else None})
                events = Mock(return_value={'status': 'ok', 'results': []})
                config = make_config()
                def delivered(*args, **kwargs):
                    classifier.assert_not_called(); events.assert_not_called()
                    return subprocess.CompletedProcess(args[0], 0, b'{"code":"DELIVERED","deliveries":1}', b'private stderr')
                with patch('subprocess.run', side_effect=delivered) as run:
                    self.assertEqual(poll(box, store, config, classifier, events, extractor=lambda mail: []), 0)
                run.assert_called_once_with([config.agentic_vulcan, 'process', '--config', config.agentic_vulcan_config,
                                             '--state', config.agentic_vulcan_state], input=self.raw,
                                            shell=False, capture_output=True, timeout=config.timeout)
                box.raw.assert_called_once_with('9'); classifier.assert_called_once(); events.assert_called_once()
                getattr(box, destination).assert_called_once_with('9')
                store.close()

    def test_untrusted_from_never_invokes(self):
        headers = (b'', b'Subject: no sender\n', b'From: other@example.invalid\n',
                   b'From: noreply@vulcan.net.pl.evil.invalid\n',
                   b'From: noreply@vulcan.net.pl, other@example.invalid\n',
                   b'From: noreply@vulcan.net.pl,\n',
                   b'From: (comment) noreply@vulcan.net.pl\n',
                   b'From: \xc2\xa0noreply@vulcan.net.pl\n',
                   b'From: noreply@vulcan.net.pl\xc2\xa0\n',
                   b'From: School <noreply@vulcan.net.pl>\xc2\xa0\n',
                   b'From: noreply@vulcan.net.pl\nFrom: noreply@vulcan.net.pl\n',
                   b'From: School <noreply@vulcan.net.pl\n',
                   b'From: noreply@vulcan.net.pl trailing junk\n',
                   b'From: Group: noreply@vulcan.net.pl;\n')
        for header in headers:
            with self.subTest(header=header), tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory) / 'db'); box = Mock(validity='77')
                box.candidates.return_value=['9']; box.source_key.return_value='key'
                box.raw.return_value=header+b'\nFrom: noreply@vulcan.net.pl in body'
                box.internal_date.return_value='2026-10-31T01:27:00+01:00'
                classifier=Mock(return_value={'destination':'leave','summary':None})
                with patch('subprocess.run') as run:
                    self.assertEqual(poll(box,store,make_config(),classifier,Mock(return_value={'status':'ok','results':[]}),extractor=lambda mail: []),0)
                run.assert_not_called(); classifier.assert_called_once(); store.close()

    def test_forwarding_precedes_ordinary_body_decoding(self):
        raw = (b'From: noreply@vulcan.net.pl\r\n'
               b'Content-Type: text/plain; charset=synthetic-unsupported\r\n\r\nprivate body')
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db'); box=Mock(validity='77')
            box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=raw
            classifier=Mock(); events=Mock()
            receipt=subprocess.CompletedProcess([],0,b'{"code":"DELIVERED","deliveries":1}',b'')
            with patch('subprocess.run',return_value=receipt) as run, \
                 self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),1)
            run.assert_called_once()
            self.assertIn('stage=read error=', '\n'.join(logs.output))
            classifier.assert_not_called(); events.assert_not_called()
            box.leave.assert_not_called(); box.archive.assert_not_called(); store.close()

    def test_failures_are_fatal_sanitized_and_retryable(self):
        receipts = [b'private output', b'{}', b'{"code":"DELIVERED","deliveries":true}',
                    b'{"code":"DELIVERED","deliveries":1.0}', b'{"code":"DELIVERED","deliveries":0}',
                    b'{"code":"DELIVERED","deliveries":1,"extra":null}',
                    b'{"code":"DELIVERED","code":"DELIVERED","deliveries":1}',
                    b'{"code":"DELIVERED","deliveries":1} trailing', b'\xff']
        failures = [subprocess.CompletedProcess([],0,r,b'private stderr') for r in receipts]
        failures += [subprocess.CompletedProcess([],3,b'{"code":"DELIVERED","deliveries":1}',b'private stderr'),
                     subprocess.TimeoutExpired('/synthetic/private-tool',1,output=b'private output',stderr=b'private stderr'),
                     OSError('/synthetic/private-tool')]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                store=Store(Path(directory)/'db'); box=Mock(validity='77')
                box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=self.raw
                classifier=Mock(); events=Mock()
                kwargs={'side_effect':failure} if isinstance(failure,Exception) else {'return_value':failure}
                with patch('subprocess.run',**kwargs), self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                    self.assertEqual(poll(box,store,make_config(),classifier,events),1)
                output='\n'.join(logs.output)
                self.assertIn('stage=agentic_vulcan error=agentic_vulcan_failed',output)
                for secret in ('private', '/synthetic', 'VULCAN.NET.PL', 'body'):
                    self.assertNotIn(secret,output)
                classifier.assert_not_called(); events.assert_not_called()
                box.leave.assert_not_called(); box.archive.assert_not_called(); self.assertIsNone(store.prepared('key'))
                # No acknowledgment is persisted: a later poll must invoke again.
                with patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,b'{"code":"DELIVERED","deliveries":1}',b'')) as retry:
                    classifier.return_value={'destination':'leave','summary':None}
                    events.return_value={'status':'ok','results':[]}
                    self.assertEqual(poll(box,store,make_config(),classifier,events,extractor=lambda mail: []),0)
                retry.assert_called_once(); store.close()

    def test_prepared_archive_retry_never_reads_or_invokes(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db'); store.prepare('key','synthetic','summary',sent_at='2026-10-31T01:27:00+01:00')
            box=Mock(validity='77'); box.candidates.return_value=['9']; box.source_key.return_value='key'
            classifier=Mock(); events=Mock()
            with patch('subprocess.run') as run:
                self.assertEqual(poll(box,store,make_config(),classifier,events),0)
            run.assert_not_called(); box.raw.assert_not_called(); classifier.assert_not_called(); events.assert_not_called()
            box.archive.assert_called_once_with('9'); store.close()
