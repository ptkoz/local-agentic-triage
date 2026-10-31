import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from local_agentic_triage.mail.pipeline import poll
from local_agentic_triage.reporting.store import Store
from helpers import make_config
from local_agentic_triage.common.diagnostics import CommandError

class PipelineTests(unittest.TestCase):
    def test_archive_without_valid_header_date_uses_imap_internal_date(self):
        for raw in (b'From: sender\n\nbody',b'Date: not-a-date\nFrom: sender\n\nbody',b'Date: Fri, 31 Oct 2026 01:27:00\nFrom: sender\n\nbody'):
            with tempfile.TemporaryDirectory() as d:
                store=Store(Path(d)/'db'); box=Mock()
                box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=raw; box.internal_date.return_value='2026-10-31T01:27:00+01:00'
                classifier=Mock(return_value={'destination':'archive','summary':'summary','events':[{'title':'X','start':None,'end':None,'date_note':None}]}); events=Mock()
                self.assertEqual(poll(box,store,make_config(),classifier,events),0)
                events.assert_called_once(); box.archive.assert_called_once_with('9'); box.leave.assert_not_called()
                self.assertEqual(store.prepared('key')['sent_at'],'2026-10-31T01:27:00+01:00')
                store.close()

    def test_command_failure_logs_uid_and_numeric_returncode(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock()
            box.candidates.return_value=['9']; box.source_key.return_value='key'
            box.raw.return_value=b'\nbody'
            box.internal_date.return_value='2026-10-31T01:27:00+01:00'
            classifier=Mock(return_value={'destination':'leave','summary':None,'events':[]})
            events=Mock(side_effect=CommandError('event_command_failed',17))
            with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),1)
            self.assertIn('uid=9 stage=events error=event_command_failed returncode=17','\n'.join(logs.output))
            store.close()

    def test_archive_retry_does_not_reclassify_or_rerun_events(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(); box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=b'Date: Fri, 31 Oct 2026 01:27:00 +0100\nFrom: original\n\nbody'
            classify=Mock(return_value={'destination':'archive','summary':'Oryginalny zakup.','events':[{'title':'X','start':None,'end':None,'date_note':None}]}); events=Mock()
            box.archive.side_effect=RuntimeError('PRIVATE ERROR')
            with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                self.assertEqual(poll(box,store,make_config(),classify,events),1)
            box.internal_date.assert_not_called()
            self.assertNotIn('PRIVATE',str(logs.output))
            self.assertEqual(store.prepared('key')['sender'],'original'); self.assertEqual(store.pending(),[])
            box.archive.side_effect=None
            self.assertEqual(poll(box,store,make_config(),classify,events),0)
            self.assertEqual(classify.call_count,1); self.assertEqual(events.call_count,1); self.assertEqual(box.raw.call_count,1)
            self.assertEqual(store.pending()[0]['summary'],'Oryginalny zakup.')
            store.close()
    def test_leave_no_row_and_event_failure_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(); box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=b'\nbody'; box.internal_date.return_value='2026-10-31T01:27:00+01:00'
            classifier=Mock(return_value={'destination':'leave','summary':None,'events':[]}); events=Mock()
            self.assertEqual(poll(box,store,make_config(),classifier,events),0)
            box.leave.assert_called_once_with('9'); self.assertIsNone(store.prepared('key')); box.archive.assert_not_called()
            box.reset_mock(); box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=b'\nbody'; events.side_effect=ValueError('private')
            with self.assertLogs('local_agentic_triage',level='ERROR'):
                self.assertEqual(poll(box,store,make_config(),classifier,events),1)
            box.leave.assert_not_called(); box.archive.assert_not_called(); store.close()
    def test_prepared_failure_then_independent_candidate_and_ready_retry(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); store.prepare('old','original','frozen',sent_at='2026-10-31T01:27:00+01:00')
            box=Mock(); box.candidates.return_value=['9','12']; box.source_key.side_effect=lambda uid: 'old' if uid=='9' else 'new'
            box.archive.side_effect=lambda uid: (_ for _ in ()).throw(RuntimeError('private')) if uid=='9' else None
            box.raw.return_value=b'Date: Fri, 31 Oct 2026 01:27:00 +0100\nFrom: synthetic\n\nbody'
            classifier=Mock(return_value={'destination':'archive','summary':'new summary','events':[]}); events=Mock()
            with self.assertLogs('local_agentic_triage',level='ERROR'):
                self.assertEqual(poll(box,store,make_config(),classifier,events),1)
            self.assertIsNone(store.prepared('old')['ready_at'])
            self.assertEqual([r['source_key'] for r in store.pending()],['new'])
            self.assertEqual(classifier.call_count,1); self.assertEqual(events.call_count,1)
            box.archive.side_effect=None
            self.assertEqual(poll(box,store,make_config(),classifier,events),0)
            self.assertEqual(classifier.call_count,1); self.assertEqual(events.call_count,1)
            store.close()

    def test_invalid_internal_date_stops_before_private_processing(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock()
            box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=b'From: sender\n\nbody'
            box.internal_date.side_effect=ValueError('PRIVATE INTERNAL DATE')
            classifier=Mock(); events=Mock()
            with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),1)
            output='\n'.join(logs.output)
            self.assertIn('uid=9 stage=read error=value_error',output); self.assertNotIn('PRIVATE',output)
            classifier.assert_not_called(); events.assert_not_called(); box.archive.assert_not_called(); box.leave.assert_not_called()
            self.assertIsNone(store.prepared('key')); store.close()

    def test_oversized_archive_row_stops_before_events_store_and_imap(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock()
            box.candidates.return_value=['9']; box.source_key.return_value='key'
            box.raw.return_value=b'Date: Fri, 31 Oct 2026 01:27:00 +0100\nFrom: sender@example.invalid\n\nbody'
            classifier=Mock(return_value={'destination':'archive','summary':'x'*200,'events':[{'title':'X','start':'2099-01-01','end':None,'date_note':None}]})
            events=Mock()
            with self.assertLogs('local_agentic_triage',level='ERROR'):
                self.assertEqual(poll(box,store,make_config(discord_limit=100),classifier,events),1)
            events.assert_not_called(); box.archive.assert_not_called(); box.leave.assert_not_called()
            self.assertIsNone(store.prepared('key')); store.close()

    def test_oversized_prepared_row_stops_archive_retry(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db')
            store.prepare('key','sender@example.invalid','x'*200,sent_at='2026-10-31T01:27:00+01:00')
            box=Mock(); box.candidates.return_value=['9']; box.source_key.return_value='key'
            with self.assertLogs('local_agentic_triage',level='ERROR'):
                self.assertEqual(poll(box,store,make_config(discord_limit=100)),1)
            box.archive.assert_not_called(); self.assertIsNone(store.prepared('key')['ready_at'])
            store.close()

    def test_info_and_error_logs_identify_action_without_private_data(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock()
            box.candidates.return_value=['9']; box.source_key.return_value='private-key'
            box.raw.return_value=b'From: secret@example.invalid\nSubject: private subject\n\nprivate body'
            box.internal_date.return_value='2026-10-31T01:27:00+01:00'
            classifier=Mock(side_effect=ValueError('archive summary')); events=Mock()
            with self.assertLogs('local_agentic_triage',level='INFO') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),1)
            output='\n'.join(logs.output)
            for expected in ('mail_poll_started','mail_candidates_discovered count=1','mail_item_started item=1 total=1 uid=9','mail_item_action item=1 uid=9 stage=classify','mail_item_failed item=1 uid=9 stage=classify error=value_error','mail_poll_completed candidates=1 completed=0 failures=1'):
                self.assertIn(expected,output)
            for private in ('private-key','secret@example.invalid','private subject','private body'):
                self.assertNotIn(private,output)
            store.close()
