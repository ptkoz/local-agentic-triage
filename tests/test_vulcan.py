import os
import stat
import subprocess
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

from helpers import make_config
from local_agentic_triage.common.diagnostics import CommandError
from local_agentic_triage.mail.mime import vulcan_sender
from local_agentic_triage.mail.pipeline import poll
from local_agentic_triage.mail.vulcan import forward
from local_agentic_triage.reporting.store import Store


def child(stdout, code=0):
    """Fake subprocess.run: write the child's stdout to the handed-over fd."""
    def run(argv, **kwargs):
        os.write(kwargs['stdout'], stdout)
        return subprocess.CompletedProcess(argv, code)
    return run


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
                    return child(b'{"code":"DELIVERED","deliveries":1}')(*args, **kwargs)
                with patch('subprocess.run', side_effect=delivered) as run:
                    self.assertEqual(poll(box, store, config, classifier, events, extractor=lambda mail: []), 0)
                run.assert_called_once_with([config.agentic_vulcan, 'process', '--config', config.agentic_vulcan_config,
                                             '--state', config.agentic_vulcan_state], input=self.raw,
                                            shell=False, stdout=ANY, stderr=subprocess.DEVNULL, timeout=config.timeout)
                box.raw.assert_called_once_with('9'); classifier.assert_called_once(); events.assert_called_once()
                getattr(box, destination).assert_called_once_with('9')
                store.close()

    def test_earliest_header_delimiter_wins_over_later_crlf_pair(self):
        self.assertTrue(vulcan_sender(b'From: noreply@vulcan.net.pl\n\nbody\r\n\r\nmore'))
        self.assertTrue(vulcan_sender(b'From: noreply@vulcan.net.pl\r\n\r\nbody\n\nmore'))
        # An earlier valid delimiter ends the header; a later From is body text.
        self.assertFalse(vulcan_sender(b'Subject: x\n\nFrom: noreply@vulcan.net.pl\r\n\r\nmore'))
        # Malformed-header rejection is unchanged (bare CR before an LF delimiter).
        self.assertFalse(vulcan_sender(b'From: noreply@vulcan.net.pl\r\n\nbody'))

    def _script(self, directory, body):
        path = Path(directory) / 'child'
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def test_real_child_receives_exact_stdin_and_receipt_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            seen = Path(directory) / 'stdin'
            child = self._script(directory, f'cat > {seen}\nprintf \'{{"code":"DELIVERED","deliveries":1}}\'\n')
            forward(self.raw, make_config(agentic_vulcan=child))
            self.assertEqual(seen.read_bytes(), self.raw)

    def test_child_output_is_bounded_and_oversize_fails_sanitized(self):
        real_run = subprocess.run
        calls = []
        def spy(*args, **kwargs):
            calls.append(kwargs); return real_run(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            # Valid receipt followed by megabytes of padding on stdout and stderr.
            child = self._script(directory, 'cat >/dev/null\n'
                'printf \'{"code":"DELIVERED","deliveries":1}\'\n'
                'head -c 4194304 /dev/zero | tr \'\\0\' \' \'\n'
                'head -c 4194304 /dev/zero >&2\n')
            config = make_config(agentic_vulcan=child)
            with patch('subprocess.run', side_effect=spy), self.assertRaises(CommandError) as caught:
                forward(self.raw, config)
        self.assertEqual(str(caught.exception), 'agentic_vulcan_failed')
        self.assertNotIn('capture_output', calls[0])
        self.assertIs(calls[0]['stderr'], subprocess.DEVNULL)
        self.assertIs(calls[0]['shell'], False)
        self.assertEqual(calls[0]['input'], self.raw)
        self.assertEqual(calls[0]['timeout'], config.timeout)

    @staticmethod
    def _resources():
        return set(threading.enumerate()), set(os.listdir('/dev/fd'))

    def test_descendant_holding_stdout_does_not_leak_reader_thread_or_fd(self):
        receipt = '{"code":"DELIVERED","deliveries":1}'
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / 'pid'
            child = self._script(directory, f'cat >/dev/null\nsleep 60 &\necho $! > {pidfile}\nprintf \'{receipt}\'\n')
            threads, fds = self._resources()
            started = time.monotonic()
            try:
                forward(self.raw, make_config(agentic_vulcan=child))
                elapsed = time.monotonic() - started
                after = self._resources()
            finally:
                if pidfile.exists():
                    os.kill(int(pidfile.read_text()), signal.SIGKILL)
        self.assertLess(elapsed, 3)
        self.assertEqual(after[0] - threads, set())
        self.assertEqual(after[1] - fds, set())

    def test_reader_thread_and_fd_are_reclaimed_on_every_path(self):
        receipt = '{"code":"DELIVERED","deliveries":1}'
        scripts = {
            'normal': f'cat >/dev/null\nprintf \'{receipt}\'\n',
            'oversize': f'cat >/dev/null\nhead -c 4194304 /dev/zero | tr \'\\0\' \' \'\n',
            'timeout': 'exec sleep 60\n',
            'missing': None,
        }
        for name, body in scripts.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = self._script(directory, body) if body else str(Path(directory) / 'missing')
                config = make_config(agentic_vulcan=path, timeout=1)
                threads, fds = self._resources()
                try:
                    forward(self.raw, config)
                except CommandError:
                    pass
                after = self._resources()
                self.assertEqual(after[0] - threads, set())
                self.assertEqual(after[1] - fds, set())

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
            with patch('subprocess.run',side_effect=child(b'{"code":"DELIVERED","deliveries":1}')) as run, \
                 self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),1)
            run.assert_called_once()
            self.assertIn('stage=read error=', '\n'.join(logs.output))
            classifier.assert_not_called(); events.assert_not_called()
            box.leave.assert_not_called(); box.archive.assert_not_called(); store.close()

    def test_success_is_logged_with_fixed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db'); box=Mock(validity='77')
            box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=self.raw
            classifier=Mock(return_value={'destination':'leave','summary':None})
            with patch('subprocess.run',side_effect=child(b'{"code":"DELIVERED","deliveries":1}')), self.assertLogs('local_agentic_triage',level='INFO') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,Mock(return_value={'status':'ok','results':[]}),extractor=lambda mail: []),0)
            output='\n'.join(logs.output)
            self.assertIn('INFO:local_agentic_triage:agentic_vulcan_completed uid=9 result=delivered',output)
            self.assertNotIn('agentic_vulcan_failed',output)
            for secret in ('private','/synthetic','VULCAN.NET.PL','body','DELIVERED'):
                self.assertNotIn(secret,output)
            store.close()

    def test_failures_are_best_effort_sanitized_and_do_not_block_triage(self):
        receipts = [b'private output', b'{}', b'{"code":"DELIVERED","deliveries":true}',
                    b'{"code":"DELIVERED","deliveries":1.0}', b'{"code":"DELIVERED","deliveries":0}',
                    b'{"code":"DELIVERED","deliveries":1,"extra":null}',
                    b'{"code":"DELIVERED","code":"DELIVERED","deliveries":1}',
                    b'{"code":"DELIVERED","deliveries":1} trailing', b'\xff',
                    b'{"code":"DELIVERED","deliveries":1}'+b' '*4096]
        failures = [child(r) for r in receipts]
        failures += [child(b'{"code":"DELIVERED","deliveries":1}',3),
                     subprocess.TimeoutExpired('/synthetic/private-tool',1,output=b'private output',stderr=b'private stderr'),
                     OSError('/synthetic/private-tool')]
        for failure in failures:
            for destination in ('leave','archive'):
                with self.subTest(failure=repr(failure)[:40],destination=destination), tempfile.TemporaryDirectory() as directory:
                    store=Store(Path(directory)/'db'); box=Mock(validity='77')
                    box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=self.raw
                    classifier=Mock(return_value={'destination':destination,'summary':'summary' if destination=='archive' else None})
                    events=Mock(return_value={'status':'ok','results':[]})
                    with patch('subprocess.run',side_effect=failure), self.assertLogs('local_agentic_triage',level='INFO') as logs:
                        self.assertEqual(poll(box,store,make_config(),classifier,events,extractor=lambda mail: []),0)
                    output='\n'.join(logs.output)
                    self.assertIn('ERROR:local_agentic_triage:agentic_vulcan_failed uid=9 result=failed',output)
                    self.assertNotIn('agentic_vulcan_completed',output)
                    self.assertNotIn('mail_item_failed',output)
                    for secret in ('private','/synthetic','VULCAN.NET.PL','body','returncode'):
                        self.assertNotIn(secret,output)
                    classifier.assert_called_once(); events.assert_called_once()
                    getattr(box,destination).assert_called_once_with('9'); store.close()

    def test_prepared_archive_retry_never_reads_or_invokes(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db'); store.prepare('key','synthetic','summary',sent_at='2026-10-31T01:27:00+01:00')
            box=Mock(validity='77'); box.candidates.return_value=['9']; box.source_key.return_value='key'
            classifier=Mock(); events=Mock()
            with patch('subprocess.run') as run:
                self.assertEqual(poll(box,store,make_config(),classifier,events),0)
            run.assert_not_called(); box.raw.assert_not_called(); classifier.assert_not_called(); events.assert_not_called()
            box.archive.assert_called_once_with('9'); store.close()
