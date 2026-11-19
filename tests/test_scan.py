import json
import os
import unittest

import cli
import dashboard
import queries
from tests.helpers import LogsCase


def count(conn, where='1=1'):
    return conn.execute(f'SELECT COUNT(*) FROM events WHERE {where}').fetchone()[0]


class Scan(LogsCase):
    def test_counts_per_source(self):
        result = self.scan()
        conn = self.connect()
        # Claude: msg_1 (in both files, stored once), msg_2, msg_3, msg_4
        self.assertEqual(count(conn, "source='claude'"), 4)
        self.assertEqual(count(conn, "source='codex'"), 8)
        self.assertEqual(count(conn, "source='gemini'"), 2)
        self.assertEqual(result['reconciliation']['mismatches'], [])

    def test_second_scan_adds_nothing_and_skips_files(self):
        first = self.scan()
        second = self.scan()
        self.assertEqual(second['turns'], 0)
        self.assertEqual(second['skipped'], first['new'])
        self.assertEqual(second['new'] + second['updated'], 0)

    def test_rescanning_everything_is_idempotent(self):
        self.scan()
        conn = self.connect()
        before = conn.execute('SELECT * FROM events ORDER BY source, event_key').fetchall()
        with conn:
            conn.execute('DELETE FROM file_fingerprints')
        result = self.scan()
        self.assertEqual(result['turns'], 0)
        after = conn.execute('SELECT * FROM events ORDER BY source, event_key').fetchall()
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in after])

    def test_growing_file_adds_only_new_responses(self):
        self.scan()
        path = self.path('codex', '2026', '10', '05', 'rollout-new.jsonl')
        record = dict(timestamp='2026-10-05T09:00:00Z', type='token_usage_record', payload=dict(
            response_id='resp_3', thread_id='t1',
            usage=dict(input_tokens=100, cached_input_tokens=0, output_tokens=7),
            thread_token_usage=dict(input_tokens=12100, cached_input_tokens=9500, output_tokens=157)))
        with path.open('a') as stream:
            stream.write(json.dumps(record) + '\n')
        result = self.scan()
        self.assertEqual((result['turns'], result['updated']), (1, 1))
        self.assertEqual(result['reconciliation']['mismatches'], [])

    def test_missing_tool_directories(self):
        result = self.scan(codex_dir=self.tmp / 'absent', gemini_dir=self.tmp / 'absent-too')
        self.assertEqual(result['source_files'], dict(claude=2, codex=0, gemini=0))

    def test_session_view_matches_its_events(self):
        self.scan()
        conn = self.connect()
        for row in conn.execute('SELECT * FROM event_sessions'):
            total = conn.execute(
                'SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cache_read_tokens) '
                'FROM events WHERE source=? AND session_id=?', (row['source'], row['session_id'])).fetchone()
            self.assertEqual(tuple(total), (row['turn_count'], row['total_input_tokens'],
                                            row['total_output_tokens'], row['total_cache_read']))

    def test_legacy_tables_are_left_alone(self):
        conn = self.connect()
        conn.executescript('''CREATE TABLE turns (id INTEGER PRIMARY KEY, session_id TEXT);
                              CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
                              INSERT INTO turns (session_id) VALUES ('old');
                              INSERT INTO sessions VALUES ('old');''')
        conn.commit()
        self.scan()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM turns').fetchone()[0], 1)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)

    def test_cli_and_api_report_the_same_cost(self):
        self.scan()
        for filters in ({}, dict(source='codex'), dict(start='2026-10-05', end='2026-10-06'),
                        dict(models=['claude-fable-5-2'])):
            report = cli.get_report(self.db, **filters)
            api = dashboard.get_dashboard_data(self.db, **filters)
            self.assertIsNotNone(report['totals']['cost'])
            self.assertEqual(report['totals']['cost'], api['totals']['cost'])
            self.assertEqual(report['totals']['unpriced_tokens'], api['totals']['unpriced_tokens'])

    def test_rows_sum_to_the_total(self):
        self.scan()
        conn = self.connect()
        total = queries.totals(conn)
        for rows in (queries.daily(conn), queries.by_model(conn), queries.by_session(conn)):
            self.assertAlmostEqual(sum(r['cost'] or 0 for r in rows), total['cost'])


class PageHasNoPrices(unittest.TestCase):
    def test_no_price_table_in_the_page(self):
        for name in ('index.html', 'app.js'):
            text = (dashboard.WEB_DIR / name).read_text()
            self.assertNotIn('PRICING', text)
            self.assertNotIn('calcCost', text)
        self.assertFalse(hasattr(dashboard, 'HTML_TEMPLATE'))


if __name__ == '__main__':
    unittest.main()
