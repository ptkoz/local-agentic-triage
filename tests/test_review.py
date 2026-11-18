import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import subprocess
from datetime import datetime

from helpers import make_config
from local_agentic_triage.mail.event_pipeline import process_events
from local_agentic_triage.mail.imap import Mailbox
from local_agentic_triage.mail.pipeline import poll
from test_imap import SELECT, SEARCH, done
from test_events import decision_response, prompt as event_prompt

EVENT = {'title': '"; doShellScript("evil"); //', 'start': '2099-12-01', 'end': '2099-12-02',
         'date_note': None, 'location': None}


class ReviewTests(unittest.TestCase):
    def test_toolless_decision_and_adapter_data(self):
        from local_agentic_triage.events.calendar import Calendar, SCRIPT

        def run(args, **kw):
            self.assertEqual(args, ['/usr/bin/osascript', '-l', 'JavaScript', '-e', SCRIPT])
            self.assertNotIn(EVENT['title'], SCRIPT)
            data = json.loads(kw['input'])
            self.assertEqual(data['calendar'], 'Test Calendar')
            self.assertEqual(data['operation'], 'search')
            return subprocess.CompletedProcess(args, 0, json.dumps([]), '')

        with patch('subprocess.run', side_effect=run) as runner, \
             patch('urllib.request.OpenerDirector.open',
                   side_effect=[decision_response({'outcome': 'create'}), decision_response(EVENT)]) as opened:
            [result] = process_events([EVENT], make_config())
        self.assertEqual(result.outcome, 'prepared')
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(result.attachment.filename, 'event-1.ics')
        self.assertIn('SUMMARY:"\\; doShellScript("evil")\\; //\r\n', result.attachment.content)
        for call in runner.call_args_list:
            self.assertFalse(call.kwargs['shell'])
            self.assertGreater(call.kwargs['timeout'], 0)
        bodies = [json.loads(call.args[0].data) for call in opened.call_args_list]
        self.assertEqual(bodies[0]['messages'][0]['content'], event_prompt())
        self.assertEqual(json.loads(bodies[0]['messages'][1]['content']), {'event': EVENT, 'nearby': []})
        self.assertEqual(json.loads(bodies[1]['messages'][1]['content']), {'event': EVENT})
        self.assertEqual(opened.call_args.kwargs['timeout'], 300)

    def test_archive_guard_blocks_move(self):
        for bad in (SEARCH.replace(b'77', b'78'), SEARCH.replace(b'9 12', b'12')):
            with patch('subprocess.run', side_effect=[done(SELECT), done(bad)]) as run:
                box = Mailbox(make_config())
                with self.assertRaises(ValueError):
                    box.archive('9')
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args.kwargs['input'],
                                 b'a1 SELECT "Test Inbox"\r\na2 UID SEARCH UID 9\r\n')

    def test_all_day_window_includes_entire_seventh_day(self):
        from local_agentic_triage.events.calendar import Calendar
        with patch.object(Calendar, 'call', return_value=[]) as call:
            Calendar(make_config()).nearby(EVENT)
        self.assertEqual(call.call_args.args[0]['upper'], '2099-12-08T23:59:59.999999')

    def test_nearby_normalizes_equal_single_day_adapter_rows(self):
        from local_agentic_triage.events.calendar import Calendar
        cases = (('2099-12-31', '2100-01-01'), ('2028-02-29', '2028-03-01'))
        for start, end in cases:
            row = {'title': 'Synthetic', 'start': start, 'end': start, 'all_day': True, 'location': None}
            with self.subTest(start=start), patch.object(Calendar, 'call', return_value=[row]):
                self.assertEqual(Calendar(make_config()).nearby(EVENT), [row | {'end': end}])
                self.assertEqual(row['end'], start)

    def test_nearby_preserves_canonical_all_day_rows(self):
        from local_agentic_triage.events.calendar import Calendar
        for start, end in (('2099-12-01', '2099-12-02'), ('2099-12-01', '2099-12-05')):
            row = {'title': 'Synthetic', 'start': start, 'end': end, 'all_day': True, 'location': None}
            with self.subTest(end=end), patch.object(Calendar, 'call', return_value=[row]):
                self.assertEqual(Calendar(make_config()).nearby(EVENT), [row])

    def test_nearby_rejects_invalid_equal_or_negative_rows(self):
        from local_agentic_triage.events.calendar import Calendar
        rows = (
            {'title': 'Synthetic', 'start': '2099-99-01', 'end': '2099-99-01', 'all_day': True, 'location': None},
            {'title': 'Synthetic', 'start': '2099-12-02', 'end': '2099-12-01', 'all_day': True, 'location': None},
            {'title': 'Synthetic', 'start': '2099-12-01T10:00:00Z', 'end': '2099-12-01T10:00:00Z',
             'all_day': False, 'location': None},
            {'title': 'Synthetic', 'start': '2099-12-01', 'end': '2099-12-01', 'all_day': False, 'location': None},
        )
        for row in rows:
            with self.subTest(row=row), patch.object(Calendar, 'call', return_value=[row]), \
                 self.assertRaisesRegex(ValueError, 'calendar_search_invalid'):
                Calendar(make_config()).nearby(EVENT)

    def test_event_adapter_failure_is_absorbed_and_archive_completes(self):
        box = Mock(validity='77')
        box.candidates.return_value = ['9']

        box.raw.return_value = (b'Date: Sat, 31 Oct 2026 01:27:00 +0100\n'
                                b'From: Synthetic Sender <sender@example.invalid>\n\nSynthetic body')
        summarize = Mock(return_value='Synthetic summary')
        classify = Mock(return_value='archive')
        process_events = Mock(side_effect=RuntimeError('PRIVATE adapter output'))
        runner = Mock(return_value=Mock(returncode=0))
        with self.assertLogs('local_agentic_triage', level='ERROR') as logs:
            self.assertEqual(poll(box, make_config(), classify, process_events, summarize=summarize,
                                  extract_event_candidates=Mock(return_value=[EVENT]), runner=runner), 0)
        output = '\n'.join(logs.output)
        self.assertEqual(output.count('mail_event_failed'), 1)
        self.assertIn('mail_event_failed uid=9 error=runtime_error', output)
        self.assertNotIn('PRIVATE adapter output', output)
        self.assertNotIn('Synthetic body', output)
        self.assertNotIn('mail_item_failed', output)
        box.archive.assert_called_once_with('9')
        box.leave.assert_not_called()
        runner.assert_called_once()
        sent_text = runner.call_args.kwargs['input']
        self.assertIn('Synthetic summary', sent_text)

    def test_calendar_adapter_rejects_every_operation_except_search(self):
        from local_agentic_triage.events.calendar import Calendar, SCRIPT
        self.assertFalse(hasattr(Calendar, 'create'))
        self.assertIn("if (data.operation !== 'search') throw Error('calendar_operation');", SCRIPT)
        with self.assertRaisesRegex(ValueError, 'calendar_boundary'):
            Calendar(make_config()).call({'operation': 'search', 'calendar': 'Other'})

    def test_attachment_boundary_requires_strictly_more_than_three_days(self):
        from local_agentic_triage.events.calendar import Calendar
        clock = lambda tz=None: datetime(2030, 1, 1, 12, 0, tzinfo=tz)
        timed = EVENT | {'start': '2030-01-02T12:00:00Z', 'end': '2030-01-04T12:00:00Z'}
        all_day = EVENT | {'start': '2030-01-02', 'end': '2030-01-04'}
        cases = ((timed, 'skipped'), (all_day, 'skipped'),
                 (timed | {'end': '2030-01-04T12:00:01Z'}, 'prepared'), (all_day | {'end': '2030-01-05'}, 'prepared'))
        for event, outcome in cases:
            with self.subTest(event=event), patch.object(Calendar, 'nearby', return_value=[]), \
                 patch('urllib.request.OpenerDirector.open',
                       side_effect=[decision_response({'outcome': 'create'}), decision_response(event)]):
                [result] = process_events([event], make_config(), clock=clock)
            self.assertEqual(result.outcome, outcome)
            self.assertEqual(result.attachment is not None, outcome == 'prepared')

    def test_every_uid_guard_rejects_changed_validity_and_missing(self):
        for operation in ('raw', 'leave', 'archive'):
            for bad in (SEARCH.replace(b'77', b'78'), SEARCH.replace(b'9 12', b'12')):
                with patch('subprocess.run', side_effect=[done(SELECT), done(bad)]) as run:
                    box = Mailbox(make_config())
                    with self.assertRaises(ValueError):
                        getattr(box, operation)('9')
                    self.assertEqual(run.call_count, 2)
                    self.assertEqual(run.call_args.kwargs['input'],
                                     b'a1 SELECT "Test Inbox"\r\na2 UID SEARCH UID 9\r\n')

    def test_semantic_outcomes_preserved_and_created_cannot_be_claimed(self):
        from local_agentic_triage.events.calendar import Calendar
        from local_agentic_triage.classifier.events import _validate_duplicate_result
        candidates = [EVENT, EVENT | {'title': 'Different'}, EVENT | {'title': 'Ambiguous'}]
        answers = [{'outcome': 'create'}, EVENT, {'outcome': 'existing'}, {'outcome': 'possible_duplicate'}]
        with patch.object(Calendar, 'nearby', return_value=[]), \
             patch('urllib.request.OpenerDirector.open',
                   side_effect=[decision_response(a) for a in answers]):
            final = list(process_events(candidates, make_config()))
        self.assertEqual([r.attachment is not None for r in final], [True, False, False])
        self.assertEqual([r.outcome for r in final], ['prepared', 'duplicate', 'duplicate'])
        with self.assertRaises(ValueError):
            _validate_duplicate_result({'outcome': 'created'})
