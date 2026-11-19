"""Dated API-equivalent prices. Unknown models/categories remain unpriced."""
import json
from pathlib import Path

PRICE_PATH = Path(__file__).with_name('prices.json')
TOKEN_RATES = {
    'input_tokens': 'input', 'output_tokens': 'output', 'cache_read_tokens': 'cache_read',
    'cache_write_5m_tokens': 'cache_write_5m', 'cache_write_1h_tokens': 'cache_write_1h',
}


def load_prices(path=PRICE_PATH):
    # Reload for each query so correcting prices never requires a rescan/restart.
    return json.loads(Path(path).read_text(encoding='utf-8'))


def lookup(model, timestamp, prices=None):
    prices = load_prices() if prices is None else prices
    matches = [p for p in prices if model and (
        model == p['model'] or (p['match'] == 'prefix' and model.startswith(p['model'])))]
    if not matches:
        return None
    # Choose the model family first: an unavailable date must not select a broader family.
    specificity = max(len(p['model']) for p in matches)
    eligible = [p for p in matches if len(p['model']) == specificity
                and p['effective_from'] <= (timestamp or '')[:10]]
    return max(eligible, key=lambda p: p['effective_from']) if eligible else None


def price_parts(event, entry):
    """Cost of one event per token category, or None when it cannot be priced."""
    if entry is None:
        return None
    rates = entry | (entry.get('fast', {}) if event.get('speed') == 'fast' else {})
    if any(event.get(token, 0) and rates.get(rate) is None for token, rate in TOKEN_RATES.items()):
        return None
    return {rate: event.get(token, 0) * (rates.get(rate) or 0) / 1_000_000
            for token, rate in TOKEN_RATES.items()}


def price_event(event, prices=None):
    parts = price_parts(event, lookup(event.get('model'), event.get('timestamp'), prices))
    return None if parts is None else sum(parts.values())
