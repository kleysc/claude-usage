"""Shared test scaffolding: every test works on a temporary copy of the synthetic logs."""
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import store
from scanner import scan

FIXTURES = Path(__file__).with_name('fixtures')

# Days are local calendar days; pin the zone so the fixtures' dates are stable.
os.environ['TZ'] = 'UTC'
time.tzset()


class LogsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='usage-test-')).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copytree(FIXTURES, self.tmp / 'logs')
        self.dirs = dict(projects_dir=self.tmp / 'logs' / 'claude' / 'projects',
                         codex_dir=self.tmp / 'logs' / 'codex',
                         gemini_dir=self.tmp / 'logs' / 'gemini')
        self.db = self.tmp / 'usage.db'

    def scan(self, **overrides):
        return scan(db_path=self.db, verbose=False, **(self.dirs | overrides))

    def connect(self):
        conn = store.connect(self.db)
        self.addCleanup(conn.close)
        return conn

    def path(self, *parts):
        return self.tmp.joinpath('logs', *parts)
