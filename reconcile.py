"""Independent raw-log checks against exact stored events."""
from collectors.common import discover_files, usage_records
from store import TOKEN_COLUMNS


def raw_totals(source, path):
    if source == 'claude':
        from collectors.claude import collect
        return {e['event_key']: e for e in collect(path)}
    if source == 'gemini':
        last, session = {}, None
        for r in usage_records(path):
            session = session or r.get('sessionId')
            if session and r.get('type') == 'gemini' and r.get('id') and r.get('tokens'):
                last[r['id']] = r['tokens']
        return dict(count=len(last), total_tokens=sum(u.get('total', 0) or 0 for u in last.values()))
    if source == 'codex':
        threads, legacy, session = {}, {}, None
        has_responses = False
        for r in usage_records(path):
            p = r.get('payload') or {}
            if r.get('type') == 'session_meta':
                session = p.get('id')
            elif session and r.get('type') == 'token_usage_record':
                has_responses = True
                total = p.get('thread_token_usage')
                if total is not None:
                    threads[p.get('thread_id') or session] = total
            elif session and r.get('type') == 'event_msg' and p.get('type') == 'token_count':
                total = (p.get('info') or {}).get('total_token_usage')
                if total is not None:
                    legacy[p.get('thread_id') or session] = total
        values = (threads if has_responses else legacy).values()
        return dict(input_with_cache=sum(u.get('input_tokens', 0) or 0 for u in values),
                    output_tokens=sum(u.get('output_tokens', 0) or 0 for u in values))
    raise ValueError('Unsupported source: ' + source)


EXACT_FIELDS = tuple(c for c in TOKEN_COLUMNS if c not in ('output_tokens', 'reasoning_tokens'))


def check_claude(conn, path):
    """Every distinct response in the transcript must be stored once.

    A response can be present in several transcripts (resumed and forked sessions
    copy earlier history), so the comparison is by key, not by file totals. The
    stored copy may be a more complete one from another file, so output may be
    larger than this file's copy but never smaller.
    """
    raw = raw_totals('claude', path)
    missing = differing = 0
    keys = list(raw)
    for start in range(0, len(keys), 500):
        chunk = keys[start:start + 500]
        stored = {r['event_key']: r for r in conn.execute(
            "SELECT * FROM events WHERE source='claude' AND event_key IN (%s)"
            % ','.join('?' for _ in chunk), chunk)}
        for key in chunk:
            row = stored.get(key)
            if row is None:
                missing += 1
            elif (any(row[c] != raw[key][c] for c in EXACT_FIELDS)
                  or row['output_tokens'] < raw[key]['output_tokens']):
                differing += 1
    expected = dict(responses=len(keys), missing=0, differing=0)
    return expected, dict(responses=len(keys), missing=missing, differing=differing)


def stored_totals(conn, source, path):
    rows = conn.execute("SELECT * FROM events WHERE source=? AND source_path=? AND quality != 'estimated'",
                        (source, str(path))).fetchall()
    totals = {c: sum(r[c] for r in rows) for c in TOKEN_COLUMNS}
    if source == 'codex':
        return dict(input_with_cache=totals['input_tokens'] + totals['cache_read_tokens'],
                    output_tokens=totals['output_tokens'])
    return dict(count=len(rows), total_tokens=sum(totals[c] for c in TOKEN_COLUMNS if c != 'reasoning_tokens'))


def changed_since_scan(conn, path):
    """True when the file is not the one that was scanned (still being written, or new)."""
    row = conn.execute('SELECT mtime,size FROM file_fingerprints WHERE path=?', (str(path),)).fetchone()
    try:
        stat = path.stat()
    except OSError:
        return True
    return row is None or tuple(row) != (stat.st_mtime_ns, stat.st_size)


def verify(conn, files=None, *, verbose=True):
    files = list(discover_files()) if files is None else list(files)
    mismatches, pending = [], 0
    skipped = conn.execute("SELECT COUNT(*) FROM events WHERE quality='estimated'").fetchone()[0]
    for source, _account, path in files:
        if changed_since_scan(conn, path):
            pending += 1
            continue
        try:
            if source == 'claude':
                raw, stored = check_claude(conn, path)
            else:
                raw, stored = raw_totals(source, path), stored_totals(conn, source, path)
        except OSError as exc:
            raw, stored = {'error': str(exc)}, {}
        # A file that grew while it was being checked is not a mismatch.
        if raw != stored and not changed_since_scan(conn, path):
            mismatches.append(dict(source=source, path=str(path), raw=raw, stored=stored))
    checked = len(files) - pending
    result = dict(files_checked=checked, mismatches=mismatches, estimated_skipped=skipped,
                  changed_since_scan=pending)
    if verbose:
        for mismatch in mismatches:
            print(f"Mismatch [{mismatch['source']}] {mismatch['path']}: raw={mismatch['raw']} stored={mismatch['stored']}")
        print(f'Reconciliation: {checked} files checked / {len(mismatches)} mismatches / '
              f'{skipped} estimated events skipped / {pending} files changed since last scan')
    return result
