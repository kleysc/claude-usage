"""Additive event storage. Legacy tables are never modified by this module."""
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get('CLAUDE_USAGE_DB', Path.home() / '.claude' / 'usage.db'))
TOKEN_COLUMNS = ('input_tokens', 'output_tokens', 'cache_read_tokens',
                 'cache_write_5m_tokens', 'cache_write_1h_tokens', 'reasoning_tokens')
COLUMNS = ('source', 'event_key', 'account', 'session_id', 'timestamp', 'model',
           'project', 'cwd', 'git_branch', *TOKEN_COLUMNS, 'speed', 'is_sidechain',
           'quality', 'source_path')


def connect(db_path=DB_PATH, *, initialize=False):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if initialize:
        init_db(conn)
    return conn


def init_db(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS events (
            source TEXT NOT NULL, event_key TEXT NOT NULL, account TEXT NOT NULL,
            session_id TEXT NOT NULL, timestamp TEXT NOT NULL, model TEXT NOT NULL,
            project TEXT NOT NULL, cwd TEXT NOT NULL, git_branch TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_5m_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_1h_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            speed TEXT NOT NULL DEFAULT 'standard', is_sidechain INTEGER NOT NULL DEFAULT 0,
            quality TEXT NOT NULL DEFAULT 'exact', source_path TEXT NOT NULL,
            UNIQUE(source, event_key)
        );
        CREATE INDEX IF NOT EXISTS events_timestamp ON events(timestamp);
        CREATE INDEX IF NOT EXISTS events_session ON events(session_id);
        CREATE INDEX IF NOT EXISTS events_path ON events(source_path);
        CREATE TABLE IF NOT EXISTS file_fingerprints (
            path TEXT PRIMARY KEY, mtime INTEGER NOT NULL, size INTEGER NOT NULL
        );
        CREATE VIEW IF NOT EXISTS event_sessions AS
            SELECT session_id, source, account, MIN(project) AS project_name,
                   MIN(timestamp) AS first_timestamp, MAX(timestamp) AS last_timestamp,
                   MIN(git_branch) AS git_branch,
                   SUM(input_tokens) AS total_input_tokens,
                   SUM(output_tokens) AS total_output_tokens,
                   SUM(cache_read_tokens) AS total_cache_read,
                   SUM(cache_write_5m_tokens) AS total_cache_write_5m,
                   SUM(cache_write_1h_tokens) AS total_cache_write_1h,
                   SUM(cache_write_5m_tokens + cache_write_1h_tokens) AS total_cache_creation,
                   SUM(reasoning_tokens) AS reasoning_tokens, COUNT(*) AS turn_count,
                   CASE WHEN COUNT(DISTINCT model)=1 THEN MIN(model) ELSE 'mixed' END AS model,
                   CASE WHEN MIN(quality)=MAX(quality) THEN MIN(quality) ELSE 'mixed' END AS quality
            FROM events GROUP BY source, account, session_id;
    ''')


def upsert(conn, events):
    """Return the number of new keys; replacements do not count as additions."""
    added = 0
    sql = ('INSERT INTO events (' + ','.join(COLUMNS) + ') VALUES (' +
           ','.join('?' for _ in COLUMNS) + ') ON CONFLICT(source,event_key) DO UPDATE SET ' +
           ','.join(c + '=excluded.' + c for c in COLUMNS if c not in ('source', 'event_key')) +
           # A resumed or forked Claude session copies earlier responses into a second
           # file; keep the most complete copy whichever file is scanned last.
           " WHERE excluded.source != 'claude' OR excluded.output_tokens >= events.output_tokens")
    for event in events:
        exists = conn.execute('SELECT 1 FROM events WHERE source=? AND event_key=?',
                              (event['source'], event['event_key'])).fetchone()
        conn.execute(sql, tuple(event[c] for c in COLUMNS))
        added += exists is None
    return added
