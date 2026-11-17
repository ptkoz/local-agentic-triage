import subprocess
import unittest
from unittest.mock import Mock, patch

from helpers import make_config
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
