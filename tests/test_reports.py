import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from helpers import make_config
from local_agentic_triage.common.attachment import Attachment
from local_agentic_triage.common.diagnostics import CommandError
from local_agentic_triage.reporting.report import deliver, render_notification


SUMMARY = 'Syntetyczne podsumowanie (przykład) wiadomości testowej.'


class RenderNotificationTests(unittest.TestCase):
    def _row(self, **kw):
        base = {'sender_name': 'Synthetic Shop', 'sender_address': 'orders@shop.invalid', 'summary': SUMMARY,
                'sent_at': '2026-11-17T14:23:00+00:00'}
        return {**base, **kw}

    def test_archive_format_with_date(self):
        text = render_notification(self._row(), 1900, destination='archive')
        self.assertEqual(text, '**Archiwum**: z **17 lis, 15:23** od **Synthetic Shop** (orders@shop.invalid)\n> ' + SUMMARY)

    def test_leave_format_with_date(self):
        text = render_notification(self._row(), 1900, destination='leave')
        self.assertEqual(text, '**Inbox**: z **17 lis, 15:23** od **Synthetic Shop** (orders@shop.invalid)\n> ' + SUMMARY)

    def test_missing_date_renders_fallback(self):
        for sent_at in (None, ''):
            with self.subTest(sent_at=sent_at):
                self.assertEqual(
                    render_notification(self._row(sent_at=sent_at), 1900, destination='archive'),
                    '**Archiwum**: od **Synthetic Shop** (orders@shop.invalid) — data wiadomości niedostępna\n> ' + SUMMARY)
                self.assertEqual(
                    render_notification(self._row(sent_at=sent_at), 1900, destination='leave'),
                    '**Inbox**: od **Synthetic Shop** (orders@shop.invalid) — data wiadomości niedostępna\n> ' + SUMMARY)

    def test_absent_display_name_renders_address_once(self):
        row = self._row(sender_name='')
        self.assertEqual(render_notification(row, 1900, destination='archive'),
                         '**Archiwum**: z **17 lis, 15:23** od **orders@shop.invalid**\n> ' + SUMMARY)
        self.assertEqual(render_notification(row | {'sent_at': None}, 1900, destination='leave'),
                         '**Inbox**: od **orders@shop.invalid** — data wiadomości niedostępna\n> ' + SUMMARY)

    def test_display_name_equal_to_address_renders_address_once(self):
        row = self._row(sender_name='orders@shop.invalid')
        self.assertEqual(render_notification(row, 1900, destination='leave'),
                         '**Inbox**: z **17 lis, 15:23** od **orders@shop.invalid**\n> ' + SUMMARY)

    def test_absent_address_renders_display_name_only(self):
        row = self._row(sender_address='')
        self.assertEqual(render_notification(row, 1900, destination='archive'),
                         '**Archiwum**: z **17 lis, 15:23** od **Synthetic Shop**\n> ' + SUMMARY)

    def test_destination_is_required_and_closed(self):
        with self.assertRaises(TypeError):
            render_notification(self._row(), 1900)
        for destination in ('', 'Archive', 'inbox', None):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                render_notification(self._row(), 1900, destination=destination)

    def test_oversize_raises(self):
        with self.assertRaises(ValueError):
            render_notification(self._row(summary='x' * 2000), 1900, destination='archive')

    def test_invalid_limit_raises(self):
        with self.assertRaises(ValueError):
            render_notification(self._row(), 50, destination='archive')
        with self.assertRaises(ValueError):
            render_notification(self._row(), 2001, destination='archive')

    def test_empty_summary_raises(self):
        with self.assertRaises(ValueError):
            render_notification(self._row(summary=''), 1900, destination='archive')

    def test_none_summary_raises(self):
        with self.assertRaises(ValueError):
            render_notification(self._row(summary=None), 1900, destination='archive')

    def test_summary_text_passes_through_exactly(self):
        summary = ' *Ważne* (4 listopada) <@123> **x**\n# nagłówek  '
        text = render_notification(self._row(summary=summary), 1900, destination='leave')
        self.assertEqual(text, '**Inbox**: z **17 lis, 15:23** od **Synthetic Shop** (orders@shop.invalid)\n> ' + summary)


