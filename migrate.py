"""Legacy import and a migration that builds and verifies a copy before swapping it in."""
import os
import shutil
import sqlite3
import time
from pathlib import Path

import queries
import store
from collectors.common import CODEX_DIR, GEMINI_DIR, discover_files, project_name_from_cwd
from reconcile import verify

LEGACY_TABLES = ('turns', 'sessions', 'processed_files')


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}


def collapse_legacy(rows, known=()):
    """Turn double-counted legacy `turns` rows into one row per API response.

    `rows` must be ordered by session and then by id. The old scanner stored one
    row per transcript record, and a response was written as several records.

    1. Inside a session, consecutive rows with the same model and the same input,
       cache-read and cache-creation counts are copies of one response; the copy
       with the largest output is kept.
    2. A response copied into another session (resume or fork) repeats all five
       values; such repeats, and anything already stored as an exact event
       (`known`), are dropped. Rows with no cache activity are too generic to
       fingerprint and are always kept.
    """
    seen = set(known)
    out, current, previous = [], None, None

    def flush():
        if current is None:
            return
        fingerprint = (current['model'], current['input_tokens'], current['output_tokens'],
                       current['cache_read_tokens'], current['cache_creation_tokens'])
        if current['cache_read_tokens'] + current['cache_creation_tokens'] > 0:
            if fingerprint in seen:
                return
            seen.add(fingerprint)
        out.append(current)

    for row in rows:
        row = dict(row)
        key = (row['session_id'], row['model'], row['input_tokens'],
               row['cache_read_tokens'], row['cache_creation_tokens'])
        if key == previous:
            current['output_tokens'] = max(current['output_tokens'], row['output_tokens'])
        else:
            flush()
            current, previous = row, key
    flush()
    return out


def import_legacy(conn):
    """Import Claude sessions that exist only in the legacy `turns` table as estimates."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'turns' not in tables:
        return 0
    columns = _columns(conn, 'turns')
    where = "WHERE t.source='claude'" if 'source' in columns else ''
    account = 't.account' if 'account' in columns else "'default'"
    rows = conn.execute(f'''
        SELECT t.id, t.session_id, t.timestamp, t.model, t.input_tokens, t.output_tokens,
               t.cache_read_tokens, t.cache_creation_tokens, t.cwd, {account} AS account
        FROM turns t {where} ORDER BY t.session_id, t.id''').fetchall()
    rebuilt = {r[0] for r in conn.execute(
        "SELECT DISTINCT session_id FROM events WHERE source='claude' AND quality='exact'")}
    known = {tuple(r) for r in conn.execute(
        "SELECT model, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_5m_tokens + cache_write_1h_tokens FROM events "
        "WHERE source='claude' AND quality='exact'")}
    branches = {}
    if 'sessions' in tables:
        branches = {r[0]: r[1] or '' for r in conn.execute('SELECT session_id, git_branch FROM sessions')}
    legacy = [r for r in rows if r['session_id'] and r['session_id'] not in rebuilt]
    events = []
    for row in collapse_legacy(legacy, known):
        cwd = row['cwd'] or ''
        events.append(dict(
            source='claude', event_key=f"legacy:{row['id']}", account=row['account'] or 'default',
            session_id=row['session_id'], timestamp=row['timestamp'] or '',
            model=row['model'] or 'unknown', project=project_name_from_cwd(cwd), cwd=cwd,
            git_branch=branches.get(row['session_id'], ''),
            input_tokens=row['input_tokens'] or 0, output_tokens=row['output_tokens'] or 0,
            cache_read_tokens=row['cache_read_tokens'] or 0,
            # The old table has no 5-minute/1-hour split; 5-minute is the cheaper rate.
            cache_write_5m_tokens=row['cache_creation_tokens'] or 0, cache_write_1h_tokens=0,
            reasoning_tokens=0, speed='standard', is_sidechain=0, quality='estimated',
            source_path=''))
    return store.upsert(conn, events)


def _copy(source, destination):
    """Consistent copy through SQLite's backup API (no stray journal files)."""
    destination = Path(destination)
    if destination.exists():
        destination.unlink()
    src = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _backup(source, destination):
    """Byte-for-byte copy of the previous file, unless SQLite has pending journal data."""
    source = Path(source)
    if any(source.with_name(source.name + suffix).exists() for suffix in ('-journal', '-wal')):
        _copy(source, destination)
    else:
        shutil.copy2(source, destination)


