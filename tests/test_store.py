import unittest
import tempfile
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import os
from pathlib import Path
from local_agentic_triage.reporting.store import Store

class StoreTests(unittest.TestCase):
    def test_prepared_preserves_and_ready(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'db')
            s.prepare('key','original',"summary ' quote",sent_at='2026-10-31T00:27:00+00:00')
            s.prepare('key','changed','changed',sent_at='2099-01-01T00:00:00+00:00')
            self.assertEqual(s.prepared('key')['sender'],'original')
            self.assertEqual(s.prepared('key')['sent_at'],'2026-10-31T00:27:00+00:00')
            self.assertEqual(s.pending(),[])
            s.ready('key'); rows=s.pending()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['summary'],"summary ' quote")
            s.reported(['key'],'2026-11-10T17:00:00+00:00'); self.assertEqual(s.pending(),[])
            self.assertEqual(s.last_reported_at(),'2026-11-10T17:00:00+00:00')
            self.assertEqual(s.report_window('2026-11-11T17:00:00+00:00'),('2026-11-10T17:00:00+00:00','2026-11-11T17:00:00+00:00'))
            self.assertEqual(s.report_window('2026-11-12T17:00:00+00:00'),('2026-11-10T17:00:00+00:00','2026-11-11T17:00:00+00:00'))
            s.reported([], '2026-11-11T17:00:00+00:00')
            self.assertEqual(s.report_window('2026-11-12T17:00:00+00:00'),('2026-11-10T17:00:00+00:00','2026-11-11T17:00:00+00:00'))
            s.complete_report_window('2026-11-11T17:00:00+00:00')
            self.assertEqual(s.last_reported_at(),'2026-11-11T17:00:00+00:00')
            self.assertIsNone(s.current_report_window())
            s.clear_report_window()
            self.assertEqual(s.db.execute('pragma journal_mode').fetchone()[0],'wal')
            self.assertIn('sent_at',[r[1] for r in s.db.execute('pragma table_info(reports)')])
            s.close()

    def test_legacy_window_migration_is_atomic_and_infers_acknowledged_rows(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'; end='2026-11-10T17:00:00+00:00'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE reports (source_key TEXT PRIMARY KEY, sender TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL, ready_at TEXT, reported_at TEXT)')
            db.execute('CREATE TABLE report_state (id INTEGER PRIMARY KEY CHECK(id=1), start_at TEXT NOT NULL, end_at TEXT NOT NULL)')
            db.execute('INSERT INTO reports VALUES (?,?,?,?,?,?)',('key','sender','summary',end,end,end))
            db.execute('INSERT INTO report_state VALUES (1,?,?)',('2026-11-09T17:00:00+00:00',end))
            db.commit(); db.close()
            def open_store(_):
                store=Store(path); value=store.report_window_has_rows(); store.close(); return value
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(list(pool.map(open_store,range(24))),[True]*24)

    def test_concurrent_new_database_initialization(self):
        with tempfile.TemporaryDirectory() as d:
            for iteration in range(10):
                path=Path(d)/f'db-{iteration}'
                def open_store(_):
                    store=Store(path); store.close(); return True
                with ThreadPoolExecutor(max_workers=16) as pool:
                    self.assertEqual(list(pool.map(open_store,range(32))),[True]*32)

    def test_unlock_failure_still_closes_lock_descriptor(self):
        with tempfile.TemporaryDirectory() as d:
            real_close=os.close
            with patch('local_agentic_triage.reporting.store.fcntl.flock',side_effect=[None,OSError('synthetic')]), patch('local_agentic_triage.reporting.store.os.close',wraps=real_close) as close:
                with self.assertRaises(OSError): Store(Path(d)/'db')
                close.assert_called_once()

    def test_lock_cleanup_preserves_primary_failure(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'
            class FalseyError(Exception):
                def __bool__(self): return False
            with patch('local_agentic_triage.reporting.store.fcntl.flock',side_effect=FalseyError('falsey-primary')):
                with self.assertRaisesRegex(FalseyError,'^falsey-primary$'): Store(path)
            with patch('local_agentic_triage.reporting.store.fcntl.flock',side_effect=[OSError('acquire'),OSError('unlock')]) as flock:
                with self.assertRaisesRegex(OSError,'^acquire$'): Store(path)
                self.assertEqual(flock.call_count,1)
            with patch('local_agentic_triage.reporting.store.fcntl.flock',side_effect=[None,OSError('unlock')]), patch('local_agentic_triage.reporting.store.sqlite3.connect',side_effect=ValueError('primary')):
                with self.assertRaisesRegex(ValueError,'^primary$'): Store(path)