class DeliverTests(unittest.TestCase):
    def test_calls_hermes_send_with_text(self):
        runner = Mock(return_value=Mock(returncode=0))
        deliver(runner, make_config(), 'hello world')
        runner.assert_called_once()
        call = runner.call_args
        self.assertEqual(call.args[0], [make_config().hermes, 'send', '--to', make_config().channel])
        self.assertEqual(call.kwargs['input'], 'hello world')
        self.assertFalse(call.kwargs['shell'])

    def test_none_runner_raises(self):
        with self.assertRaises(RuntimeError):
            deliver(None, make_config(), 'text')

    def test_nonzero_returncode_raises_and_logs_safe(self):
        huge = 2 ** 80
        runner = Mock(return_value=subprocess.CompletedProcess([], huge, 'PRIVATE', 'PRIVATE'))
        with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            with self.assertRaises(Exception):
                deliver(runner, make_config(), 'text')
        output = '\n'.join(logs.output)
        self.assertNotIn(str(huge), output)
        self.assertNotIn('PRIVATE', output)

    def test_subprocess_exception_logs_safe_code(self):
        runner = Mock(side_effect=OSError('PRIVATE connection error'))
        with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            with self.assertRaises(OSError):
                deliver(runner, make_config(), 'text')
        self.assertNotIn('PRIVATE', '\n'.join(logs.output))


ATTACHMENTS = [
    Attachment('event-1.ics', 'BEGIN:VCALENDAR\r\nSUMMARY:PRIVATE żółć\r\nEND:VCALENDAR\r\n'),
    Attachment('event-2.ics', 'BEGIN:VCALENDAR\r\nSUMMARY:PRIVATE second\r\nEND:VCALENDAR\r\n'),
]


class DeliverAttachmentTests(unittest.TestCase):
    def capture(self, returncode=0, side_effect=None):
        seen = {}

        def run(argv, **kwargs):
            paths = [line[len('MEDIA:'):] for line in kwargs['input'].split('\n') if line.startswith('MEDIA:')]
            seen.update(argv=argv, input=kwargs['input'], paths=paths,
                        contents=[Path(path).read_bytes() for path in paths],
                        modes=[stat.S_IMODE(os.stat(path).st_mode) for path in paths],
                        directories={stat.S_IMODE(os.stat(Path(path).parent).st_mode) for path in paths})
            if side_effect is not None:
                raise side_effect
            return subprocess.CompletedProcess(argv, returncode, 'PRIVATE', 'PRIVATE')
        return Mock(side_effect=run), seen

    def test_attachments_are_media_lines_of_private_files_in_one_send(self):
        runner, seen = self.capture()
        deliver(runner, make_config(), 'hello', ATTACHMENTS)
        runner.assert_called_once()
        self.assertEqual(seen['argv'], [make_config().hermes, 'send', '--to', make_config().channel])
        self.assertEqual(seen['input'], 'hello\n' + '\n'.join('MEDIA:' + path for path in seen['paths']))
        self.assertEqual([Path(path).name for path in seen['paths']], ['event-1.ics', 'event-2.ics'])
        self.assertEqual(len({Path(path).parent for path in seen['paths']}), 1)
        self.assertTrue(all(Path(path).is_absolute() and not re.search(r'\s', path) for path in seen['paths']))
        self.assertEqual(seen['contents'], [item.content.encode('utf-8') for item in ATTACHMENTS])
        self.assertEqual(seen['modes'], [0o600, 0o600])
        self.assertEqual(seen['directories'], {0o700})
        for path in seen['paths']:
            self.assertFalse(Path(path).exists())
            self.assertFalse(Path(path).parent.exists())
        self.assertNotIn('PRIVATE', ' '.join(seen['argv']))

    def test_attachments_are_removed_after_failed_send(self):
        for runner, seen, error in ((*self.capture(returncode=1), CommandError),
                                    (*self.capture(side_effect=OSError('PRIVATE')), OSError)):
            with self.subTest(error=error.__name__):
                with self.assertLogs('local_agentic_triage', level='ERROR') as logs, self.assertRaises(error):
                    deliver(runner, make_config(), 'hello', ATTACHMENTS)
                self.assertEqual(len(seen['paths']), 2)
                self.assertFalse(Path(seen['paths'][0]).parent.exists())
                self.assertNotIn('PRIVATE', '\n'.join(logs.output))

    def test_materialization_failure_still_sends_text_only(self):
        runner, seen = self.capture()
        with patch('tempfile.mkdtemp', side_effect=OSError('PRIVATE disk')), \
             self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            deliver(runner, make_config(), 'hello', ATTACHMENTS)
        self.assertEqual(seen['input'], 'hello')
        self.assertEqual(logs.output, ['ERROR:local_agentic_triage:mail_attachment_failed error=os_error'])

    def test_partial_materialization_is_cleaned_and_sends_text_only(self):
        created = []
        real_mkdtemp = tempfile.mkdtemp

        def mkdtemp(**kwargs):
            created.append(real_mkdtemp(**kwargs))
            return created[-1]
        runner, seen = self.capture()
        duplicate = [ATTACHMENTS[0], ATTACHMENTS[0]]
        with patch('tempfile.mkdtemp', side_effect=mkdtemp), \
             self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            deliver(runner, make_config(), 'hello', duplicate)
        self.assertEqual(seen['input'], 'hello')
        self.assertEqual(logs.output, ['ERROR:local_agentic_triage:mail_attachment_failed error=file_exists_error'])
        self.assertFalse(Path(created[0]).exists())

    def test_unsafe_filenames_are_never_materialized(self):
        for filename in ('../event.ics', 'dir/event.ics', '.hidden.ics', 'event 1.ics', 'event\n.ics', '', None):
            runner, seen = self.capture()
            with self.subTest(filename=filename), patch('tempfile.mkdtemp') as mkdtemp, \
                 self.assertLogs('local_agentic_triage', level='ERROR') as logs:
                deliver(runner, make_config(), 'hello', [Attachment(filename, 'x')])
            mkdtemp.assert_not_called()
            self.assertEqual(seen['input'], 'hello')
            self.assertIn('mail_attachment_failed error=value_error', '\n'.join(logs.output))

    def test_no_attachments_create_no_directory(self):
        runner, seen = self.capture()
        with patch('tempfile.mkdtemp') as mkdtemp:
            deliver(runner, make_config(), 'hello', [])
        mkdtemp.assert_not_called()
        self.assertEqual(seen['input'], 'hello')


