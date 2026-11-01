import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock,Mock,patch
from local_agentic_triage.mail.pipeline import diagnose,poll
from local_agentic_triage.reporting.store import Store
from helpers import make_config
from local_agentic_triage.common.diagnostics import CommandError
from local_agentic_triage.classifier.core import ModelContentJsonInvalid

class PipelineTests(unittest.TestCase):
    def setUp(self):
        patcher=patch('local_agentic_triage.mail.pipeline.extract',return_value=[])
        self.extractor=patcher.start()
        self.addCleanup(patcher.stop)

    def disposition(self, *, destination,summary,extracted_events):
        self.extractor.return_value=extracted_events
        return {'destination':destination,'summary':summary}

    @staticmethod
    def event_result(*entries):
        return {'status':'ok','results':list(entries)}
    def test_immediate_reporting_after_ready_for_archive_retry_and_reconciliation(self):
        for mode in ('new','prepared','absent','absent_at_end'):
            for outcome in ('success','failure','disabled','archive_failure'):
                if outcome=='archive_failure' and mode.startswith('absent'): continue
                with self.subTest(mode=mode,outcome=outcome),tempfile.TemporaryDirectory() as d:
                    store=Store(Path(d)/'db'); key='account:inbox:77:9'
                    box=Mock(validity='77'); box.source_key.return_value=key
                    box.candidates.return_value=[] if mode.startswith('absent') else ['9']
                    box.all_uids.side_effect=[{'9'},set()] if mode=='absent_at_end' else None
                    box.all_uids.return_value=set() if mode=='absent' else {'9'}
                    box.raw.return_value=b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: secret@example.invalid\n\nprivate body'
                    if mode!='new': store.prepare(key,'secret@example.invalid','private summary',sent_at='2026-10-31T01:27:00+01:00')
                    if outcome=='archive_failure': box.archive.side_effect=RuntimeError('PRIVATE archive')
                    def delivery(args,**kwargs):
                        row=store.prepared(key)
                        self.assertIsNotNone(row['ready_at']); self.assertIsNone(row['reported_at'])
                        self.assertEqual(args,[make_config().hermes,'send','--to',make_config().channel])
                        self.assertFalse(kwargs['shell'])
                        if outcome=='failure': raise RuntimeError('PRIVATE delivery')
                        return Mock(returncode=0)
                    runner=Mock(side_effect=delivery)
                    classifier=Mock(return_value={'destination':'archive','summary':'private summary'})
                    events=Mock(return_value=self.event_result())
                    kwargs={} if outcome=='disabled' else {'runner':runner}
                    lock=Mock(return_value=MagicMock())
                    with patch('subprocess.run',side_effect=AssertionError('live send forbidden')) as live, \
                         patch('local_agentic_triage.mail.pipeline.Lock',lock), \
                         self.assertLogs('local_agentic_triage',level='INFO') as logs:
                        result=poll(box,store,make_config(),classifier,events,**kwargs)
                    self.assertEqual(result,int(outcome=='archive_failure'))
                    row=store.prepared(key)
                    if outcome=='success':
                        runner.assert_called_once(); self.assertIsNotNone(row['reported_at'])
                        self.assertGreaterEqual(row['reported_at'],row['ready_at'])
                        lock.assert_called_once_with(Path(make_config().delivery_lock),blocking=True)
                    else:
                        self.assertIsNone(row['reported_at'])
                        if outcome!='failure': runner.assert_not_called()
                        else: runner.assert_called_once()
                    if mode!='new': classifier.assert_not_called(); events.assert_not_called()
                    for private in ('secret@example.invalid','private summary','private body','PRIVATE',key):
                        self.assertNotIn(private,' '.join(logs.output))
                    live.assert_not_called(); store.close()

    def test_immediate_delivery_rechecks_acknowledgement_after_shared_lock(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); key='account:inbox:77:9'
            box=Mock(validity='77'); box.source_key.return_value=key
            box.candidates.return_value=['9']; box.all_uids.return_value={'9'}
            box.raw.return_value=b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: sender@example.invalid\n\nbody'
            classifier=Mock(return_value={'destination':'archive','summary':'summary'})
            events=Mock(return_value=self.event_result())
            lock=Mock(return_value=MagicMock())
            lock.return_value.__enter__.side_effect=lambda: store.reported(
                [key],at='2026-11-01T17:00:00+00:00')
            runner=Mock(return_value=Mock(returncode=0))
            with patch('local_agentic_triage.mail.pipeline.Lock',lock):
                self.assertEqual(poll(box,store,make_config(),classifier,events,runner=runner),0)
            runner.assert_not_called()
            self.assertEqual(store.prepared(key)['reported_at'],'2026-11-01T17:00:00+00:00')
            store.close()

    def test_diagnose_is_read_only_and_reports_safe_specific_error(self):
        box=Mock(); box.validity='77'; box.raw.return_value=b'Date: Wed, 01 Jan 2025 12:00:00 +0100\nFrom: private@example.invalid\n\nsecret'
        classifier=Mock(side_effect=ModelContentJsonInvalid('PRIVATE OUTPUT'))
        with self.assertLogs('local_agentic_triage',level='INFO') as logs:
            self.assertEqual(diagnose(box,make_config(),'9',classifier),1)
        output='\n'.join(logs.output)
        self.assertIn('mail_diagnosis_failed uid=9 uidvalidity=77 stage=classify error=model_content_json_invalid',output)
        for private in ('private@example.invalid','secret','PRIVATE OUTPUT'):
            self.assertNotIn(private,output)
        box.raw.assert_called_once_with('9'); box.internal_date.assert_not_called()
        box.archive.assert_not_called(); box.leave.assert_not_called()
        classifier.assert_called_once()

    def test_diagnose_rejects_uidvalidity_mismatch_before_read(self):
        box=Mock(); box.validity='77'; classifier=Mock()
        with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
            self.assertEqual(diagnose(box,make_config(),'9',classifier,expected_validity='76'),1)
        self.assertIn('mail_diagnosis_identity_failed uid=9 error=uidvalidity_mismatch','\n'.join(logs.output))
        box.raw.assert_not_called(); box.internal_date.assert_not_called(); classifier.assert_not_called()

    def test_diagnose_internal_date_success_has_no_mutations(self):
        box=Mock(); box.validity='77'; box.raw.return_value=b'From: synthetic@example.invalid\n\nbody'; box.internal_date.return_value='2026-10-31T01:27:00+01:00'
        classifier=Mock(return_value=self.disposition(destination='leave',summary=None,extracted_events=[]))
        with self.assertLogs('local_agentic_triage',level='INFO') as logs:
            self.assertEqual(diagnose(box,make_config(),'9',classifier,expected_validity='77'),0)
        self.assertIn('mail_diagnosis_completed uid=9 uidvalidity=77 stage=classify','\n'.join(logs.output))
        box.internal_date.assert_called_once_with('9'); box.archive.assert_not_called(); box.leave.assert_not_called()
    def test_archive_without_valid_header_date_uses_imap_internal_date(self):
        for raw in (b'From: sender\n\nbody',b'Date: not-a-date\nFrom: sender\n\nbody',b'Date: Fri, 31 Oct 2026 01:27:00\nFrom: sender\n\nbody'):
            with tempfile.TemporaryDirectory() as d:
                store=Store(Path(d)/'db'); box=Mock()
                box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=raw; box.internal_date.return_value='2026-10-31T01:27:00+01:00'
                classifier=Mock(return_value=self.disposition(destination='archive',summary='summary',extracted_events=[{'title':'X','start':None,'end':None,'date_note':None,'location':None}])); events=Mock(return_value=self.event_result({'index':0,'outcome':'skipped','reason':'missing_date'}))
                self.assertEqual(poll(box,store,make_config(),classifier,events),0)
                events.assert_called_once(); box.archive.assert_called_once_with('9'); box.leave.assert_not_called()
                self.assertEqual(store.prepared('key')['sent_at'],'2026-10-31T01:27:00+01:00')
                store.close()

    def test_event_command_failure_is_absorbed_and_logs_only_safe_failure(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(); box.validity='77'
            box.candidates.return_value=['9']; box.source_key.return_value='key'
            box.raw.return_value=b'\nbody'
            box.internal_date.return_value='2026-10-31T01:27:00+01:00'
            classifier=Mock(return_value=self.disposition(destination='leave',summary=None,extracted_events=[]))
            events=Mock(side_effect=CommandError('event_command_failed',17))
            with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),0)
            output='\n'.join(logs.output)
            self.assertEqual(output.count('mail_event_failed'),1)
            self.assertIn('mail_event_failed uid=9 error=event_command_failed returncode=17',output)
            self.assertNotIn('mail_item_failed',output)
            box.leave.assert_called_once_with('9'); box.archive.assert_not_called()
            store.close()

    def test_archive_event_command_failure_logs_bounded_returncode_and_archives(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(validity='77')
            box.candidates.return_value=['9']; box.source_key.return_value='key'
            box.raw.return_value=b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: synthetic\n\nbody'
            classifier=Mock(return_value=self.disposition(destination='archive',summary='summary',extracted_events=[]))
            events=Mock(side_effect=CommandError('event_command_failed',17))
            with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),0)
            output='\n'.join(logs.output)
            self.assertEqual(output.count('mail_event_failed'),1)
            self.assertIn('mail_event_failed uid=9 error=event_command_failed returncode=17',output)
            self.assertNotIn('mail_item_failed',output)
            box.archive.assert_called_once_with('9'); box.leave.assert_not_called()
            self.assertIsNotNone(store.prepared('key')['ready_at'])
            store.close()

    def test_event_model_http_failure_is_sanitized_optional_enrichment(self):
        import urllib.error
        from local_agentic_triage.events.core import process
        event={'title':'PRIVATE EVENT','start':'2099-12-01','end':None,'date_note':None,'location':None}
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(); box.validity='77'
            box.candidates.return_value=['9']; box.source_key.return_value='key'
            box.raw.return_value=b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: secret@example.invalid\n\nprivate body'
            classifier=Mock(return_value=self.disposition(destination='leave',summary=None,extracted_events=[event]))
            with patch('local_agentic_triage.events.calendar.Calendar.nearby',return_value=[]), \
                 patch('local_agentic_triage.events.calendar.Calendar.create') as create, \
                 patch('subprocess.run',side_effect=AssertionError('decision subprocess')), \
                 patch('urllib.request.OpenerDirector.open',side_effect=urllib.error.URLError('PRIVATE_SENTINEL')), \
                 self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,process),0)
            output='\n'.join(logs.output)
            self.assertIn('mail_event_failed uid=9 error=model_request_failed',output)
            self.assertNotIn('returncode=',output)
            self.assertNotIn('mail_item_failed',output)
            for private in ('PRIVATE_SENTINEL','PRIVATE EVENT','secret@example.invalid','private body','2099-12-01'):
                self.assertNotIn(private,output)
            create.assert_not_called(); box.leave.assert_called_once_with('9'); box.archive.assert_not_called()
            store.close()

    def test_archive_disposition_failure_after_event_failure_remains_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(validity='77')
            box.candidates.return_value=['9']; box.source_key.return_value='key'
            box.raw.return_value=b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: synthetic\n\nbody'
            box.archive.side_effect=RuntimeError('PRIVATE disposition output')
            classifier=Mock(return_value=self.disposition(destination='archive',summary='summary',extracted_events=[]))
            events=Mock(side_effect=RuntimeError('PRIVATE event output'))
            with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),1)
            output='\n'.join(logs.output)
            self.assertEqual(output.count('mail_event_failed'),1)
            self.assertEqual(output.count('mail_item_failed'),1)
            self.assertIn('stage=disposition error=runtime_error',output)
            self.assertNotIn('PRIVATE',output)
            box.archive.assert_called_once_with('9'); box.leave.assert_not_called()
            self.assertIsNone(store.prepared('key')['ready_at'])
            store.close()

    def test_event_results_are_logged_without_private_event_data(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(); box.validity='77'
            box.candidates.return_value=['9']; box.source_key.return_value='key'
            box.raw.return_value=b'From: secret@example.invalid\nSubject: private subject\n\nprivate body'
            box.internal_date.return_value='2026-10-31T01:27:00+01:00'
            classifier=Mock(return_value=self.disposition(destination='leave',summary=None,extracted_events=[{'title':'PRIVATE EVENT','start':'2026-11-01','end':None,'date_note':None,'location':None}]))
            events=Mock(return_value={'status':'ok','results':[{'index':0,'outcome':'skipped','reason':'too_soon'}]})
            with self.assertLogs('local_agentic_triage',level='INFO') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),0)
            output='\n'.join(logs.output)
            self.assertIn('mail_event_result uid=9 index=0 outcome=skipped reason=too_soon',output)
            for private in ('PRIVATE EVENT','private subject','private body','secret@example.invalid','2026-11-01'):
                self.assertNotIn(private,output)
            box.leave.assert_called_once_with('9'); store.close()

    def test_completed_missing_end_is_logged_as_created_not_failed(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(validity='77')
            box.candidates.return_value=['9']; box.source_key.return_value='key'
            box.raw.return_value=b'Date: Sat, 31 Oct 2026 01:27:00 +0100\nFrom: synthetic\n\nbody'
            event={'title':'PRIVATE EVENT','start':'2099-12-01T18:00:00+01:00','end':None,'date_note':None,'location':None}
            classifier=Mock(return_value=self.disposition(destination='archive',summary='summary',extracted_events=[event]))
            events=Mock(return_value=self.event_result({'index':0,'outcome':'created','reason':None}))
            with self.assertLogs('local_agentic_triage',level='INFO') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),0)
            output='\n'.join(logs.output)
            self.assertIn('mail_event_result uid=9 index=0 outcome=created reason=None',output)
            self.assertNotIn('mail_event_failed',output)
            self.assertNotIn('PRIVATE EVENT',output)
            box.archive.assert_called_once_with('9'); box.leave.assert_not_called()
            self.assertIsNotNone(store.prepared('key')['ready_at'])
            store.close()

    def test_malformed_event_results_are_absorbed_and_disposition_continues(self):
        event={'title':'PRIVATE EVENT','start':'2099-12-01','end':None,'date_note':None,'location':None}
        malformed=(
            None, Mock(), [],
            {'status':'ok','results':[]},
            {'status':'ok','results':[{'index':0,'outcome':'existing','reason':None},
                                      {'index':0,'outcome':'existing','reason':None}]},
            {'status':'ok','results':[{'index':1,'outcome':'existing','reason':None}]},
            {'status':'ok','results':[{'index':0,'outcome':'skipped','reason':None}]},
            {'status':'error','results':[{'index':0,'outcome':'existing','reason':None}]},
        )
        for destination in ('leave','archive'):
            for result in malformed:
                with self.subTest(destination=destination,result_type=type(result).__name__),tempfile.TemporaryDirectory() as d:
                    store=Store(Path(d)/'db'); box=Mock(); box.validity='77'
                    box.candidates.return_value=['9']; box.source_key.return_value='key'
                    box.raw.return_value=b'From: private@example.invalid\n\nprivate body'
                    box.internal_date.return_value='2026-10-31T01:27:00+01:00'
                    summary='synthetic' if destination=='archive' else None
                    classifier=Mock(return_value=self.disposition(destination=destination,summary=summary,extracted_events=[event]))
                    with self.assertLogs('local_agentic_triage',level='INFO') as logs:
                        self.assertEqual(poll(box,store,make_config(),classifier,Mock(return_value=result)),0)
                    output='\n'.join(logs.output)
                    self.assertNotIn('mail_event_result',output)
                    self.assertEqual(output.count('mail_event_failed'),1)
                    self.assertIn('mail_event_failed uid=9 error=',output)
                    self.assertNotIn('mail_item_failed',output)
                    for private in ('PRIVATE EVENT','private@example.invalid','private body','2099-12-01'):
                        self.assertNotIn(private,output)
                    if destination=='leave':
                        box.leave.assert_called_once_with('9'); box.archive.assert_not_called()
                    else:
                        box.archive.assert_called_once_with('9'); box.leave.assert_not_called()
                        self.assertIsNotNone(store.prepared('key')['ready_at'])
                    store.close()

    def test_archive_retry_does_not_reclassify_or_rerun_events(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(); box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=b'Date: Fri, 31 Oct 2026 01:27:00 +0100\nFrom: original\n\nbody'
            classify=Mock(return_value=self.disposition(destination='archive',summary='Oryginalny zakup.',extracted_events=[{'title':'X','start':None,'end':None,'date_note':None,'location':None}])); events=Mock(return_value=self.event_result({'index':0,'outcome':'skipped','reason':'missing_date'}))
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
    def test_leave_no_row_and_event_failure_does_not_block(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(); box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=b'\nbody'; box.internal_date.return_value='2026-10-31T01:27:00+01:00'
            classifier=Mock(return_value=self.disposition(destination='leave',summary=None,extracted_events=[])); events=Mock(return_value=self.event_result())
            self.assertEqual(poll(box,store,make_config(),classifier,events),0)
            box.leave.assert_called_once_with('9'); self.assertIsNone(store.prepared('key')); box.archive.assert_not_called()
            box.reset_mock(); box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=b'\nbody'; events.side_effect=ValueError('private')
            with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),0)
            output='\n'.join(logs.output)
            self.assertEqual(output.count('mail_event_failed'),1); self.assertNotIn('private',output)
            self.assertNotIn('mail_item_failed',output)
            box.leave.assert_called_once_with('9'); box.archive.assert_not_called(); store.close()
    def test_prepared_failure_then_independent_candidate_and_ready_retry(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); store.prepare('old','original','frozen',sent_at='2026-10-31T01:27:00+01:00')
            box=Mock(); box.candidates.return_value=['9','12']; box.source_key.side_effect=lambda uid: 'old' if uid=='9' else 'new'
            box.archive.side_effect=lambda uid: (_ for _ in ()).throw(RuntimeError('private')) if uid=='9' else None
            box.raw.return_value=b'Date: Fri, 31 Oct 2026 01:27:00 +0100\nFrom: synthetic\n\nbody'
            classifier=Mock(return_value=self.disposition(destination='archive',summary='new summary',extracted_events=[])); events=Mock(return_value=self.event_result())
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
            store=Store(Path(d)/'db'); box=Mock(); box.validity='77'
            box.candidates.return_value=['9']; box.source_key.return_value='key'; box.raw.return_value=b'From: sender\n\nbody'
            box.internal_date.side_effect=ValueError('PRIVATE INTERNAL DATE')
            classifier=Mock(); events=Mock()
            with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),1)
            output='\n'.join(logs.output)
            self.assertIn('uid=9 uidvalidity=77 stage=read error=value_error',output); self.assertNotIn('PRIVATE',output)
            classifier.assert_not_called(); events.assert_not_called(); box.archive.assert_not_called(); box.leave.assert_not_called()
            self.assertIsNone(store.prepared('key')); store.close()

    def test_oversized_archive_row_stops_before_events_store_and_imap(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock()
            box.candidates.return_value=['9']; box.source_key.return_value='key'
            box.raw.return_value=b'Date: Fri, 31 Oct 2026 01:27:00 +0100\nFrom: sender@example.invalid\n\nbody'
            classifier=Mock(return_value=self.disposition(destination='archive',summary='x'*200,extracted_events=[{'title':'X','start':'2099-01-01','end':None,'date_note':None,'location':None}]))
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
            box.validity='77'; box.candidates.return_value=['9']; box.source_key.return_value='private-key'
            box.raw.return_value=b'From: secret@example.invalid\nSubject: private subject\n\nprivate body'
            box.internal_date.return_value='2026-10-31T01:27:00+01:00'
            classifier=Mock(side_effect=ValueError('archive summary')); events=Mock()
            with self.assertLogs('local_agentic_triage',level='INFO') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,events),1)
            output='\n'.join(logs.output)
            for expected in ('mail_poll_started','mail_candidates_discovered count=1','mail_item_started item=1 total=1 uid=9 uidvalidity=77','mail_item_action item=1 uid=9 stage=classify','mail_item_failed item=1 uid=9 uidvalidity=77 stage=classify error=value_error','mail_poll_completed candidates=1 completed=0 failures=1'):
                self.assertIn(expected,output)
            for private in ('private-key','secret@example.invalid','private subject','private body'):
                self.assertNotIn(private,output)
            store.close()
