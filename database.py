import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB = str(Path(__file__).with_name('careerpilot.db'))

def path():
    return os.getenv('DATABASE_PATH') or DEFAULT_DB

@contextmanager
def connect():
    p = path()
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            salary_min REAL,
            salary_max REAL,
            salary_verified INTEGER NOT NULL DEFAULT 0,
            match_score INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Saved',
            notes TEXT NOT NULL DEFAULT '',
            deadline TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
            canonical_url TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL DEFAULT 'Queued',
            reason TEXT NOT NULL DEFAULT '',
            cover_letter TEXT NOT NULL DEFAULT '',
            materials_hash TEXT NOT NULL DEFAULT '',
            profile_hash TEXT NOT NULL DEFAULT '',
            approved_hash TEXT NOT NULL DEFAULT '',
            questions TEXT NOT NULL DEFAULT '[]',
            draft_answers TEXT NOT NULL DEFAULT '{}',
            approved_answers TEXT NOT NULL DEFAULT '{}',
            receipt TEXT NOT NULL DEFAULT '',
            claim_time TEXT,
            submit_started TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS application_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL REFERENCES applications(id),
            state TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS application_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            mime TEXT NOT NULL,
            content BLOB NOT NULL,
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS applications_state ON applications(state, id);
        ''')
        columns = {r['name'] for r in db.execute('PRAGMA table_info(applications)')}
        for name in ('draft_answers', 'approved_answers'):
            if name not in columns:
                db.execute(f"ALTER TABLE applications ADD COLUMN {name} TEXT NOT NULL DEFAULT '{{}}'")

