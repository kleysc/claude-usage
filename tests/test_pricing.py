import unittest

import pricing
import queries

PRICES = [
    dict(model='m', match='prefix', effective_from='2026-01-01', input=4, output=20, cache_read=0.2,
         cache_write_5m=5, cache_write_1h=8, fast=dict(input=8, output=40), verified=True),
    dict(model='m', match='prefix', effective_from='2026-09-01', input=2, output=10, cache_read=0.1,
         cache_write_5m=2.5, cache_write_1h=4, verified=True),
    dict(model='m-pro', match='prefix', effective_from='2026-01-01', input=100, output=100,
         cache_read=100, cache_write_5m=100, cache_write_1h=100, verified=False),
]


def event(model='m', timestamp='2026-08-15T00:00:00Z', quality='exact', speed='standard', **tokens):
    base = dict(source='claude', session_id='s', model=model, timestamp=timestamp, quality=quality,
                speed=speed, input_tokens=0, output_tokens=0, cache_read_tokens=0,
                cache_write_5m_tokens=0, cache_write_1h_tokens=0, reasoning_tokens=0)
    return base | tokens


class Pricing(unittest.TestCase):
    def test_price_in_effect_on_the_event_date(self):
        self.assertEqual(pricing.price_event(event(input_tokens=1_000_000), PRICES), 4)
        self.assertEqual(pricing.price_event(
            event(timestamp='2026-09-15T00:00:00Z', input_tokens=1_000_000), PRICES), 2)

    def test_cache_writes_priced_by_duration(self):
        cost = pricing.price_event(event(cache_write_5m_tokens=1_000_000,
                                         cache_write_1h_tokens=1_000_000), PRICES)
        self.assertEqual(cost, 13)

    def test_every_category_has_its_own_rate(self):
        cost = pricing.price_event(event(input_tokens=1_000_000, output_tokens=1_000_000,
                                         cache_read_tokens=1_000_000), PRICES)
        self.assertAlmostEqual(cost, 24.2)

    def test_fast_mode_rates(self):
        cost = pricing.price_event(event(speed='fast', input_tokens=1_000_000,
                                         output_tokens=1_000_000), PRICES)
        self.assertEqual(cost, 48)

    def test_unknown_model_has_no_cost(self):
        self.assertIsNone(pricing.price_event(event(model='other', input_tokens=5), PRICES))

    def test_event_before_any_price_has_no_cost(self):
        self.assertIsNone(pricing.price_event(
            event(timestamp='2025-06-01T00:00:00Z', input_tokens=5), PRICES))

    def test_most_specific_entry_wins(self):
        self.assertEqual(pricing.lookup('m-pro-20260101', '2026-08-15', PRICES)['model'], 'm-pro')
        self.assertEqual(pricing.lookup('m-20260101', '2026-08-15', PRICES)['model'], 'm')

    def test_shipped_price_file_resolves_model_families(self):
        for model, entry in [('claude-fable-5-2', 'claude-fable-5-2'),
                             ('claude-fable-5-1', 'claude-fable-5-1'),
                             ('claude-fable-5', 'claude-fable-5'),
                             ('claude-opus-5-5', 'claude-opus-5-5'),
                             ('claude-opus-5', 'claude-opus-5'),
                             ('claude-haiku-4-5-20251001', 'claude-haiku-4-5'),
                             ('gpt-5.4-mini', 'gpt-5.4-mini'), ('gpt-5.4', 'gpt-5.4')]:
            self.assertEqual(pricing.lookup(model, '2026-11-01')['model'], entry)
        self.assertIsNone(pricing.lookup('<synthetic>', '2026-11-01'))


class Aggregates(unittest.TestCase):
    def test_unpriced_tokens_are_reported_not_costed(self):
        total = queries.aggregate([event(input_tokens=1_000_000),
                                   event(model='other', input_tokens=700, output_tokens=300)], PRICES)
        self.assertEqual(total['cost'], 4)
        self.assertEqual(total['unpriced_tokens'], 1000)
        self.assertEqual(total['input_tokens'], 1_000_700)

    def test_only_unpriced_usage_gives_null_cost(self):
        total = queries.aggregate([event(model='other', input_tokens=700)], PRICES)
        self.assertIsNone(total['cost'])

    def test_estimated_and_unverified_amounts(self):
        total = queries.aggregate([
            event(input_tokens=1_000_000),
            event(input_tokens=1_000_000, quality='estimated'),
            event(model='m-pro', input_tokens=10_000)], PRICES)
        self.assertEqual(total['cost'], 9)
        self.assertEqual(total['estimated_cost'], 4)
        self.assertEqual(total['unverified_cost'], 1)
        self.assertEqual(total['quality'], 'mixed')


if __name__ == '__main__':
    unittest.main()
