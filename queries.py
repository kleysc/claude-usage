"""Shared event queries and Python-only costs for CLI and dashboard."""
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta

from pricing import load_prices, lookup, price_parts
from store import TOKEN_COLUMNS

BILLED_COLUMNS = tuple(c for c in TOKEN_COLUMNS if c != 'reasoning_tokens')
COST_PARTS = ('input', 'output', 'cache_read', 'cache_write_5m', 'cache_write_1h')


def local_day(timestamp):
    """Local calendar date of an ISO timestamp ('' when it cannot be parsed)."""
    try:
        return datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone().date().isoformat()
    except (AttributeError, ValueError):
        return (timestamp or '')[:10]


def read_events(conn, *, start=None, end=None, source=None, account=None, models=None, project=None):
    """Events for the filters. `start`/`end` are inclusive local dates (YYYY-MM-DD)."""
    clauses, args = [], []
    for field, value in [('source', source), ('account', account), ('project', project)]:
        if value:
            clauses.append(field + '=?')
            args.append(value)
    if models is not None:
        clauses.append('model IN (' + ','.join('?' for _ in models) + ')')
        args.extend(models)
    sql = 'SELECT * FROM events' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
    events = []
    for row in conn.execute(sql + ' ORDER BY timestamp, source, event_key', args):
        event = dict(row)
        event['day'] = local_day(event['timestamp'])
        if (start and event['day'] < start) or (end and event['day'] > end):
            continue
        events.append(event)
    return events


def annotate(events, prices=None):
    """Price every event once; later groupings reuse the result."""
    prices = load_prices() if prices is None else prices
    entries = {}
    for e in events:
        if '_parts' in e:
            continue
        key = (e.get('model'), (e.get('timestamp') or '')[:10])
        if key not in entries:
            entries[key] = lookup(key[0], key[1], prices)
        entry = entries[key]
        e['_parts'] = price_parts(e, entry)
        e['_verified'] = bool(entry and entry.get('verified'))
    return events


def aggregate(events, prices=None):
    annotate(events, prices)
    result = {column: 0 for column in TOKEN_COLUMNS}
    parts = dict.fromkeys(COST_PARTS, 0.0)
    cost = None
    estimated_cost = unverified_cost = 0.0
    unpriced = defaultdict(int)
    qualities, sessions = set(), set()
    estimated_events = 0
    first = last = ''
    for e in events:
        for column in TOKEN_COLUMNS:
            result[column] += e[column]
        qualities.add(e['quality'])
        sessions.add((e['source'], e['session_id']))
        estimated_events += e['quality'] == 'estimated'
        stamp = e['timestamp']
        if stamp:
            first = stamp if not first or stamp < first else first
            last = stamp if stamp > last else last
        if e['_parts'] is None:
            unpriced[e['model']] += sum(e[c] for c in BILLED_COLUMNS)
            continue
        value = sum(e['_parts'].values())
        cost = (cost or 0.0) + value
        for name in COST_PARTS:
            parts[name] += e['_parts'][name]
        if e['quality'] == 'estimated':
            estimated_cost += value
        if not e['_verified']:
            unverified_cost += value
    result.update(cost=cost, priced_cost=cost or 0.0, cost_parts=parts,
                  unpriced_tokens=sum(unpriced.values()),
                  estimated_cost=estimated_cost, unverified_cost=unverified_cost,
                  estimated_share=estimated_cost / cost if cost else 0.0,
                  unverified_share=unverified_cost / cost if cost else 0.0,
                  quality=next(iter(qualities)) if len(qualities) == 1 else ('mixed' if qualities else 'exact'),
                  estimated_events=estimated_events, turns=len(events), sessions=len(sessions),
                  tokens=sum(result[c] for c in BILLED_COLUMNS),
                  first_timestamp=first, last_timestamp=last,
                  excluded=[dict(model=m, tokens=t) for m, t in sorted(unpriced.items())])
    result.update(input=result['input_tokens'], output=result['output_tokens'],
                  cache_read=result['cache_read_tokens'],
                  cache_creation=result['cache_write_5m_tokens'] + result['cache_write_1h_tokens'])
    return result


