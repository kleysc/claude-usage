"""Gemini JSONL chat usage; rewritten message ids use the last copy."""
from pathlib import Path
from .common import event, usage_records


def collect(path):
    session_id = None
    responses = {}
    for record in usage_records(path):
        if session_id is None and record.get('sessionId'):
            session_id = 'gemini:' + record['sessionId']
        if not session_id or record.get('type') != 'gemini' or not record.get('tokens'):
            continue
        key = record.get('id')
        if not key:
            continue
        usage = record['tokens']
        cached = usage.get('cached', 0) or 0
        thoughts = usage.get('thoughts', 0) or 0
        responses[key] = event(
            'gemini', key, path, session_id, record.get('timestamp', ''),
            record.get('model') or 'unknown', project=Path(path).parent.parent.name,
            input_tokens=(usage.get('input', 0) or 0) - cached,
            cache_read_tokens=cached, output_tokens=(usage.get('output', 0) or 0) + thoughts,
            reasoning_tokens=thoughts)
    return list(responses.values())
