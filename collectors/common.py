"""Shared log discovery and format helpers (no database access)."""
import json
from pathlib import Path

CODEX_DIR = Path.home() / '.codex' / 'sessions'
GEMINI_DIR = Path.home() / '.gemini' / 'tmp'


def discover_accounts():
    home = Path.home()
    accounts = [('default', home / '.claude' / 'projects')]
    accounts.extend((p.name[len('.claude-'):], p / 'projects')
                    for p in sorted(home.glob('.claude-*'))
                    if p.is_dir() and (p / 'projects').exists())
    return accounts


def discover_files(projects_dir=None, codex_dir=CODEX_DIR, gemini_dir=GEMINI_DIR):
    accounts = discover_accounts() if projects_dir is None else [('default', Path(projects_dir))]
    for account, root in accounts:
        for path in sorted(root.glob('**/*.jsonl')):
            yield 'claude', account, path.resolve()
    for source, root, pattern in (
        ('codex', codex_dir, '**/rollout-*.jsonl'),
        ('gemini', gemini_dir, '*/chats/session-*.jsonl'),
    ):
        if root is not None:
            for path in sorted(Path(root).glob(pattern)):
                yield source, source, path.resolve()


def usage_records(path):
    """Ignore malformed/incomplete JSONL lines, retrying them when a file changes."""
    with Path(path).open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def project_name_from_cwd(cwd):
    if not cwd:
        return 'unknown'
    parts = cwd.replace('\\', '/').rstrip('/').split('/')
    return '/'.join(parts[-2:])


def event(source, key, path, session_id, timestamp='', model='unknown', cwd='', branch='', **usage):
    return dict(source=source, event_key=key, account=source, session_id=session_id,
                timestamp=timestamp, model=model, project=project_name_from_cwd(cwd),
                cwd=cwd, git_branch=branch, input_tokens=0, output_tokens=0,
                cache_read_tokens=0, cache_write_5m_tokens=0, cache_write_1h_tokens=0,
                reasoning_tokens=0, speed='standard', is_sidechain=0, quality='exact',
                source_path=str(Path(path).resolve())) | usage
