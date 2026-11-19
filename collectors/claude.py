"""Claude assistant usage: the last copy of each response wins."""
import json
from pathlib import Path
from .common import event, usage_records


def collect(path):
    account = 'default'
    for parent in Path(path).parents:
        if parent.name.startswith('.claude-'):
            account = parent.name[len('.claude-'):]
            break
        if parent.name == '.claude':
            break
    responses = {}
    for record in usage_records(path):
        if record.get('type') != 'assistant' or not record.get('sessionId'):
            continue
        message = record.get('message') or {}
        usage = message.get('usage')
        if not isinstance(usage, dict) or not message.get('id'):
            continue
        key = json.dumps([message['id'], record.get('requestId')], separators=(',', ':'))
        creation = usage.get('cache_creation')
        if isinstance(creation, dict):
            write_5m = creation.get('ephemeral_5m_input_tokens', 0) or 0
            write_1h = creation.get('ephemeral_1h_input_tokens', 0) or 0
        else:
            write_5m = usage.get('cache_creation_input_tokens', 0) or 0
            write_1h = 0
        responses[key] = event(
            'claude', key, path, record['sessionId'], record.get('timestamp', ''),
            message.get('model') or 'unknown', record.get('cwd', ''), record.get('gitBranch', ''),
            account=account, input_tokens=usage.get('input_tokens', 0) or 0,
            output_tokens=usage.get('output_tokens', 0) or 0,
            cache_read_tokens=usage.get('cache_read_input_tokens', 0) or 0,
            cache_write_5m_tokens=write_5m, cache_write_1h_tokens=write_1h,
            reasoning_tokens=(usage.get('output_tokens_details') or {}).get('thinking_tokens', 0) or 0,
            speed='fast' if usage.get('speed') == 'fast' else 'standard',
            is_sidechain=int(bool(record.get('isSidechain'))))
    return list(responses.values())
