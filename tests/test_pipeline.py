import json
import unittest
import urllib.error
from unittest.mock import MagicMock, Mock, patch

from helpers import make_config
from local_agentic_triage.classifier.helpers import ModelContentJsonInvalid
from local_agentic_triage.common.diagnostics import CommandError
from local_agentic_triage.classifier.input import ContextBoundMail
from local_agentic_triage.mail.pipeline import diagnose, poll


def _runner_ok():
    return Mock(return_value=Mock(returncode=0))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('local_agentic_triage.mail.pipeline.extract_events', return_value=[])
        self.extractor = patcher.start()
        self.addCleanup(patcher.stop)
        self.summarizer = Mock(return_value='summary')

    def run_poll(self, *args, **kwargs):
        kwargs.setdefault('summarizer', self.summarizer)
        return poll(*args, **kwargs)

    def run_diagnose(self, *args, **kwargs):
        kwargs.setdefault('summarizer', self.summarizer)
        return diagnose(*args, **kwargs)

    @staticmethod
    def event_result(*entries):
        return {'status': 'ok', 'results': list(entries)}

    # ── required behavioral tests ────────────────────────────────────────────

    def test_poll_summarizes_then_classifies_with_the_summary(self):
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.raw.return_value = b'From: a@b.invalid\n\nbody'
        box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
        order = []
        summarizer = Mock(side_effect=lambda mail: order.append('summarize') or 'Synthetic summary.')
        classifier = Mock(side_effect=lambda mail, summary: order.append('classify') or 'archive')
        runner = _runner_ok()

        with self.assertLogs('local_agentic_triage', level='INFO'):
            self.assertEqual(self.run_poll(box, make_config(), classifier, runner=runner,
                                           summarizer=summarizer), 0)

        self.assertEqual(order, ['summarize', 'classify'])
        classifier.assert_called_once_with(summarizer.call_args.args[0], 'Synthetic summary.')
        self.assertIsInstance(summarizer.call_args.args[0], ContextBoundMail)
        self.assertIs(self.extractor.call_args.args[0], summarizer.call_args.args[0])
        self.assertIn('Synthetic summary.', runner.call_args.kwargs['input'])
        box.archive.assert_called_once_with('9')

    def test_poll_fits_once_and_shares_one_object_with_all_model_stages(self):
        from local_agentic_triage.classifier.input import fit_to_context as real_fit_to_context
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.raw.return_value = b'From: a@b.invalid\n\nbody'
        box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
        summarizer = Mock(return_value='Synthetic summary.')
        classifier = Mock(return_value='archive')
        extractor = Mock(return_value=[])
        with patch('local_agentic_triage.mail.pipeline.fit_to_context', wraps=real_fit_to_context) as fitting, \
             self.assertLogs('local_agentic_triage', level='INFO'):
            self.assertEqual(poll(box, make_config(), classifier, summarizer=summarizer,
                                  extractor=extractor, runner=_runner_ok()), 0)
        fitting.assert_called_once()
        context_bound_mail=summarizer.call_args.args[0]
        self.assertIs(classifier.call_args.args[0],context_bound_mail)
        self.assertIs(extractor.call_args.args[0],context_bound_mail)

    def test_diagnose_fits_once_and_shares_one_object(self):
        from local_agentic_triage.classifier.input import fit_to_context as real_fit_to_context
        box = Mock(validity='77')
        box.raw.return_value = b'From: a@b.invalid\n\nbody'
        box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
        summarizer = Mock(return_value='Synthetic summary.')
        classifier = Mock(return_value='archive')
        with patch('local_agentic_triage.mail.pipeline.fit_to_context', wraps=real_fit_to_context) as fitting, \
             self.assertLogs('local_agentic_triage', level='INFO'):
            self.assertEqual(diagnose(box, make_config(), '9', classifier,
                                      summarizer=summarizer), 0)
        fitting.assert_called_once()
        self.assertIs(classifier.call_args.args[0],summarizer.call_args.args[0])

    def test_pipeline_names_the_fitted_stage_context_bound_mail(self):
        import inspect
        import local_agentic_triage.mail.pipeline as pipeline
        source=inspect.getsource(pipeline)
        self.assertGreaterEqual(source.count('context_bound_mail = fit_to_context('),2)
        self.assertNotIn('mail_content_truncated',source)

    def test_notification_and_vulcan_use_the_original_mail(self):
        sender = 'Dziennik ' + 'V' * 1100 + ' <noreply@vulcan.net.pl>'
        raw = ('Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: ' + sender + '\n\nbody').encode()
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.raw.return_value = raw
        config = make_config()
        runner = _runner_ok()
        with patch('local_agentic_triage.mail.pipeline.delegate_to_vulcan_handler') as delegate, \
             self.assertLogs('local_agentic_triage', level='INFO'):
            self.assertEqual(self.run_poll(box, config, Mock(return_value='leave'), runner=runner), 0)
        sent_text = runner.call_args.kwargs['input']
        self.assertIn(f'Od **{sender}** z dnia', sent_text)
        self.assertNotIn('[content truncated]', sent_text)
        delegate.assert_called_once_with('9', raw, config)

    def test_mandatory_summary_for_leave(self):
        """Every mail, including leave, must produce a non-empty summary."""
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'From: a@b.invalid\n\nbody'
        box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
        self.summarizer.return_value = 'Synthetic leave summary.'
        classifier = Mock(return_value='leave')
        runner = _runner_ok()
        with self.assertLogs('local_agentic_triage', level='INFO'):
            self.assertEqual(self.run_poll(box, make_config(), classifier, runner=runner), 0)
        box.leave.assert_called_once_with('9')
        runner.assert_called_once()
        sent_text = runner.call_args.kwargs['input']
        self.assertIn('Synthetic leave summary.', sent_text)

    def test_notify_before_archive(self):
        """Discord notification is sent before archive IMAP mutation."""
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: a@b.invalid\n\nbody'
        self.summarizer.return_value = 'Archive summary.'
        classifier = Mock(return_value='archive')
        order = []
        def capture_send(*args, **kwargs):
            order.append('send')
            return Mock(returncode=0)
        def capture_archive(uid):
            order.append('archive')
        box.archive.side_effect = capture_archive
        runner = Mock(side_effect=capture_send)
        with self.assertLogs('local_agentic_triage', level='INFO'):
            self.assertEqual(self.run_poll(box, make_config(), classifier, runner=runner), 0)
        self.assertEqual(order, ['send', 'archive'])

    def test_notify_before_leave(self):
        """Discord notification is sent before leave IMAP mutation."""
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'From: a@b.invalid\n\nbody'
        box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
        self.summarizer.return_value = 'Leave summary.'
        classifier = Mock(return_value='leave')
        order = []
        def capture_send(*args, **kwargs):
            order.append('send')
            return Mock(returncode=0)
        def capture_leave(uid):
            order.append('leave')
        box.leave.side_effect = capture_leave
        runner = Mock(side_effect=capture_send)
        with self.assertLogs('local_agentic_triage', level='INFO'):
            self.assertEqual(self.run_poll(box, make_config(), classifier, runner=runner), 0)
        self.assertEqual(order, ['send', 'leave'])

    def test_send_failure_blocks_archive_disposition(self):
        """If Discord send fails, archive IMAP mutation does not happen."""
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: a@b.invalid\n\nbody'
        classifier = Mock(return_value='archive')
        runner = Mock(side_effect=RuntimeError('PRIVATE send error'))
        with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            self.assertEqual(self.run_poll(box, make_config(), classifier, runner=runner), 1)
        box.archive.assert_not_called()
        output = '\n'.join(logs.output)
        self.assertNotIn('PRIVATE', output)

    def test_send_failure_blocks_leave_disposition(self):
        """If Discord send fails, leave IMAP mutation does not happen."""
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'From: a@b.invalid\n\nbody'
        box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
        classifier = Mock(return_value='leave')
        runner = Mock(side_effect=RuntimeError('PRIVATE send error'))
        with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            self.assertEqual(self.run_poll(box, make_config(), classifier, runner=runner), 1)
        box.leave.assert_not_called()
        output = '\n'.join(logs.output)
        self.assertNotIn('PRIVATE', output)

    def test_summary_failure_causes_no_send_no_disposition(self):
        """If summary generation fails, no Discord send and no IMAP mutation."""
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'From: a@b.invalid\n\nbody'
        box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
        self.summarizer.side_effect = ModelContentJsonInvalid('PRIVATE')
        classifier = Mock()
        runner = _runner_ok()
        with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            self.assertEqual(self.run_poll(box, make_config(), classifier, runner=runner), 1)
        runner.assert_not_called()
        box.leave.assert_not_called()
        box.archive.assert_not_called()
        classifier.assert_not_called()
        self.assertNotIn('PRIVATE', '\n'.join(logs.output))

    def test_calendar_failure_does_not_block_send_or_disposition(self):
        """Calendar enrichment failure must not block notification or disposition."""
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: a@b.invalid\n\nbody'
        classifier = Mock(return_value='archive')
        events = Mock(side_effect=CommandError('event_command_failed', 17))
        runner = _runner_ok()
        with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            self.assertEqual(self.run_poll(box, make_config(), classifier, events, runner=runner), 0)
        runner.assert_called_once()
        box.archive.assert_called_once_with('9')
        output = '\n'.join(logs.output)
        self.assertIn('mail_event_failed', output)
        self.assertNotIn('mail_item_failed', output)

    def test_vulcan_failure_does_not_block_send_or_disposition(self):
        """eduVULCAN spawn failure must not block notification or disposition."""
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'From: noreply@vulcan.net.pl\n\nbody'
        box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
        classifier = Mock(return_value='leave')
        runner = _runner_ok()
        with patch('os.posix_spawn', side_effect=OSError('PRIVATE spawn error')):
            with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
                self.assertEqual(self.run_poll(box, make_config(), classifier, runner=runner), 0)
        runner.assert_called_once()
        box.leave.assert_called_once_with('9')
        output = '\n'.join(logs.output)
        self.assertIn('agentic_vulcan_failed', output)
        self.assertNotIn('PRIVATE', output)
        self.assertNotIn('mail_item_failed', output)

    def test_renderer_receives_destination_and_is_neutral_for_both(self):
        """render_notification is called with destination; format is neutral for archive and leave."""
        from local_agentic_triage.reporting.report import render_notification
        with patch('local_agentic_triage.mail.pipeline.render_notification',
                   wraps=render_notification) as render_spy:
            for destination in ('archive', 'leave'):
                render_spy.reset_mock()
                box = Mock(validity='77')
                box.candidates.return_value = ['9']
                box.source_key.return_value = 'key'
                box.raw.return_value = b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: a@b.invalid\n\nbody'
                self.summarizer.return_value = 'Synthetic.'
                classifier = Mock(return_value=destination)
                runner = _runner_ok()
                with self.assertLogs('local_agentic_triage', level='INFO'):
                    self.assertEqual(self.run_poll(box, make_config(), classifier, runner=runner), 0)
                self.assertEqual(render_spy.call_count, 1)
                call_destination = render_spy.call_args.args[1]
                self.assertEqual(call_destination, destination)
                sent_text = runner.call_args.kwargs['input']
                # Neutral format: not labeled "Archiwum"
                self.assertNotIn('**Archiwum**', sent_text)
                self.assertIn('Synthetic.', sent_text)

    def test_oversize_notification_blocks_send_and_disposition(self):
        """If the rendered notification exceeds discord_limit, no send and no disposition."""
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: a@b.invalid\n\nbody'
        self.summarizer.return_value = 'x' * 200
        classifier = Mock(return_value='archive')
        runner = _runner_ok()
        with self.assertLogs('local_agentic_triage', level='ERROR'):
            self.assertEqual(self.run_poll(box, make_config(discord_limit=100), classifier, runner=runner), 1)
        runner.assert_not_called()
        box.archive.assert_not_called()
        box.leave.assert_not_called()

    def test_invalid_notification_prevents_all_optional_side_effects(self):
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.raw.return_value = b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: noreply@vulcan.net.pl\n\nbody'
        self.summarizer.return_value = 'x' * 200
        classifier = Mock(return_value='archive')
        events = Mock()
        runner = _runner_ok()
        with patch('local_agentic_triage.mail.pipeline.delegate_to_vulcan_handler') as delegate_mock, \
             self.assertLogs('local_agentic_triage', level='ERROR'):
            self.assertEqual(self.run_poll(box, make_config(discord_limit=100), classifier,
                                  events, runner=runner), 1)
        delegate_mock.assert_not_called()
        self.extractor.assert_not_called()
        events.assert_not_called()
        runner.assert_not_called()
        box.archive.assert_not_called()
        box.leave.assert_not_called()

    # ── existing behaviors retained ──────────────────────────────────────────

    def test_diagnose_is_read_only_and_reports_safe_specific_error(self):
        box = Mock()
        box.validity = '77'
        box.raw.return_value = b'Date: Wed, 01 Jan 2025 12:00:00 +0100\nFrom: private@example.invalid\n\nsecret'
        classifier = Mock(side_effect=ModelContentJsonInvalid('PRIVATE OUTPUT'))
        with self.assertLogs('local_agentic_triage', level='INFO') as logs:
            self.assertEqual(self.run_diagnose(box, make_config(), '9', classifier), 1)
        output = '\n'.join(logs.output)
        self.assertIn('mail_diagnosis_failed uid=9 uidvalidity=77 stage=classify error=model_content_json_invalid', output)
        for private in ('private@example.invalid', 'secret', 'PRIVATE OUTPUT'):
            self.assertNotIn(private, output)
        box.raw.assert_called_once_with('9')
        box.internal_date.assert_not_called()
        box.archive.assert_not_called()
        box.leave.assert_not_called()

    def test_diagnose_rejects_uidvalidity_mismatch_before_read(self):
        box = Mock()
        box.validity = '77'
        classifier = Mock()
        with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            self.assertEqual(self.run_diagnose(box, make_config(), '9', classifier, expected_validity='76'), 1)
        self.assertIn('mail_diagnosis_identity_failed uid=9 error=uidvalidity_mismatch', '\n'.join(logs.output))
        box.raw.assert_not_called()
        box.internal_date.assert_not_called()
        classifier.assert_not_called()

    def test_diagnose_internal_date_success_has_no_mutations(self):
        box = Mock()
        box.validity = '77'
        box.raw.return_value = b'From: synthetic@example.invalid\n\nbody'
        box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
        self.summarizer.return_value = 'synthetic'
        classifier = Mock(return_value='leave')
        with self.assertLogs('local_agentic_triage', level='INFO') as logs:
            self.assertEqual(self.run_diagnose(box, make_config(), '9', classifier, expected_validity='77'), 0)
        self.assertIn('mail_diagnosis_completed uid=9 uidvalidity=77 stage=classify', '\n'.join(logs.output))
        classifier.assert_called_once_with(self.summarizer.call_args.args[0], 'synthetic')
        box.internal_date.assert_called_once_with('9')
        box.archive.assert_not_called()
        box.leave.assert_not_called()

    def test_archive_without_valid_header_date_uses_imap_internal_date(self):
        for raw in (b'From: sender\n\nbody', b'Date: not-a-date\nFrom: sender\n\nbody',
                    b'Date: Fri, 31 Oct 2026 01:27:00\nFrom: sender\n\nbody'):
            box = Mock(validity='77')
            box.candidates.return_value = ['9']
            box.source_key.return_value = 'key'
            box.raw.return_value = raw
            box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
            classifier = Mock(return_value='archive')
            events = Mock(return_value=self.event_result({'index': 0, 'outcome': 'skipped', 'reason': 'missing_date'}))
            runner = _runner_ok()
            with self.assertLogs('local_agentic_triage', level='INFO'):
                self.assertEqual(self.run_poll(box, make_config(), classifier, events, runner=runner), 0)
            events.assert_called_once()
            box.archive.assert_called_once_with('9')
            box.leave.assert_not_called()

    def test_event_command_failure_is_absorbed_and_logs_only_safe_failure(self):
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'\nbody'
        box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
        classifier = Mock(return_value='leave')
        events = Mock(side_effect=CommandError('event_command_failed', 17))
        runner = _runner_ok()
        with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            self.assertEqual(self.run_poll(box, make_config(), classifier, events, runner=runner), 0)
        output = '\n'.join(logs.output)
        self.assertEqual(output.count('mail_event_failed'), 1)
        self.assertIn('mail_event_failed uid=9 error=event_command_failed returncode=17', output)
        self.assertNotIn('mail_item_failed', output)
        box.leave.assert_called_once_with('9')
        box.archive.assert_not_called()

    def test_archive_event_command_failure_logs_bounded_returncode_and_archives(self):
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: synthetic\n\nbody'
        classifier = Mock(return_value='archive')
        events = Mock(side_effect=CommandError('event_command_failed', 17))
        runner = _runner_ok()
        with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            self.assertEqual(self.run_poll(box, make_config(), classifier, events, runner=runner), 0)
        output = '\n'.join(logs.output)
        self.assertEqual(output.count('mail_event_failed'), 1)
        self.assertIn('mail_event_failed uid=9 error=event_command_failed returncode=17', output)
        self.assertNotIn('mail_item_failed', output)
        box.archive.assert_called_once_with('9')
        box.leave.assert_not_called()

    def test_event_model_http_failure_is_sanitized_optional_enrichment(self):
        from local_agentic_triage.mail.event_pipeline import process_events
        event = {'title': 'PRIVATE EVENT', 'start': '2099-12-01', 'end': None, 'date_note': None, 'location': None}
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: secret@example.invalid\n\nprivate body'
        classifier = Mock(return_value='leave')
        self.extractor.return_value = [event]
        runner = _runner_ok()
        with patch('local_agentic_triage.events.calendar.Calendar.nearby', return_value=[]), \
             patch('local_agentic_triage.events.calendar.Calendar.create') as create, \
             patch('subprocess.run', side_effect=AssertionError('decision subprocess')), \
             patch('urllib.request.OpenerDirector.open', side_effect=urllib.error.URLError('PRIVATE_SENTINEL')), \
             self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            self.assertEqual(self.run_poll(box, make_config(), classifier, process_events, runner=runner), 0)
        output = '\n'.join(logs.output)
        self.assertIn('mail_event_failed uid=9 error=model_request_failed', output)
        self.assertNotIn('returncode=', output)
        self.assertNotIn('mail_item_failed', output)
        for private in ('PRIVATE_SENTINEL', 'PRIVATE EVENT', 'secret@example.invalid', 'private body', '2099-12-01'):
            self.assertNotIn(private, output)
        create.assert_not_called()
        box.leave.assert_called_once_with('9')
        box.archive.assert_not_called()

    def test_event_results_are_logged_without_private_event_data(self):
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'From: secret@example.invalid\nSubject: private subject\n\nprivate body'
        box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
        classifier = Mock(return_value='leave')
        self.extractor.return_value = [{'title': 'PRIVATE EVENT', 'start': '2026-11-01', 'end': None, 'date_note': None, 'location': None}]
        events = Mock(return_value={'status': 'ok', 'results': [{'index': 0, 'outcome': 'skipped', 'reason': 'too_soon'}]})
        runner = _runner_ok()
        with self.assertLogs('local_agentic_triage', level='INFO') as logs:
            self.assertEqual(self.run_poll(box, make_config(), classifier, events, runner=runner), 0)
        output = '\n'.join(logs.output)
        self.assertIn('mail_event_result uid=9 index=0 outcome=skipped reason=too_soon', output)
        for private in ('PRIVATE EVENT', 'private subject', 'private body', 'secret@example.invalid', '2026-11-01'):
            self.assertNotIn(private, output)
        box.leave.assert_called_once_with('9')

    def test_invalid_internal_date_stops_before_private_processing(self):
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'key'
        box.raw.return_value = b'From: sender\n\nbody'
        box.internal_date.side_effect = ValueError('PRIVATE INTERNAL DATE')
        classifier = Mock()
        events = Mock()
        runner = _runner_ok()
        with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            self.assertEqual(self.run_poll(box, make_config(), classifier, events, runner=runner), 1)
        output = '\n'.join(logs.output)
        self.assertIn('uid=9 uidvalidity=77 stage=read error=value_error', output)
        self.assertNotIn('PRIVATE', output)
        classifier.assert_not_called()
        events.assert_not_called()
        box.archive.assert_not_called()
        box.leave.assert_not_called()
        runner.assert_not_called()

    def test_info_and_error_logs_identify_action_without_private_data(self):
        box = Mock(validity='77')
        box.candidates.return_value = ['9']
        box.source_key.return_value = 'private-key'
        box.raw.return_value = b'From: secret@example.invalid\nSubject: private subject\n\nprivate body'
        box.internal_date.return_value = '2026-10-31T01:27:00+01:00'
        classifier = Mock(side_effect=ValueError('archive summary'))
        events = Mock()
        runner = _runner_ok()
        with self.assertLogs('local_agentic_triage', level='INFO') as logs:
            self.assertEqual(self.run_poll(box, make_config(), classifier, events, runner=runner), 1)
        output = '\n'.join(logs.output)
        for expected in ('mail_poll_started', 'mail_candidates_discovered count=1',
                         'mail_item_started item=1 total=1 uid=9 uidvalidity=77',
                         'mail_item_action item=1 uid=9 stage=classify',
                         'mail_item_failed item=1 uid=9 uidvalidity=77 stage=classify error=value_error',
                         'mail_poll_completed candidates=1 completed=0 failures=1'):
            self.assertIn(expected, output)
        for private in ('private-key', 'secret@example.invalid', 'private subject', 'private body'):
            self.assertNotIn(private, output)
