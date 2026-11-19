import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from http.server import HTTPServer
from unittest import mock

import dashboard
import queries
from tests.helpers import LogsCase

TODAY = date(2026, 10, 7)


class Overview(LogsCase):
    def setUp(self):
        super().setUp()
        self.scan()

    def overview(self, **filters):
        return queries.overview(self.connect(), today=TODAY, **filters)

    def test_breakdowns_add_up_to_the_total(self):
        data = self.overview()
        total = data['totals']['cost']
        self.assertGreater(total, 0)
        self.assertAlmostEqual(sum(r['cost'] or 0 for r in data['by_source']), total)
        self.assertAlmostEqual(sum(r['cost'] or 0 for r in data['by_account']), total)
        self.assertAlmostEqual(sum(r['cost'] or 0 for r in data['by_model']), total)
        self.assertAlmostEqual(sum(v for d in data['days'] for k, v in d.items() if k != 'day'), total, places=3)
        self.assertAlmostEqual(sum(r['cost'] or 0 for r in data['sessions']), total)
        self.assertAlmostEqual(sum(data['totals']['cost_parts'].values()), total)
        self.assertAlmostEqual(sum(r['cost'] or 0 for r in data['by_project']) + data['other_projects_cost'], total)

    def test_tool_filter_covers_everything_but_the_filter_values(self):
        data = self.overview(source='codex')
        self.assertEqual({r['source'] for r in data['by_source']}, {'codex'})
        self.assertEqual({r['source'] for r in data['sessions']}, {'codex'})
        self.assertEqual({k for d in data['days'] for k in d if k != 'day'}, {'codex'})
        self.assertEqual(data['options']['sources'], ['claude', 'codex', 'gemini'])

    def test_model_filter(self):
        data = self.overview(models=['gpt-5.4'])
        self.assertEqual(data['totals']['turns'], 1)
        self.assertEqual([r['model'] for r in data['by_model']], ['gpt-5.4'])

    def test_previous_period_has_the_same_length(self):
        data = self.overview(start='2026-10-05', end='2026-10-07')
        self.assertEqual(data['range']['days'], 3)
        self.assertEqual((data['previous']['start'], data['previous']['end']), ('2026-10-02', '2026-10-04'))
        # msg_4 (2026-10-02) is the only response in the previous three days
        self.assertEqual(data['previous']['events'], 1)
        self.assertAlmostEqual(data['daily_average'], data['totals']['cost'] / 3)

    def test_project_filter(self):
        data = self.overview(project='acme/api')
        self.assertEqual({r['project'] for r in data['by_project']}, {'acme/api'})
        self.assertEqual({r['source'] for r in data['by_source']}, {'codex'})

    def test_every_day_of_the_period_is_listed(self):
        data = self.overview(start='2026-10-01', end='2026-10-07')
        self.assertEqual([d['day'] for d in data['days']],
                         ['2026-10-0%d' % n for n in range(1, 8)])
        self.assertEqual(data['days'][2], {'day': '2026-10-03'})  # no usage that day

    def test_rows_carry_trend_and_previous_cost(self):
        data = self.overview(start='2026-10-05', end='2026-10-07')
        for row in data['by_project'] + data['by_model'] + data['by_source']:
            self.assertEqual(len(row['series']), 3)
            self.assertAlmostEqual(sum(row['series']), row['cost'] or 0, places=3)
        demo = next(r for r in self.overview(start='2026-10-02', end='2026-10-02')['by_project']
                    if r['project'] == 'acme/demo')
        self.assertGreater(demo['previous_cost'], 0)  # msg_1 and msg_2 were on 2026-10-01
        self.assertIsNone(self.overview()['by_project'][0]['previous_cost'])

    def test_all_time_has_no_previous_period(self):
        self.assertIsNone(self.overview()['previous'])

    def test_month_projection(self):
        month = self.overview()['month']
        self.assertEqual((month['label'], month['day'], month['days']), ('2026-10', 7, 31))
        self.assertAlmostEqual(month['projected'], month['cost'] / 7 * 31)
        self.assertGreater(month['cost'], 0)

    def test_sessions_are_sorted_by_cost(self):
        costs = [r['cost'] or 0 for r in self.overview()['sessions']]
        self.assertEqual(costs, sorted(costs, reverse=True))

    def test_overview_is_json_serialisable(self):
        json.dumps(self.overview())


class LocalDays(unittest.TestCase):
    def test_event_is_counted_on_its_local_date(self):
        import os
        import time
        previous = os.environ.get('TZ')
        os.environ['TZ'] = 'America/Bogota'  # UTC-5
        time.tzset()
        try:
            # 23:30 local on the 5th is 04:30 UTC on the 6th
            self.assertEqual(queries.local_day('2026-10-06T04:30:00Z'), '2026-10-05')
        finally:
            os.environ['TZ'] = previous or 'UTC'
            time.tzset()


class Server(LogsCase):
    def setUp(self):
        super().setUp()
        self.scan()
        patches = [mock.patch.object(dashboard, 'DB_PATH', self.db),
                   mock.patch.object(dashboard.get_dashboard_data, '__defaults__', (self.db,)),
                   mock.patch.object(dashboard, 'run_scan', lambda: dict(events_added=0, files_read=0, mismatches=0))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.server = HTTPServer(('127.0.0.1', 0), dashboard.DashboardHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def get(self, path, method='GET'):
        request = urllib.request.Request(self.base + path, method=method)
        with urllib.request.urlopen(request) as response:
            return response.headers['Content-Type'], response.read()

    def test_static_files(self):
        for path, kind in (('/', 'text/html'), ('/app.js', 'text/javascript'), ('/style.css', 'text/css')):
            content_type, body = self.get(path)
            self.assertTrue(content_type.startswith(kind))
            self.assertGreater(len(body), 500)

    def test_unknown_path_is_not_served(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get('/prices.json')
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_data_endpoint_applies_filters(self):
        _, body = self.get('/api/data?source=gemini&start=2026-10-01&end=2026-10-31')
        data = json.loads(body)
        self.assertEqual({r['source'] for r in data['by_source']}, {'gemini'})
        self.assertEqual(data['totals']['turns'], 2)
        self.assertIn('last_scan', data)

    def test_scan_endpoint(self):
        _, body = self.get('/api/scan', method='POST')
        self.assertEqual(json.loads(body)['mismatches'], 0)


if __name__ == '__main__':
    unittest.main()
