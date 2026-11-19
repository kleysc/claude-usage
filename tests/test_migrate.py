import io
import sqlite3
import unittest
from contextlib import redirect_stdout
from unittest import mock

import migrate
import schedule
from tests.helpers import LogsCase

LEGACY_SCHEMA = '''
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project_name TEXT, git_branch TEXT);
CREATE TABLE turns (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, timestamp TEXT, model TEXT,
    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER, tool_name TEXT, cwd TEXT, account TEXT,
    source TEXT NOT NULL DEFAULT 'claude');
CREATE TABLE processed_files (path TEXT PRIMARY KEY, mtime REAL, lines INTEGER);
'''


def turn(session, ts, model, i, o, cr, cc):
    return (session, ts, model, i, o, cr, cc, '/work/acme/old', 'default')


class Migration(LogsCase):
    def setUp(self):
        super().setUp()
        conn = sqlite3.connect(self.db)
        conn.executescript(LEGACY_SCHEMA)
        rows = [
            # Only in the old table: two responses, the first written three times.
            turn('gone', '2026-04-01T10:00:00Z', 'claude-sonnet-4-6', 5, 10, 1000, 300),
            turn('gone', '2026-04-01T10:00:01Z', 'claude-sonnet-4-6', 5, 10, 1000, 300),
            turn('gone', '2026-04-01T10:00:02Z', 'claude-sonnet-4-6', 5, 80, 1000, 300),
            turn('gone', '2026-04-01T10:05:00Z', 'claude-sonnet-4-6', 6, 20, 1300, 0),
            # A fork of the same old session repeats its first response.
            turn('gone-fork', '2026-04-02T09:00:00Z', 'claude-sonnet-4-6', 5, 80, 1000, 300),
            turn('gone-fork', '2026-04-02T09:01:00Z', 'claude-sonnet-4-6', 9, 15, 1600, 0),
            # Still on disk (double counted in the old table): must not be imported.
            turn('sess-a', '2026-10-01T10:00:01Z', 'claude-fable-5-2', 3, 10, 200, 5000),
            turn('sess-a', '2026-10-01T10:00:03Z', 'claude-fable-5-2', 3, 50, 200, 5000),
        ]
        conn.executemany('INSERT INTO turns (session_id, timestamp, model, input_tokens, output_tokens,'
                         ' cache_read_tokens, cache_creation_tokens, cwd, account) VALUES (?,?,?,?,?,?,?,?,?)', rows)
        conn.execute("INSERT INTO sessions VALUES ('gone', 'acme/old', 'legacy-branch')")
        conn.commit()
        conn.close()

    def run_migrate(self, **kwargs):
        with redirect_stdout(io.StringIO()) as out:
            code = migrate.migrate(self.db, verbose=True, **self.dirs, **kwargs)
        return code, out.getvalue()

    def test_migration_builds_events_and_keeps_a_backup(self):
        original = self.db.read_bytes()
        code, output = self.run_migrate()
        self.assertEqual(code, 0)
        backups = list(self.tmp.glob('usage.db.bak-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertFalse((self.tmp / 'usage.db.migrating').exists())
        self.assertIn('2026-04', output)
        conn = self.connect()
        estimated = conn.execute("SELECT session_id, output_tokens, cache_write_5m_tokens, git_branch,"
                                 " project FROM events WHERE quality='estimated' ORDER BY timestamp").fetchall()
        # 'gone': 3 copies collapse to 1 (largest output) + 1; the fork keeps only its new response.
        self.assertEqual([tuple(r) for r in estimated], [
            ('gone', 80, 300, 'legacy-branch', 'acme/old'),
            ('gone', 20, 0, 'legacy-branch', 'acme/old'),
            ('gone-fork', 15, 0, '', 'acme/old')])
        exact = conn.execute("SELECT COUNT(*) FROM events WHERE session_id='sess-a'").fetchone()[0]
        self.assertGreater(exact, 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events WHERE session_id='sess-a'"
                                      " AND quality='estimated'").fetchone()[0], 0)
        # Old tables are kept as they were.
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM turns').fetchone()[0], 8)

    def test_migrating_twice_adds_nothing(self):
        self.run_migrate()
        conn = self.connect()
        first = conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        conn.close()
        self.assertEqual(self.run_migrate()[0], 0)
        self.assertEqual(self.connect().execute('SELECT COUNT(*) FROM events').fetchone()[0], first)

    def test_failed_verification_leaves_the_live_file_untouched(self):
        original = self.db.read_bytes()
        bad = dict(files_checked=1, mismatches=[dict(source='codex', path='x', raw={}, stored={})],
                   estimated_skipped=0, changed_since_scan=0)
        with mock.patch.object(migrate, 'verify', return_value=bad):
            code, output = self.run_migrate()
        self.assertEqual(code, 1)
        self.assertIn('aborted', output)
        self.assertEqual(self.db.read_bytes(), original)
        self.assertEqual(list(self.tmp.glob('usage.db.bak-*')), [])
        self.assertFalse((self.tmp / 'usage.db.migrating').exists())

    def test_dry_run_changes_nothing(self):
        original = self.db.read_bytes()
        code, output = self.run_migrate(dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.db.read_bytes(), original)
        self.assertEqual(list(self.tmp.glob('usage.db.bak-*')), [])


class Schedule(unittest.TestCase):
    def test_plist_runs_a_daily_scan(self):
        plist = schedule.build_plist(python='/usr/bin/python3', cli='/opt/tool/cli.py')
        self.assertEqual(plist['ProgramArguments'][0], '/usr/bin/python3')
        self.assertEqual(plist['ProgramArguments'][-1], 'scan')
        self.assertTrue(plist['ProgramArguments'][1].endswith('cli.py'))
        self.assertEqual(set(plist['StartCalendarInterval']), {'Hour', 'Minute'})


if __name__ == '__main__':
    unittest.main()
