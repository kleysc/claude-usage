import unittest

from collectors import claude, codex, gemini
from tests.helpers import FIXTURES


def by_key(events, fragment):
    return next(e for e in events if fragment in e['event_key'])


class ClaudeCollector(unittest.TestCase):
    def setUp(self):
        self.events = claude.collect(FIXTURES / 'claude/projects/-work-acme-demo/sess-a.jsonl')

    def test_one_event_per_response(self):
        # msg_1 is written three times; the record without usage is ignored.
        self.assertEqual(len(self.events), 3)

    def test_last_copy_wins(self):
        event = by_key(self.events, 'msg_1')
        self.assertEqual(event['output_tokens'], 50)
        self.assertEqual(event['reasoning_tokens'], 20)

    def test_cache_writes_split_by_duration(self):
        event = by_key(self.events, 'msg_1')
        self.assertEqual((event['cache_write_5m_tokens'], event['cache_write_1h_tokens']), (1000, 4000))
        self.assertEqual((event['input_tokens'], event['cache_read_tokens']), (3, 200))

    def test_cache_write_without_split_counts_as_5m(self):
        event = by_key(self.events, 'msg_2')
        self.assertEqual((event['cache_write_5m_tokens'], event['cache_write_1h_tokens']), (70, 0))

    def test_fast_and_sidechain(self):
        self.assertEqual(by_key(self.events, 'msg_2')['speed'], 'fast')
        self.assertEqual(by_key(self.events, 'msg_2')['is_sidechain'], 1)
        self.assertEqual(by_key(self.events, 'msg_1')['speed'], 'standard')
        self.assertEqual(by_key(self.events, 'msg_1')['is_sidechain'], 0)

    def test_session_and_project(self):
        event = by_key(self.events, 'msg_1')
        self.assertEqual((event['session_id'], event['project'], event['git_branch']),
                         ('sess-a', 'acme/demo', 'main'))


class CodexCollector(unittest.TestCase):
    def test_new_format_uses_response_records_only(self):
        events = codex.collect(FIXTURES / 'codex/2026/10/05/rollout-new.jsonl')
        self.assertEqual(len(events), 2)  # resp_2 is repeated, token_count is ignored
        first = by_key(events, 'resp_1')
        self.assertEqual((first['input_tokens'], first['cache_read_tokens'], first['output_tokens']),
                         (1000, 9000, 100))
        self.assertEqual(first['reasoning_tokens'], 40)
        self.assertEqual(first['model'], 'gpt-6-astra')
        self.assertEqual(by_key(events, 'resp_2')['model'], 'gpt-5.4')
        self.assertEqual(first['session_id'], 'codex:cx-new')

    def test_old_format_skips_null_info_and_repeats(self):
        events = codex.collect(FIXTURES / 'codex/2026/08/01/rollout-old.jsonl')
        self.assertEqual(len(events), 2)
        self.assertEqual(sum(e['input_tokens'] + e['cache_read_tokens'] for e in events), 1300)
        self.assertEqual(sum(e['output_tokens'] for e in events), 50)

    def test_multi_thread_file(self):
        events = codex.collect(FIXTURES / 'codex/2026/10/06/rollout-threads.jsonl')
        self.assertEqual(len(events), 4)
        self.assertEqual(sum(e['input_tokens'] + e['cache_read_tokens'] for e in events), 3700)


class GeminiCollector(unittest.TestCase):
    def test_rewritten_id_keeps_last_copy(self):
        events = gemini.collect(FIXTURES / 'gemini/demo/chats/session-1.jsonl')
        self.assertEqual(len(events), 2)
        first = by_key(events, 'g1')
        # output includes thoughts; input excludes cached
        self.assertEqual((first['input_tokens'], first['cache_read_tokens'], first['output_tokens']),
                         (400, 600, 70))
        self.assertEqual((first['session_id'], first['project']), ('gemini:gm-1', 'demo'))


if __name__ == '__main__':
    unittest.main()
