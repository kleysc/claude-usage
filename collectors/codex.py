"""Codex per-response records and older cumulative token-count records."""
import hashlib
import json
from .common import event, usage_records


def collect(path):
    session_id = None
    model, cwd, branch = 'unknown', '', ''
    responses, legacy = {}, {}
    has_responses = False
    for record in usage_records(path):
        kind = record.get('type')
        payload = record.get('payload') or {}
        if kind == 'session_meta':
            session_id = payload['id']
            cwd = payload.get('cwd') or cwd
            branch = (payload.get('git') or {}).get('branch', '')
        elif kind == 'turn_context':
            model = payload.get('model') or 'unknown'
            cwd = payload.get('cwd') or cwd
        elif session_id and (kind == 'token_usage_record' or
                             (kind == 'event_msg' and payload.get('type') == 'token_count')):
            if kind == 'token_usage_record':
                has_responses = True
                usage = payload.get('usage')
                key = payload.get('response_id')
                target = responses
            else:
                info = payload.get('info') or {}
                usage = info.get('last_token_usage')
                total = info.get('total_token_usage')
                key = ('legacy:' + hashlib.sha256(json.dumps(
                    [session_id, total], sort_keys=True).encode()).hexdigest()) if total else None
                target = legacy
            if not usage or not key or key in target:
                continue
            cached = usage.get('cached_input_tokens', 0) or 0
            target[key] = event(
                'codex', key, path, 'codex:' + session_id,
                record.get('timestamp', ''), model, cwd, branch,
                input_tokens=(usage.get('input_tokens', 0) or 0) - cached,
                cache_read_tokens=cached,
                cache_write_5m_tokens=usage.get('cache_write_input_tokens', 0) or 0,
                output_tokens=usage.get('output_tokens', 0) or 0,
                reasoning_tokens=usage.get('reasoning_output_tokens', 0) or 0)
    return list((responses if has_responses else legacy).values())
