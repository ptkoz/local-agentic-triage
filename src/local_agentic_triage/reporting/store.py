import sqlite3
import fcntl
import os
from datetime import datetime, timedelta, timezone

def now(): return datetime.now(timezone.utc).isoformat()

class Store:
    def __init__(self,path):
        lock_fd=os.open(str(path)+'.init.lock',os.O_CREAT|os.O_RDWR,0o600)
        locked=False; primary=None
        try:
            fcntl.flock(lock_fd,fcntl.LOCK_EX); locked=True
            self.db=sqlite3.connect(path,timeout=5)
            self.db.row_factory=sqlite3.Row
            self.db.execute('PRAGMA busy_timeout=5000')
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('CREATE TABLE IF NOT EXISTS reports (source_key TEXT PRIMARY KEY, sender TEXT NOT NULL, sent_at TEXT, summary TEXT NOT NULL, created_at TEXT NOT NULL, ready_at TEXT, reported_at TEXT)')
            if 'sent_at' not in [row[1] for row in self.db.execute('PRAGMA table_info(reports)')]:
                self.db.execute('ALTER TABLE reports ADD COLUMN sent_at TEXT')
            self.db.execute('CREATE TABLE IF NOT EXISTS report_state (id INTEGER PRIMARY KEY CHECK(id=1), start_at TEXT NOT NULL, end_at TEXT NOT NULL, has_rows INTEGER)')
            if 'has_rows' not in [row[1] for row in self.db.execute('PRAGMA table_info(report_state)')]:
                self.db.execute('ALTER TABLE report_state ADD COLUMN has_rows INTEGER')
            self.db.execute('CREATE TABLE IF NOT EXISTS report_meta (id INTEGER PRIMARY KEY CHECK(id=1), last_end_at TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS summary_meta (id INTEGER PRIMARY KEY CHECK(id=1), last_cutoff_at TEXT NOT NULL)')
            self.db.execute('INSERT OR IGNORE INTO summary_meta (id,last_cutoff_at) SELECT id,last_end_at FROM report_meta WHERE id=1')
            self.db.execute('''UPDATE report_state SET has_rows=CASE WHEN EXISTS (
                SELECT 1 FROM reports
                WHERE ready_at IS NOT NULL AND ready_at<=report_state.end_at
                  AND (reported_at IS NULL OR reported_at=report_state.end_at)
            ) THEN 1 ELSE 0 END WHERE has_rows IS NULL''')
            self.db.commit()
        except BaseException as exc:
            primary=exc
            if hasattr(self,'db'):
                try: self.db.rollback()
                except BaseException: pass
                try: self.db.close()
                except BaseException: pass
        unlock_error=None
        if locked:
            try: fcntl.flock(lock_fd,fcntl.LOCK_UN)
            except BaseException as exc: unlock_error=exc
        close_error=None
        try: os.close(lock_fd)
        except BaseException as exc: close_error=exc
        if primary is not None: failure=primary
        elif unlock_error is not None: failure=unlock_error
        else: failure=close_error
        if failure is not None:
            if primary is None and hasattr(self,'db'):
                try: self.db.close()
                except BaseException: pass
            raise failure

    def prepare(self,key,sender,summary,*,sent_at):
        if not sent_at: raise ValueError('mail sent date missing')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO reports (source_key,sender,sent_at,summary,created_at) VALUES (?,?,?,?,?)',(key,sender,sent_at,summary,now()))
    def prepared(self,key):
        row=self.db.execute('SELECT * FROM reports WHERE source_key=?',(key,)).fetchone()
        return dict(row) if row else None
    def stranded(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM reports WHERE ready_at IS NULL ORDER BY source_key")]
    def ready(self,key):
        with self.db: self.db.execute('UPDATE reports SET ready_at=COALESCE(ready_at,?) WHERE source_key=?',(now(),key))
    def pending(self,end=None):
        if end is None: query='SELECT * FROM reports WHERE ready_at IS NOT NULL AND reported_at IS NULL ORDER BY created_at, source_key'; args=()
        else: query='SELECT * FROM reports WHERE ready_at IS NOT NULL AND ready_at<=? AND reported_at IS NULL ORDER BY created_at, source_key'; args=(end,)
        return [dict(r) for r in self.db.execute(query,args)]
    def last_reported_at(self):
        meta=self.db.execute('SELECT last_end_at FROM report_meta WHERE id=1').fetchone()
        return meta[0] if meta else None
    def timestamp(self): return now()
    def last_summary_cutoff(self):
        row=self.db.execute('SELECT last_cutoff_at FROM summary_meta WHERE id=1').fetchone()
        return row[0] if row else None
    def reported_since_summary(self,end):
        start=self.last_summary_cutoff()
        if start is None:
            return self.db.execute('SELECT COUNT(*) FROM reports WHERE reported_at IS NOT NULL AND reported_at<=?',(end,)).fetchone()[0]
        return self.db.execute('SELECT COUNT(*) FROM reports WHERE reported_at>? AND reported_at<=?',(start,end)).fetchone()[0]
    def current_report_window(self):
        row=self.db.execute('SELECT start_at,end_at FROM report_state WHERE id=1').fetchone()
        return (row[0],row[1]) if row else None
    def report_window_has_rows(self):
        row=self.db.execute('SELECT has_rows FROM report_state WHERE id=1').fetchone()
        return None if not row or row[0] is None else bool(row[0])
    def set_report_window_has_rows(self,value):
        with self.db: self.db.execute('UPDATE report_state SET has_rows=? WHERE id=1',(int(bool(value)),))
    def report_window(self,end):
        row=self.db.execute('SELECT start_at,end_at FROM report_state WHERE id=1').fetchone()
        last=self.last_reported_at()
        if row: return row[0],row[1]
        if last is None:
            try: last=(datetime.fromisoformat(end)-timedelta(days=1)).isoformat()
            except (TypeError,ValueError): raise ValueError('invalid report boundary') from None
        with self.db:
            self.db.execute('INSERT INTO report_state (id,start_at,end_at) VALUES (1,?,?)',(last,end))
        return last,end
    def clear_report_window(self):
        with self.db: self.db.execute('DELETE FROM report_state WHERE id=1')
    def complete_report_window(self,end,*,summary_cutoff=None):
        with self.db:
            self.db.execute('INSERT INTO report_meta (id,last_end_at) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET last_end_at=excluded.last_end_at',(end,))
            if summary_cutoff is not None:
                self.db.execute('INSERT INTO summary_meta (id,last_cutoff_at) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET last_cutoff_at=excluded.last_cutoff_at',(summary_cutoff,))
            self.db.execute('DELETE FROM report_state WHERE id=1')
    def reported(self,keys,at=None):
        boundary=at or now()
        with self.db:
            self.db.executemany('UPDATE reports SET reported_at=? WHERE source_key=? AND ready_at IS NOT NULL AND reported_at IS NULL',[(boundary,key) for key in keys])
    def close(self): self.db.close()
