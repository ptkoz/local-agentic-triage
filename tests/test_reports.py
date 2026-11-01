import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from helpers import make_config
from local_agentic_triage.reporting.report import sanitize, header, record_message, send
from local_agentic_triage.reporting.store import Store

UTC=ZoneInfo('UTC')


class ReportTests(unittest.TestCase):
    def test_sanitize(self):
        text=sanitize('a|b\n`x`\\_*~#[]()+-\x00 @everyone foo@everyone foo@here @here <@123> <@&4> <#5> https://evil.invalid x://one.invalid mailto:bad@example.invalid tel:+48123 www.evil.invalid example.com/path discord.gg/code пример.рф/path 例子。中国 例子．中国 例子｡中国 127.0.0.1/path')
        for bad in ('|','\n','`','\\','_','*','~','#','[',']','(',')','+','-','\x00','@everyone','@here','<@','<#','://','mailto:','tel:','www.','example.com','one.invalid','discord.gg','пример.рф','例子。中国','例子．中国','例子｡中国','127.0.0.1'):
            self.assertNotIn(bad,text)
        for address in ('person@example.com','person@here.example.com','person@everyone.example.com','first-last+tag@example.com','a+b@example.com'):
            self.assertIn(address,sanitize(address))
        for address in ('foo*bar*baz@example.com','*foo*@example.com','foo_bar_baz@example.com','_foo_@example.com','foo~bar~baz@example.com','~foo~@example.com','foo`bar`baz@example.com','`foo`@example.com','foo|bar@example.com',r'foo\bar@example.com'):
            cleaned=sanitize(address)
            self.assertIn('@example.com',cleaned)
            self.assertFalse(any(char in cleaned for char in ('*','_','~','`','|','\\')))

    def test_summary_and_one_record_format(self):
        start=datetime(2026,11,8,16,44,tzinfo=UTC); end=datetime(2026,11,9,17,0,tzinfo=UTC)
        self.assertEqual(header('2',start,end,9),'<@2>, powyżej znajduje się informacja o **9 wiadomościach** przeniesionych do Archiwum między 8 lis 17:44 a 9 lis 18:00:')
        row={'source_key':'key','created_at':'2099-01-01T00:00:00+00:00','sent_at':'2026-10-31T00:27:00+00:00','sender':'Example Sender <sender@example.invalid>','summary':'Nadawca przesłał syntetyczne powiadomienie.'}
        self.assertEqual(record_message(row,1900),'**Archiwum**: Od **Example Sender ‹sender@example.invalid›** z dnia 31 paź, 01:27\n> Nadawca przesłał syntetyczne powiadomienie.')
        with self.assertRaises(ValueError): record_message(row|{'summary':'x'*2000},1900)

    def test_delivery_requires_explicit_runner_before_state_access(self):
        store=Mock()
        with self.assertRaisesRegex(RuntimeError,'report_delivery_disabled'):
            send(store,make_config(),datetime(2026,11,10,17,0,tzinfo=UTC))
        store.report_window.assert_not_called()

    def test_legacy_missing_date_is_reported_without_fabricated_timestamp(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db')
            with store.db:
                store.db.execute('INSERT INTO reports (source_key,sender,sent_at,summary,created_at,ready_at) VALUES (?,?,?,?,?,?)',('legacy','a@example.com',None,'Summary','2026-11-09T18:13:00+00:00','2026-11-09T18:13:00+00:00'))
            runner=Mock(return_value=Mock(returncode=0))
            send(store,make_config(),datetime(2026,11,10,17,0,tzinfo=UTC),runner=runner)
            self.assertEqual(runner.call_args_list[0].kwargs['input'],'**Archiwum**: Od **a@example.com** — data wiadomości niedostępna\n> Summary')
            self.assertIsNotNone(store.db.execute('SELECT reported_at FROM reports WHERE source_key=?',('legacy',)).fetchone()[0]); store.close()

    def test_rows_then_summary_and_per_row_ack(self):
        store=Mock(); at=datetime(2026,11,10,17,0,tzinfo=UTC)
        store.current_report_window.return_value=None
        store.report_window.return_value=('2026-11-09T17:00:00+00:00','2026-11-10T17:00:00+00:00')
        store.moved_count.return_value=9
        store.pending.return_value=[
            {'source_key':'a','created_at':'2099-01-01T00:00:00+00:00','sent_at':'2026-11-09T18:13:00+00:00','sender':'a@example.com','summary':'Pierwsze.'},
            {'source_key':'b','created_at':'2099-01-01T00:00:00+00:00','sent_at':'2026-11-09T19:14:00+00:00','sender':'b@example.com','summary':'Drugie.'},
        ]
        runner=Mock(side_effect=[subprocess.CompletedProcess([],0,'','') for _ in range(3)])
        send(store,make_config(),at,runner=runner)
        self.assertEqual(runner.call_count,3)
        self.assertIn('**9 wiadomościach**',runner.call_args_list[2].kwargs['input'])
        self.assertEqual(runner.call_args_list[0].kwargs['input'],'**Archiwum**: Od **a@example.com** z dnia 9 lis, 19:13\n> Pierwsze.')
        self.assertEqual(runner.call_args_list[1].kwargs['input'],'**Archiwum**: Od **b@example.com** z dnia 9 lis, 20:14\n> Drugie.')
        self.assertEqual([call.args for call in store.reported.call_args_list],[(['a'],),(['b'],)])
        store.complete_report_window.assert_called_once_with('2026-11-10T17:00:00+00:00')

    def test_partial_failure_acks_only_sent_record_and_retry_uses_remaining(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db')
            for key in ('a','b'):
                store.prepare(key,f'{key}@example.com',f'{key} summary',sent_at='2026-11-09T18:13:00+00:00'); store.ready(key)
                store.db.execute('UPDATE reports SET created_at=?,ready_at=? WHERE source_key=?',('2026-11-09T18:13:00+00:00','2026-11-09T18:13:00+00:00',key))
            store.db.commit(); at=datetime(2026,11,10,17,0,tzinfo=UTC)
            first=Mock(side_effect=[subprocess.CompletedProcess([],0,'',''),subprocess.CompletedProcess([],1,'','PRIVATE')])
            with self.assertRaises(RuntimeError): send(store,make_config(),at,runner=first)
            self.assertEqual([row['source_key'] for row in store.pending()],['b'])
            retry=Mock(return_value=subprocess.CompletedProcess([],0,'',''))
            send(store,make_config(),datetime(2026,11,11,17,0,tzinfo=UTC),runner=retry)
            self.assertEqual(retry.call_count,2)
            self.assertIn('b summary',retry.call_args_list[0].kwargs['input'])
            self.assertNotIn('a summary',retry.call_args_list[0].kwargs['input'])
            self.assertEqual(store.pending(),[])
            store.close()

    def test_summary_failure_retries_only_summary_and_defers_later_rows(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db')
            start='2026-11-01T07:00:00+00:00'; end='2026-11-01T17:00:00+00:00'
            store.complete_report_window(start)
            for key in ('immediate','pending','later'):
                store.prepare(key,'sender',key,sent_at=start)
                ready=end if key!='later' else '2026-11-01T18:00:00+00:00'
                store.db.execute('UPDATE reports SET ready_at=? WHERE source_key=?',(ready,key))
            store.db.commit(); store.reported(['immediate'])
            first=Mock(side_effect=[Mock(returncode=0),Mock(returncode=1)])
            with patch('local_agentic_triage.reporting.store.now',return_value='2026-11-01T17:01:00+00:00'):
                with self.assertRaises(RuntimeError): send(store,make_config(),datetime.fromisoformat(end),runner=first)
            self.assertEqual(store.prepared('pending')['reported_at'],'2026-11-01T17:01:00+00:00')
            retry=Mock(return_value=Mock(returncode=0))
            send(store,make_config(),datetime(2026,11,2,17,tzinfo=UTC),runner=retry)
            retry.assert_called_once()
            self.assertIn('**2 wiadomościach**',retry.call_args.kwargs['input'])
            self.assertIn('1 lis 08:00 a 1 lis 18:00',retry.call_args.kwargs['input'])
            self.assertEqual([r['source_key'] for r in store.pending()],['later'])
            self.assertIsNone(store.current_report_window()); store.close()

    def test_empty_report_summary_with_mention_advances_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db'); runner=Mock(return_value=subprocess.CompletedProcess([],0,'',''))
            send(store,make_config(),datetime(2026,11,10,17,0,tzinfo=UTC),runner=runner)
            self.assertEqual(runner.call_args.kwargs['input'],'<@2>, powyżej znajduje się informacja o **0 wiadomościach** przeniesionych do Archiwum między 9 lis 18:00 a 10 lis 18:00:')
            self.assertIn('<@2>',runner.call_args.kwargs['input'])
            store.close()

    def test_subprocess_errors_are_safe_and_returncodes_bounded(self):
        store=Mock(); store.current_report_window.return_value=None
        store.report_window.return_value=('2026-11-09T17:00:00+00:00','2026-11-10T17:00:00+00:00')
        store.moved_count.return_value=1
        store.pending.return_value=[{'source_key':'private-key','created_at':'2099-01-01T00:00:00+00:00','sent_at':'2026-11-09T18:13:00+00:00','sender':'secret@example.invalid','summary':'private summary'}]
        at=datetime(2026,11,10,17,0,tzinfo=UTC)
        runner=Mock(return_value=subprocess.CompletedProcess([],1,'PRIVATE','PRIVATE'))
        with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
            with self.assertRaises(RuntimeError): send(store,make_config(),at,runner=runner)
        output='\n'.join(logs.output)
        self.assertIn('error=report_command_failed returncode=1',output)
        for private in ('private-key','secret@example.invalid','private summary','PRIVATE'):
            self.assertNotIn(private,output)
        huge=2**80; runner=Mock(return_value=subprocess.CompletedProcess([],huge,'PRIVATE','PRIVATE'))
        with self.assertLogs('local_agentic_triage',level='ERROR') as logs:
            with self.assertRaises(RuntimeError): send(store,make_config(),at,runner=runner)
        self.assertNotIn(str(huge),'\n'.join(logs.output))