def _legacy_counts(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
            for t in LEGACY_TABLES if t in tables}


def _before(conn):
    """Month/source totals as the old tables report them."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'turns' not in tables:
        return {}
    source = 'source' if 'source' in _columns(conn, 'turns') else "'claude'"
    return {(r[0], r[1]): dict(rows=r[2], tokens=r[3]) for r in conn.execute(f'''
        SELECT substr(timestamp,1,7), {source}, COUNT(*),
               SUM(input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens)
        FROM turns GROUP BY 1, 2''')}


def _after(conn):
    result = {}
    for row in queries.grouped(queries.read_events(conn), ('month', 'source')):
        tokens = row['tokens']
        result[(row['month'], row['source'])] = dict(
            rows=row['turns'], tokens=tokens, cost=row['cost'], estimated=row['estimated_events'])
    return result


def print_summary(before, after):
    def cost(value):
        return '       —' if value is None else f'{value:>8.2f}'
    print(f"{'month':8} {'source':7} {'old rows':>9} {'old tokens':>15} | "
          f"{'events':>7} {'tokens':>15} {'cost $':>8}  note")
    totals = dict(old_rows=0, old_tokens=0, rows=0, tokens=0, cost=0.0)
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key, {}), after.get(key, {})
        note = 'estimated' if new.get('estimated') else ''
        if new.get('estimated') and new['estimated'] != new['rows']:
            note = f"{new['estimated']} estimated"
        print(f"{key[0] or '?':8} {key[1]:7} {old.get('rows', 0):>9,} {old.get('tokens', 0) or 0:>15,} | "
              f"{new.get('rows', 0):>7,} {new.get('tokens', 0):>15,} {cost(new.get('cost'))}  {note}")
        totals['old_rows'] += old.get('rows', 0)
        totals['old_tokens'] += old.get('tokens', 0) or 0
        totals['rows'] += new.get('rows', 0)
        totals['tokens'] += new.get('tokens', 0)
        totals['cost'] += new.get('cost') or 0.0
    print(f"{'total':16} {totals['old_rows']:>9,} {totals['old_tokens']:>15,} | "
          f"{totals['rows']:>7,} {totals['tokens']:>15,} {totals['cost']:>8.2f}")


def migrate(db_path=store.DB_PATH, *, dry_run=False, verbose=True,
            projects_dir=None, codex_dir=CODEX_DIR, gemini_dir=GEMINI_DIR):
    """Build the event tables in a copy, verify it, then replace the live file.

    Returns 0 on success. On any failure the live file is left exactly as it was.
    """
    from scanner import scan
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f'Database not found: {db_path}')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup = db_path.with_name(f'{db_path.name}.bak-{stamp}')
    work = db_path.with_name(db_path.name + '.migrating')
    _copy(db_path, work)
    try:
        conn = store.connect(work)
        before, legacy_before = _before(conn), _legacy_counts(conn)
        conn.close()
        scan(projects_dir=projects_dir, db_path=work, verbose=verbose,
             codex_dir=codex_dir, gemini_dir=gemini_dir)
        conn = store.connect(work)
        try:
            with conn:
                imported = import_legacy(conn)
            check = verify(conn, list(discover_files(projects_dir, codex_dir, gemini_dir)),
                           verbose=verbose)
            problems = [f"{len(check['mismatches'])} files do not match their logs"] \
                if check['mismatches'] else []
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                problems.append('integrity check failed')
            if _legacy_counts(conn) != legacy_before:
                problems.append('legacy tables changed')
            after = _after(conn)
        finally:
            conn.close()
        if verbose:
            print(f'Legacy import: {imported} estimated events')
            print_summary(before, after)
        if problems:
            print('Migration aborted, live database untouched: ' + '; '.join(problems))
            return 1
        if dry_run:
            print('Dry run: live database untouched.')
            return 0
        _backup(db_path, backup)
        os.replace(work, db_path)
        print(f'Migrated {db_path}. Backup of the previous file: {backup}')
        return 0
    finally:
        if work.exists():
            work.unlink()
