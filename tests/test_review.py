import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import subprocess
from datetime import datetime
from helpers import make_config
from local_agentic_triage.events.core import process
from local_agentic_triage.mail.imap import Mailbox
from local_agentic_triage.mail.pipeline import poll
from local_agentic_triage.reporting.store import Store
from test_imap import SELECT, SEARCH, done
from helpers import config_text
from test_events import decision_response, prompt as event_prompt

EVENT={'title':'\"; doShellScript("evil"); //','start':'2099-12-01','end':'2099-12-02','date_note':None,'location':None}
class ReviewTests(unittest.TestCase):
    def setUp(self):
        patcher=patch('local_agentic_triage.mail.pipeline.extract',return_value=[])
        self.extractor=patcher.start()
        self.addCleanup(patcher.stop)

    def disposition(self, *, destination,summary,extracted_events):
        self.extractor.return_value=extracted_events
        return {'destination':destination,'summary':summary}

    def test_toolless_decision_and_adapter_data(self):
        from local_agentic_triage.events.calendar import Calendar, SCRIPT
        def run(args,**kw):
            self.assertEqual(args,['/usr/bin/osascript','-l','JavaScript','-e',SCRIPT])
            self.assertNotIn(EVENT['title'],SCRIPT)
            self.assertNotIn('hermes',args)
            for forbidden in ('chat','--toolsets','--skills','--model','--provider','stream-json'):
                self.assertNotIn(forbidden,args)
            data=json.loads(kw['input']); self.assertEqual(data['calendar'],'Test Calendar')
            out=[] if data['operation']=='search' else {'title':EVENT['title'],'start':'2099-12-01','end':'2099-12-02','all_day':True,'location':None}
            return subprocess.CompletedProcess(args,0,json.dumps(out),'')
        with patch('subprocess.run',side_effect=run) as runner, \
             patch('urllib.request.OpenerDirector.open',side_effect=[decision_response({'outcome':'create'}),decision_response(EVENT)]) as opened:
            result=process([EVENT],make_config())
        self.assertEqual(result['results'][0]['outcome'],'created')
        self.assertEqual(runner.call_count,2)
        for call in runner.call_args_list:
            self.assertFalse(call.kwargs['shell']); self.assertGreater(call.kwargs['timeout'],0)
        bodies=[json.loads(call.args[0].data) for call in opened.call_args_list]
        self.assertEqual(bodies[0]['messages'][0]['content'],event_prompt())
        self.assertEqual(json.loads(bodies[0]['messages'][1]['content']),{'event':EVENT,'nearby':[]})
        self.assertEqual(json.loads(bodies[1]['messages'][1]['content']),{'event':EVENT})
        self.assertEqual(opened.call_args.kwargs['timeout'],300)

    def test_after_seen_guard_blocks_move(self):
        for bad in (SEARCH.replace(b'77',b'78'),SEARCH.replace(b'9 12',b'12')):
            with patch('subprocess.run',side_effect=[done(SELECT),done(SEARCH),done(b'{"message":"Flag(s) successfully added"}'),done(bad)]) as run:
                box=Mailbox(make_config())
                with self.assertRaises(ValueError): box.archive('9')
                self.assertEqual(run.call_count,4)
                self.assertEqual(run.call_args.kwargs["input"],b'a1 SELECT "Test Inbox"\r\na2 UID SEARCH UID 9\r\n')

    def test_reconcile_crash_after_move(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(validity='77')
            box.source_key.side_effect=lambda uid: 'imap:test-account:Test%20Inbox:77:'+uid
            box.candidates.return_value=['9']; box.raw.return_value=b'Date: Fri, 31 Oct 2026 01:27:00 +0100\nFrom: fake\n\nbody'
            present={'9'}
            box.all_uids.side_effect=lambda:set(present)
            box.archive.side_effect=lambda uid: present.remove(uid)
            classifier=Mock(return_value=self.disposition(destination='archive',summary='synthetic',extracted_events=[])); events=Mock(return_value={'status':'ok','results':[]})
            with patch.object(store,'ready',side_effect=RuntimeError('private')):
                with self.assertLogs('local_agentic_triage',level='ERROR'):
                    self.assertEqual(poll(box,store,make_config(),classifier,events),1)
            box.candidates.return_value=[]
            self.assertEqual(poll(box,store,make_config(),classifier,events),0)
            self.assertEqual(len(store.pending()),1)
            classifier.assert_called_once(); events.assert_called_once(); box.archive.assert_called_once()
            store.close()

    def test_all_day_window_includes_entire_seventh_day(self):
        from local_agentic_triage.events.calendar import Calendar
        with patch.object(Calendar,'call',return_value=[]) as call:
            Calendar(make_config()).nearby(EVENT)
        self.assertEqual(call.call_args.args[0]['upper'],'2099-12-08T23:59:59.999999')

    def test_nearby_normalizes_equal_single_day_adapter_rows(self):
        from local_agentic_triage.events.calendar import Calendar
        cases=(('2099-12-31','2100-01-01'),('2028-02-29','2028-03-01'))
        for start,end in cases:
            row={'title':'Synthetic','start':start,'end':start,'all_day':True,'location':None}
            with self.subTest(start=start),patch.object(Calendar,'call',return_value=[row]):
                self.assertEqual(Calendar(make_config()).nearby(EVENT),[row|{'end':end}])
                self.assertEqual(row['end'],start)

    def test_nearby_preserves_canonical_all_day_rows(self):
        from local_agentic_triage.events.calendar import Calendar
        for start,end in (('2099-12-01','2099-12-02'),('2099-12-01','2099-12-05')):
            row={'title':'Synthetic','start':start,'end':end,'all_day':True,'location':None}
            with self.subTest(end=end),patch.object(Calendar,'call',return_value=[row]):
                self.assertEqual(Calendar(make_config()).nearby(EVENT),[row])

    def test_nearby_rejects_invalid_equal_or_negative_rows(self):
        from local_agentic_triage.events.calendar import Calendar
        rows=(
            {'title':'Synthetic','start':'2099-99-01','end':'2099-99-01','all_day':True,'location':None},
            {'title':'Synthetic','start':'2099-12-02','end':'2099-12-01','all_day':True,'location':None},
            {'title':'Synthetic','start':'2099-12-01T10:00:00Z','end':'2099-12-01T10:00:00Z','all_day':False,'location':None},
            {'title':'Synthetic','start':'2099-12-01','end':'2099-12-01','all_day':False,'location':None},
        )
        for row in rows:
            with self.subTest(row=row),patch.object(Calendar,'call',return_value=[row]),self.assertRaisesRegex(ValueError,'calendar_search_invalid'):
                Calendar(make_config()).nearby(EVENT)

    def test_equal_all_day_create_readback_is_not_normalized(self):
        from local_agentic_triage.events.calendar import Calendar
        row={'title':EVENT['title'],'start':'2099-12-01','end':'2099-12-01','all_day':True,'location':None}
        with patch.object(Calendar,'call',return_value=row),self.assertRaisesRegex(ValueError,'calendar_verification_failed'):
            Calendar(make_config()).create(EVENT)

    def test_event_adapter_failure_is_absorbed_and_archive_completes(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(validity='77'); box.candidates.return_value=['9']
            key='imap:test-account:Test%20Inbox:77:9'
            box.source_key.return_value=key
            box.raw.return_value=(b'Date: Sat, 31 Oct 2026 01:27:00 +0100\n'
                                  b'From: Synthetic Sender <sender@example.invalid>\n\nSynthetic body')
            classifier=Mock(return_value=self.disposition(destination='archive',summary='Synthetic summary',extracted_events=[EVENT]))
            event_processor=Mock(side_effect=RuntimeError('PRIVATE adapter output'))
            transitions=[]; prepare=store.prepare; ready=store.ready
            store.prepare=Mock(side_effect=lambda *args,**kwargs:(transitions.append('prepared'),prepare(*args,**kwargs))[1])
            store.ready=Mock(side_effect=lambda *args,**kwargs:(transitions.append('ready'),ready(*args,**kwargs))[1])
            with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                self.assertEqual(poll(box,store,make_config(),classifier,event_processor),0)
            output='\n'.join(logs.output)
            self.assertEqual(output.count('mail_event_failed'),1)
            self.assertIn('mail_event_failed uid=9 error=runtime_error',output)
            self.assertNotIn('PRIVATE adapter output',output)
            self.assertNotIn('Synthetic body',output)
            self.assertNotIn('mail_item_failed',output)
            box.archive.assert_called_once_with('9'); box.leave.assert_not_called()
            row=store.prepared(key)
            self.assertEqual(row['sender'],'Synthetic Sender <sender@example.invalid>')
            self.assertEqual(row['summary'],'Synthetic summary')
            self.assertEqual(row['sent_at'],'2026-10-31T01:27:00+01:00')
            self.assertIsNotNone(row['ready_at'])
            self.assertEqual(transitions,['prepared','ready'])
            store.close()

    def test_readback_mismatch_and_unsafe_create_rejected(self):
        from local_agentic_triage.events.calendar import Calendar, canonical
        expected=canonical(EVENT)
        for wrong in (expected|{'title':'other'},expected|{'start':'2099-12-02'},expected|{'end':'2099-12-03'},expected|{'calendar':'Other'},{}):
            with patch.object(Calendar,'call',return_value=wrong),self.assertRaisesRegex(ValueError,'calendar_verification_failed'):
                Calendar(make_config()).create(EVENT)
        noted=EVENT|{'date_note':'Synthetic retained note'}
        with patch.object(Calendar,'call',return_value=expected) as call:
            self.assertEqual(Calendar(make_config()).create(noted),expected)
        call.assert_called_once_with({'operation':'create','event':expected})
        for event in (EVENT|{'start':None},EVENT|{'start':'2099-12-01T10:00:00Z'},EVENT|{'start':'2000-01-01','end':'2000-01-02'}):
            with patch.object(Calendar,'call') as call,self.assertRaises(ValueError):
                Calendar(make_config()).create(event)
            call.assert_not_called()

    def test_create_boundary_requires_strictly_more_than_three_days(self):
        from local_agentic_triage.events.calendar import Calendar, canonical
        clock=lambda tz=None: datetime(2030,1,1,12,0,tzinfo=tz)
        timed=EVENT|{'start':'2030-01-02T12:00:00Z','end':'2030-01-04T12:00:00Z'}
        all_day=EVENT|{'start':'2030-01-02','end':'2030-01-04'}
        for event in (timed,all_day):
            with self.subTest(event=event),patch.object(Calendar,'call') as call,self.assertRaisesRegex(ValueError,'calendar_too_soon'):
                Calendar(make_config(),clock=clock).create(event)
            call.assert_not_called()
        for event in (timed|{'end':'2030-01-04T12:00:01Z'},all_day|{'end':'2030-01-05'}):
            with self.subTest(event=event),patch.object(Calendar,'call',return_value=canonical(event)) as call:
                Calendar(make_config(),clock=clock).create(event)
            call.assert_called_once()

    def test_every_uid_guard_rejects_changed_validity_and_missing(self):
        for operation in ('raw','leave','archive'):
            for bad in (SEARCH.replace(b'77',b'78'),SEARCH.replace(b'9 12',b'12')):
                with patch('subprocess.run',side_effect=[done(SELECT),done(bad)]) as run:
                    box=Mailbox(make_config())
                    with self.assertRaises(ValueError): getattr(box,operation)('9')
                    self.assertEqual(run.call_count,2)
                    self.assertEqual(run.call_args.kwargs['input'],b'a1 SELECT "Test Inbox"\r\na2 UID SEARCH UID 9\r\n')

    def test_exists_ignores_leave_and_reconciliation_scopes_identity(self):
        with patch('subprocess.run',side_effect=[done(SELECT),done(SEARCH)]) as run:
            self.assertTrue(Mailbox(make_config()).exists('9'))
            self.assertNotIn(b'HermesLeave',run.call_args.kwargs['input'])
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(); box.candidates.return_value=[]
            box.source_key.side_effect=lambda uid:'imap:test-account:Test%20Inbox:77:'+uid; box.all_uids.return_value=set()
            keys=['imap:test-account:Test%20Inbox:77:9','imap:test-account:Test%20Inbox:78:9','imap:other:Test%20Inbox:77:9','imap:test-account:Test%20Archive:77:9']
            for key in keys: store.prepare(key,'synthetic','synthetic',sent_at='2026-10-31T01:27:00+01:00')
            self.assertEqual(poll(box,store,make_config()),0)
            self.assertEqual([r['source_key'] for r in store.pending()],keys[:1])
            self.assertEqual(len(store.stranded()),3); self.assertEqual(box.all_uids.call_count,2); box.exists.assert_not_called(); store.close()

    def test_reconciliation_lists_inbox_once(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); box=Mock(validity='77')
            box.candidates.return_value=[]
            box.source_key.side_effect=lambda uid:'imap:test-account:Test%20Inbox:77:'+uid
            box.all_uids.return_value={'9','10'}
            for uid in ('9','10','11'):
                store.prepare(f'imap:test-account:Test%20Inbox:77:{uid}','synthetic','synthetic',sent_at='2026-10-31T01:27:00+01:00')
            self.assertEqual(poll(box,store,make_config()),0)
            self.assertEqual([r['source_key'] for r in store.pending()],['imap:test-account:Test%20Inbox:77:11'])
            self.assertEqual(box.all_uids.call_count,2)
            box.exists.assert_not_called()
            store.close()

    def test_report_and_poll_locks_are_independent(self):
        from local_agentic_triage.cli import main
        from local_agentic_triage.common.lock import Lock
        with tempfile.TemporaryDirectory() as d:
            config=Path(d)/'config.toml'
            config.write_text(config_text(database=f'{d}/db',poll_lock=f'{d}/poll',report_lock=f'{d}/report'))
            with Lock(Path(d)/'poll'),patch('local_agentic_triage.cli.send') as send:
                self.assertEqual(main(['--config',str(config),'report','send','--deliver']),0)
                send.assert_called_once()
            with Lock(Path(d)/'report'),patch('local_agentic_triage.cli.Mailbox.connect'),patch('local_agentic_triage.cli.poll',return_value=0) as polling:
                self.assertEqual(main(['--config',str(config),'mail','poll']),0)
                polling.assert_called_once()

    def test_semantic_outcomes_preserved_and_created_cannot_be_claimed(self):
        from local_agentic_triage.events.calendar import Calendar
        from local_agentic_triage.events.core import duplicate_results
        candidates=[EVENT,EVENT|{'title':'Different'},EVENT|{'title':'Ambiguous'}]
        answers=[{'outcome':'create'},EVENT,{'outcome':'existing'},{'outcome':'possible_duplicate'}]
        with patch.object(Calendar,'nearby',return_value=[]),patch.object(Calendar,'create') as create, \
             patch('urllib.request.OpenerDirector.open',side_effect=[decision_response(a) for a in answers]):
            final=process(candidates,make_config())
        create.assert_called_once_with(EVENT)
        self.assertEqual([r['outcome'] for r in final['results']],['created','existing','skipped'])
        with self.assertRaises(ValueError): duplicate_results({'outcome':'created'})
