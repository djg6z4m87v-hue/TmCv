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
        ''')
