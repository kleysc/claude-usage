"""Compatibility entry point for fingerprinted, idempotent event ingestion."""
from collectors import claude, codex, gemini
from collectors.common import CODEX_DIR, GEMINI_DIR, discover_accounts, discover_files
from store import DB_PATH, connect, upsert
from reconcile import verify

COLLECTORS = {'claude': claude.collect, 'codex': codex.collect, 'gemini': gemini.collect}


def scan(projects_dir=None, db_path=DB_PATH, verbose=True,
         codex_dir=CODEX_DIR, gemini_dir=GEMINI_DIR):
    conn = connect(db_path, initialize=True)
    result = dict(new=0, updated=0, skipped=0, turns=0, sessions=0)
    read_files, sessions = [], set()
    source_files = dict.fromkeys(COLLECTORS, 0)
    try:
        for source, account, path in discover_files(projects_dir, codex_dir, gemini_dir):
            source_files[source] += 1
            stat = path.stat()
            fingerprint = (stat.st_mtime_ns, stat.st_size)
            old = conn.execute('SELECT mtime,size FROM file_fingerprints WHERE path=?', (str(path),)).fetchone()
            if old is not None and tuple(old) == fingerprint:
                result['skipped'] += 1
                continue
            events = COLLECTORS[source](path)
            for event in events:
                event['account'] = account
                sessions.add(event['session_id'])
            with conn:
                result['turns'] += upsert(conn, events)
                # If the file grows during parsing, this older fingerprint forces a retry.
                conn.execute('INSERT INTO file_fingerprints VALUES (?,?,?) ON CONFLICT(path) '
                             'DO UPDATE SET mtime=excluded.mtime,size=excluded.size', (str(path), *fingerprint))
            result['new' if old is None else 'updated'] += 1
            read_files.append((source, account, path))
        result['sessions'] = len(sessions)
        result['events_added'] = result['turns']
        result['source_files'] = source_files
        if verbose:
            print(f"Scan: {result['new']} new files / {result['updated']} updated / {result['skipped']} skipped / {result['turns']} events added")
            print('Files by source: ' + ', '.join(f'{s}={n}' for s, n in source_files.items()))
        result['reconciliation'] = verify(conn, read_files, verbose=verbose)
        return result
    finally:
        conn.close()


if __name__ == '__main__':
    scan()
