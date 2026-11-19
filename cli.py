"""Standard-library CLI for event-based usage and API-equivalent costs."""
import argparse
import json
from datetime import date
from pathlib import Path
from store import DB_PATH, connect
import queries


def fmt(n):
    if n >= 1_000_000:
        return f'{n / 1_000_000:.2f}M'
    if n >= 1_000:
        return f'{n / 1_000:.1f}K'
    return str(n)


def fmt_cost(cost):
    return '—' if cost is None else f'${cost:.4f}'


def cost_label(row):
    label = fmt_cost(row['cost'])
    if row['unpriced_tokens']:
        label += f" (excludes {row['unpriced_tokens']:,} unpriced tokens)"
    return label


def get_report(db_path=DB_PATH, **filters):
    if not Path(db_path).exists():
        raise FileNotFoundError('Database not found. Run: python cli.py scan')
    conn = connect(db_path)
    try:
        return queries.report(conn, **filters)
    finally:
        conn.close()


def print_report(report, title):
    total = report['totals']
    print(title)
    print(f"Period: {total['first_timestamp'][:10]} to {total['last_timestamp'][:10]}")
    print(f"Sessions: {total['sessions']:,}  Events: {total['turns']:,}")
    for key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_5m_tokens', 'cache_write_1h_tokens', 'reasoning_tokens'):
        print(f"{key}: {total[key]:,}")
    print(f"Total priced cost: {cost_label(total)}")
    print(f"Estimated events: {total['estimated_events']:,}; cost {fmt_cost(total['estimated_cost'])} ({total['estimated_share']:.2%})")
    print(f"Unverified prices: {fmt_cost(total['unverified_cost'])} ({total['unverified_share']:.2%})")
    print('By source:')
    for row in report['by_source']:
        print(f"  {row['source']}: {row['turns']:,} events; {cost_label(row)}; unverified {row['unverified_share']:.2%}")
    print('By model:')
    for row in report['by_model']:
        print(f"  {row['source']}/{row['model']}: {row['turns']:,} events; {cost_label(row)}")
    print('Top projects:')
    for row in sorted(report['by_project'], key=lambda r: r['cost'] or 0, reverse=True)[:5]:
        print(f"  {row['project']}: {row['sessions']} sessions; {row['turns']:,} events; {cost_label(row)}")


def cmd_scan():
    from scanner import scan
    return scan(db_path=DB_PATH)


def cmd_verify(db_path=DB_PATH):
    from reconcile import verify
    if not Path(db_path).exists():
        raise FileNotFoundError('Database not found. Run: python cli.py scan')
    conn = connect(db_path)
    try:
        return int(bool(verify(conn)['mismatches']))
    finally:
        conn.close()


def cmd_dashboard():
    import threading
    import time
    import webbrowser
    from dashboard import serve
    cmd_scan()
    def open_browser():
        time.sleep(1)
        webbrowser.open('http://localhost:8080')
    threading.Thread(target=open_browser, daemon=True).start()
    serve(port=8080)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    for command in ('scan', 'verify', 'dashboard', 'today', 'stats', 'migrate', 'install-schedule'):
        sub = subparsers.add_parser(command)
        if command == 'migrate':
            sub.add_argument('--dry-run', action='store_true',
                             help='Build and verify the copy, print the summary, change nothing')
        if command in ('today', 'stats'):
            sub.add_argument('--start')
            sub.add_argument('--end')
            sub.add_argument('--source', choices=['claude', 'codex', 'gemini'])
            sub.add_argument('--account')
            sub.add_argument('--models', help='Comma-separated model ids')
            sub.add_argument('--json', action='store_true', help='Machine-readable shared query report')
    args = parser.parse_args(argv)
    if args.command in ('today', 'stats'):
        filters = dict(start=args.start, end=args.end, source=args.source, account=args.account,
                       models=args.models.split(',') if args.models is not None else None)
        if args.command == 'today':
            filters.update(start=date.today().isoformat(), end=date.today().isoformat())
        report = get_report(**filters)
        if args.json:
            print(json.dumps(report, default=str))
        else:
            print_report(report, "Today's usage" if args.command == 'today' else 'AI CLI Usage - Statistics')
        return 0
    if args.command == 'scan':
        return int(bool(cmd_scan()['reconciliation']['mismatches']))
    if args.command == 'verify':
        return cmd_verify()
    if args.command == 'migrate':
        from migrate import migrate
        return migrate(DB_PATH, dry_run=args.dry_run)
    if args.command == 'install-schedule':
        from schedule import install
        return install()
    cmd_dashboard()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
