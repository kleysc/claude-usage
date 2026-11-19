import os
import unittest

import cli
from reconcile import verify
from collectors.common import discover_files
from tests.helpers import LogsCase


class Reconcile(LogsCase):
    def files(self):
        return list(discover_files(**self.dirs))

    def test_all_files_match(self):
        self.scan()
        result = verify(self.connect(), self.files(), verbose=False)
        self.assertEqual((result['files_checked'], result['mismatches']), (6, []))

    def test_tampered_totals_are_reported_per_source(self):
        self.scan()
        conn = self.connect()
        with conn:
            conn.execute("UPDATE events SET output_tokens = output_tokens + 1 WHERE event_key='resp_a'")
            conn.execute("DELETE FROM events WHERE source='gemini' AND event_key='g2'")
            conn.execute("DELETE FROM events WHERE source='claude' AND event_key LIKE '%msg_4%'")
        result = verify(conn, self.files(), verbose=False)
        self.assertEqual(sorted(m['source'] for m in result['mismatches']), ['claude', 'codex', 'gemini'])
        for mismatch in result['mismatches']:
            self.assertTrue(mismatch['path'])
            self.assertNotEqual(mismatch['raw'], mismatch['stored'])

    def test_estimated_events_are_skipped_and_counted(self):
        self.scan()
        conn = self.connect()
        with conn:
            conn.execute('''INSERT INTO events (source, event_key, account, session_id, timestamp, model,
                            project, cwd, git_branch, quality, source_path)
                            VALUES ('claude', 'legacy:1', 'default', 'old', '2026-04-01T00:00:00Z',
                                    'claude-sonnet-4-6', 'p', '', '', 'estimated', '')''')
        result = verify(conn, self.files(), verbose=False)
        self.assertEqual((result['estimated_skipped'], result['mismatches']), (1, []))

    def test_file_changed_after_the_scan_is_not_a_mismatch(self):
        self.scan()
        path = self.path('gemini', 'demo', 'chats', 'session-1.jsonl')
        with path.open('a') as stream:
            stream.write('{"id":"g3","type":"gemini","tokens":{"input":1,"output":1,"total":2}}\n')
        result = verify(self.connect(), self.files(), verbose=False)
        self.assertEqual((result['changed_since_scan'], result['mismatches']), (1, []))

    def test_verify_command_exit_status(self):
        self.scan()
        conn = self.connect()
        files = self.files()
        self.assertEqual(int(bool(verify(conn, files, verbose=False)['mismatches'])), 0)
        with conn:
            conn.execute("DELETE FROM events WHERE event_key='resp_1'")
        self.assertEqual(int(bool(verify(conn, files, verbose=False)['mismatches'])), 1)


if __name__ == '__main__':
    unittest.main()