def _field(event, name):
    if name == 'day':
        return event.get('day') or event['timestamp'][:10]
    if name == 'month':
        return (event.get('day') or event['timestamp'])[:7]
    return event[name]


def grouped(events, fields, prices=None):
    annotate(events, prices)
    groups = defaultdict(list)
    for e in events:
        groups[tuple(_field(e, f) for f in fields)].append(e)
    return [dict(zip(fields, key)) | aggregate(rows) for key, rows in sorted(groups.items())]


def totals(conn, **filters):
    return aggregate(read_events(conn, **filters))


def daily(conn, **filters):
    return grouped(read_events(conn, **filters), ('day', 'model', 'source', 'account'))


def by_model(conn, **filters):
    return grouped(read_events(conn, **filters), ('source', 'model'))


def by_project(conn, **filters):
    return grouped(read_events(conn, **filters), ('project',))


def by_session(conn, **filters):
    return grouped(read_events(conn, **filters), ('source', 'account', 'session_id'))


def by_source_account(conn, **filters):
    return grouped(read_events(conn, **filters), ('source', 'account'))


def _duration_minutes(first, last):
    try:
        return round((datetime.fromisoformat(last.replace('Z', '+00:00')) -
                      datetime.fromisoformat(first.replace('Z', '+00:00'))).total_seconds() / 60, 1)
    except ValueError:
        return 0


def session_rows(events):
    """One row per session, with the model that cost the most."""
    per_session = defaultdict(list)
    for e in events:
        per_session[(e['source'], e['account'], e['session_id'])].append(e)
    rows = []
    for (source, account, session_id), items in per_session.items():
        row = aggregate(items)
        by_model_cost = defaultdict(float)
        for e in items:
            by_model_cost[e['model']] += sum(e['_parts'].values()) if e['_parts'] else 0.0
        models = sorted(by_model_cost, key=lambda m: (-by_model_cost[m], m))
        last = items[-1]
        row.update(source=source, account=account, session_id=session_id,
                   session_short=session_id.split(':')[-1][:8], project=last['project'],
                   git_branch=last['git_branch'], model=models[0], model_count=len(models),
                   last=local_day(row['last_timestamp']), last_date=local_day(row['last_timestamp']),
                   duration_min=_duration_minutes(row['first_timestamp'], row['last_timestamp']))
        rows.append(row)
    return rows


def report(conn, **filters):
    """Totals and breakdowns for the CLI."""
    events = annotate(read_events(conn, **filters))
    return dict(totals=aggregate(events),
                by_model=grouped(events, ('source', 'model')),
                by_project=grouped(events, ('project',)),
                by_source=grouped(events, ('source',)),
                by_source_account=grouped(events, ('source', 'account')),
                by_month=grouped(events, ('month', 'source')))


def _slim(row):
    """Drop fields the page does not use."""
    return {k: v for k, v in row.items() if k not in ('input', 'output', 'cache_read', 'cache_creation',
                                                      'priced_cost', 'first_timestamp', 'reasoning_tokens')}


def _cost(event):
    return sum(event['_parts'].values()) if event['_parts'] else 0.0