UNTRUSTED_MEDIA = 'ok\nMEDIA:/Users/example/private'
# Hermes recognizes MEDIA: case-insensitively anywhere in the text, not only on bare lines.
HERMES_MEDIA = re.compile(r'(?i)media:')


class DeliverMediaNeutralizationTests(unittest.TestCase):
    def capture(self):
        seen = {}

        def run(argv, **kwargs):
            seen.update(argv=argv, input=kwargs['input'],
                        existing={path for path in re.findall(r'(?m)^MEDIA:(.*)$', kwargs['input'])
                                  if Path(path).is_file()})
            return subprocess.CompletedProcess(argv, 0, '', '')
        return Mock(side_effect=run), seen

    def test_untrusted_media_directives_cannot_attach_without_attachments(self):
        texts = (UNTRUSTED_MEDIA,
                 '**Inbox**: od **Bob MEDIA:/Users/example/private.pdf** (a@b.invalid)\n> ok',
                 'ok\n  media:/Users/example/private', 'ok\n"MEDIA: /Users/example/private"',
                 'ok\n**Media:~/private**')
        for text in texts:
            runner, seen = self.capture()
            with self.subTest(text=text), self.assertNoLogs('local_agentic_triage', level='DEBUG'):
                deliver(runner, make_config(), text)
            self.assertIsNone(HERMES_MEDIA.search(seen['input']))
            self.assertEqual(len(seen['input']), len(text))
            self.assertIn('/private', seen['input'])
            self.assertEqual(seen['argv'], [make_config().hermes, 'send', '--to', make_config().channel])

    def test_only_materialized_paths_remain_media_directives(self):
        runner, seen = self.capture()
        with self.assertNoLogs('local_agentic_triage', level='DEBUG'):
            deliver(runner, make_config(), UNTRUSTED_MEDIA, ATTACHMENTS)
        lines = seen['input'].split('\n')
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0], 'ok')
        self.assertIsNone(HERMES_MEDIA.search('\n'.join(lines[:2])))
        self.assertTrue(all(re.fullmatch(r'MEDIA:/\S+', line) for line in lines[2:]))
        trusted = [line[len('MEDIA:'):] for line in lines[2:]]
        self.assertEqual([Path(path).name for path in trusted], ['event-1.ics', 'event-2.ics'])
        self.assertEqual(set(trusted), seen['existing'])
        self.assertEqual(len(HERMES_MEDIA.findall(seen['input'])), 2)
        self.assertEqual(seen['argv'], [make_config().hermes, 'send', '--to', make_config().channel])

    def test_ordinary_text_is_sent_unchanged(self):
        text = '**Inbox**: od **Multimedia Shop** (media@shop.invalid)\n> Plan: 10:00, MEDIA plan; /tmp/x'
        for attachments in ((), ATTACHMENTS):
            runner, seen = self.capture()
            with self.subTest(attachments=len(attachments)):
                deliver(runner, make_config(), text, attachments)
            self.assertEqual(seen['input'].split('\nMEDIA:')[0], text)
