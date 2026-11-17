import subprocess
import unittest
from unittest.mock import Mock, patch

from helpers import make_config
from local_agentic_triage.reporting.report import deliver, render_notification


class RenderNotificationTests(unittest.TestCase):
    def _row(self, **kw):
        base = {'sender': 'Example <ex@example.invalid>', 'summary': 'Synthetic summary.',
                'sent_at': '2026-10-31T00:27:00+00:00'}
        return {**base, **kw}

    def test_neutral_format_with_date(self):
        text = render_notification(self._row(), 1900)
        self.assertEqual(text, 'Od **Example <ex@example.invalid>** z dnia 31 paź, 01:27\n> Synthetic summary.')

    def test_missing_date_renders_fallback(self):
        text = render_notification(self._row(sent_at=None), 1900)
        self.assertIn('data wiadomości niedostępna', text)
        self.assertIn('Synthetic summary.', text)

    def test_oversize_raises(self):
        with self.assertRaises(ValueError):
            render_notification(self._row(summary='x' * 2000), 1900)

    def test_invalid_limit_raises(self):
        with self.assertRaises(ValueError):
            render_notification(self._row(), 50)
        with self.assertRaises(ValueError):
            render_notification(self._row(), 2001)

    def test_empty_summary_raises(self):
        with self.assertRaises(ValueError):
            render_notification(self._row(summary=''), 1900)

    def test_none_summary_raises(self):
        with self.assertRaises(ValueError):
            render_notification(self._row(summary=None), 1900)

    def test_special_summary_chars_pass_through(self):
        row = self._row(summary='*Ważne* (4 listopada)')
        text = render_notification(row, 1900)
        self.assertIn('*Ważne* (4 listopada)', text)


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