def _buckets(values, limit=60):
    """Trend line values; long periods are summed into at most `limit` points."""
    size = -(-len(values) // limit) if len(values) > limit else 1
    return [round(sum(values[i:i + size]), 4) for i in range(0, len(values), size)]


def overview(conn, *, start=None, end=None, source=None, account=None, models=None,
             project=None, today=None):
    """Everything the dashboard shows for one selection, computed on the server."""
    today = today or date.today()
    everything = annotate(read_events(conn, source=source, account=account, models=models,
                                      project=project))
    current = [e for e in everything
               if not (start and e['day'] < start) and not (end and e['day'] > end)]
    days = sorted({e['day'] for e in current if e['day']})
    range_start = start or (days[0] if days else today.isoformat())
    range_end = min(end or today.isoformat(), today.isoformat())
    span = (date.fromisoformat(range_end) - date.fromisoformat(range_start)).days + 1 if days or start else 0
    span = max(span, 0)

    previous, previous_events = None, []
    if start and span > 0:
        prev_end = date.fromisoformat(start) - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span - 1)
        previous_events = [e for e in everything
                           if prev_start.isoformat() <= e['day'] <= prev_end.isoformat()]
        previous = dict(start=prev_start.isoformat(), end=prev_end.isoformat(),
                        cost=aggregate(previous_events)['cost'], events=len(previous_events))

    month_prefix = today.isoformat()[:7]
    month_days = calendar.monthrange(today.year, today.month)[1]
    month_cost = aggregate([e for e in everything if e['day'][:7] == month_prefix])['cost'] or 0.0
    last_month_end = today.replace(day=1) - timedelta(days=1)
    last_month_cost = aggregate([e for e in everything
                                 if e['day'][:7] == last_month_end.isoformat()[:7]])['cost']
    month = dict(label=month_prefix, cost=month_cost, day=today.day, days=month_days,
                 projected=month_cost / today.day * month_days,
                 last_month=last_month_end.isoformat()[:7], last_month_cost=last_month_cost)

    # Every day of the period gets an entry, so days without usage show as gaps.
    day_list = [(date.fromisoformat(range_start) + timedelta(days=i)).isoformat() for i in range(span)]
    per_day = defaultdict(lambda: defaultdict(float))
    for e in current:
        per_day[e['day']][e['source']] += _cost(e)

    def with_trend(rows, key):
        """Add each row's trend over the period and its cost in the previous period."""
        now, before = defaultdict(lambda: defaultdict(float)), defaultdict(float)
        for e in current:
            now[key(e)][e['day']] += _cost(e)
        for e in previous_events:
            before[key(e)] += _cost(e)
        out = []
        for row in rows:
            row = _slim(row)
            row['series'] = _buckets([now[key(row)].get(d, 0.0) for d in day_list])
            row['previous_cost'] = before.get(key(row), 0.0) if previous else None
            out.append(row)
        return out

    total = aggregate(current)
    by_cost = lambda r: -(r['cost'] or 0)
    projects = sorted(grouped(current, ('project',)), key=by_cost)
    all_events = conn.execute('SELECT DISTINCT source, account, model FROM events').fetchall()
    return dict(
        range=dict(start=range_start if span else None, end=range_end if span else None,
                   days=span, bounded=bool(start)),
        totals=_slim(total),
        daily_average=(total['cost'] or 0.0) / span if span else 0.0,
        previous=previous, month=month,
        days=[dict(day=d, **{k: round(v, 4) for k, v in per_day.get(d, {}).items()}) for d in day_list],
        by_source=with_trend(sorted(grouped(current, ('source',)), key=by_cost), lambda r: r['source']),
        by_account=with_trend(sorted(grouped(current, ('source', 'account')), key=by_cost),
                              lambda r: (r['source'], r['account'])),
        by_model=with_trend(sorted(grouped(current, ('source', 'model')), key=by_cost),
                            lambda r: (r['source'], r['model'])),
        by_project=with_trend(projects[:12], lambda r: r['project']),
        project_count=len(projects),
        other_projects_cost=sum(r['cost'] or 0 for r in projects[12:]),
        sessions=[_slim(r) for r in sorted(session_rows(current), key=by_cost)],
        options=dict(sources=sorted({r[0] for r in all_events}),
                     accounts=sorted({(r[0], r[1]) for r in all_events}),
                     models=sorted({(r[0], r[2]) for r in all_events})))
