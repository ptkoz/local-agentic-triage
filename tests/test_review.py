import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import subprocess
from helpers import make_config
from local_agentic_triage.events.core import process
from local_agentic_triage.mail.imap import Mailbox
from local_agentic_triage.mail.pipeline import poll
from local_agentic_triage.reporting.store import Store
from test_imap import SELECT, SEARCH, done
from helpers import config_text
from test_events import stream

EVENT={'title':'\"; doShellScript("evil"); //','start':'2099-12-01','end':None,'date_note':None}
class ReviewTests(unittest.TestCase):
    def test_toolless_decision_and_adapter_data(self):
        from local_agentic_triage.events.calendar import Calendar, SCRIPT
        def run(args,**kw):
            if args[0]=='/usr/bin/osascript':
                self.assertEqual(args,['/usr/bin/osascript','-l','JavaScript','-e',SCRIPT])
                self.assertNotIn(EVENT['title'],SCRIPT)
                data=json.loads(kw['input']); self.assertEqual(data['calendar'],'Test Calendar')
                out=[] if data['operation']=='search' else {'title':EVENT['title'],'start':'2099-12-01','end':'2099-12-02','all_day':True}
                return subprocess.CompletedProcess(args,0,json.dumps(out),'')
            self.assertIn('--ignore-rules',args)
            self.assertEqual(args[args.index('--toolsets')+1],'none')
            for forbidden in ('--skills','apple-account-automation','terminal','browser','files','--model','--provider'):
                self.assertNotIn(forbidden,args)
            data=json.loads(kw['input'].split('\n--- BEGIN UNTRUSTED EVENTS JSON ---\n')[1])
            self.assertEqual(data['calendar'],'Test Calendar'); self.assertEqual(data['events'],[EVENT]); self.assertEqual(data['nearby'],[[]])
            return subprocess.CompletedProcess(args,0,stream({'status':'ok','results':[{'index':0,'outcome':'create','reason':None}]}),'')
        with patch('subprocess.run',side_effect=run) as runner:
            result=process([EVENT],make_config())
        self.assertEqual(result['results'][0]['outcome'],'created')
        self.assertEqual(runner.call_count,3)
        for call in runner.call_args_list:
            self.assertFalse(call.kwargs['shell']); self.assertGreater(call.kwargs['timeout'],0)

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
            classifier=Mock(return_value={'destination':'archive','summary':'synthetic','events':[]}); events=Mock()
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
            row={'title':'Synthetic','start':start,'end':start,'all_day':True}
            with self.subTest(start=start),patch.object(Calendar,'call',return_value=[row]):
                self.assertEqual(Calendar(make_config()).nearby(EVENT),[row|{'end':end}])
                self.assertEqual(row['end'],start)

    def test_nearby_preserves_canonical_all_day_rows(self):
        from local_agentic_triage.events.calendar import Calendar
        for start,end in (('2099-12-01','2099-12-02'),('2099-12-01','2099-12-05')):
            row={'title':'Synthetic','start':start,'end':end,'all_day':True}
            with self.subTest(end=end),patch.object(Calendar,'call',return_value=[row]):
                self.assertEqual(Calendar(make_config()).nearby(EVENT),[row])

    def test_nearby_rejects_invalid_equal_or_negative_rows(self):
        from local_agentic_triage.events.calendar import Calendar
        rows=(
            {'title':'Synthetic','start':'2099-99-01','end':'2099-99-01','all_day':True},
            {'title':'Synthetic','start':'2099-12-02','end':'2099-12-01','all_day':True},
            {'title':'Synthetic','start':'2099-12-01T10:00:00Z','end':'2099-12-01T10:00:00Z','all_day':False},
            {'title':'Synthetic','start':'2099-12-01','end':'2099-12-01','all_day':False},
        )
        for row in rows:
            with self.subTest(row=row),patch.object(Calendar,'call',return_value=[row]),self.assertRaisesRegex(ValueError,'calendar_search_invalid'):
                Calendar(make_config()).nearby(EVENT)

    def test_equal_all_day_create_readback_is_not_normalized(self):
        from local_agentic_triage.events.calendar import Calendar
        row={'title':EVENT['title'],'start':'2099-12-01','end':'2099-12-01','all_day':True}
        with patch.object(Calendar,'call',return_value=row),self.assertRaisesRegex(ValueError,'calendar_verification_failed'):
            Calendar(make_config()).create(EVENT)

    def test_adapter_failures_block_disposition(self):
        for response in (subprocess.CompletedProcess([],1,'private','private'),
                         subprocess.CompletedProcess([],0,'not JSON','private'),
                         subprocess.CompletedProcess([],0,'{}','')):
            with tempfile.TemporaryDirectory() as d:
                store=Store(Path(d)/'db'); box=Mock(); box.candidates.return_value=['9']
                box.source_key.return_value='imap:test-account:Test%20Inbox:77:9'; box.raw.return_value=b'\nsynthetic'
                classifier=Mock(return_value={'destination':'archive','summary':'synthetic','events':[EVENT]})
                with patch('subprocess.run',return_value=response),self.assertLogs('local_agentic_triage',level='ERROR') as logs:
                    self.assertEqual(poll(box,store,make_config(),classifier),1)
                self.assertNotIn('private',str(logs.output)); box.archive.assert_not_called(); box.leave.assert_not_called()
                self.assertEqual(store.stranded(),[]); store.close()

    def test_readback_mismatch_and_unsafe_create_rejected(self):
        from local_agentic_triage.events.calendar import Calendar, canonical
        expected=canonical(EVENT)
        for wrong in (expected|{'title':'other'},expected|{'start':'2099-12-02'},expected|{'end':'2099-12-03'},expected|{'calendar':'Other'},{}):
            with patch.object(Calendar,'call',return_value=wrong),self.assertRaisesRegex(ValueError,'calendar_verification_failed'):
                Calendar(make_config()).create(EVENT)
        for event in (EVENT|{'start':None},EVENT|{'date_note':'uncertain'},EVENT|{'start':'2099-12-01T10:00:00Z'},EVENT|{'start':'2000-01-01'}):
            with patch.object(Calendar,'call') as call,self.assertRaises(ValueError):
                Calendar(make_config()).create(event)
            call.assert_not_called()

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
        from local_agentic_triage.events.core import parse_results
        candidates=[EVENT,EVENT|{'title':'Different title','start':'2099-12-02'},EVENT|{'title':'Ambiguous'}]
        decisions={'status':'ok','results':[{'index':0,'outcome':'create','reason':None},{'index':1,'outcome':'existing','reason':None},{'index':2,'outcome':'skipped','reason':'possible_duplicate'}]}
        nearby={'title':EVENT['title'],'start':'2099-12-01','end':'2099-12-02','all_day':True}
        with patch.object(Calendar,'nearby',return_value=[nearby]),patch.object(Calendar,'create') as create,patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,stream(decisions),'')) as run:
            final=process(candidates,make_config())
            create.assert_called_once_with(EVENT)
            data=json.loads(run.call_args.kwargs['input'].split('\n--- BEGIN UNTRUSTED EVENTS JSON ---\n')[1])
            self.assertEqual(data['nearby'],[[nearby]]*3)
            self.assertEqual([r['outcome'] for r in final['results']],['created','existing','skipped'])
        decisions['results'][0]['outcome']='created'
        with self.assertRaises(ValueError): parse_results(json.dumps(decisions),candidates,decisions=True)
