// The server sends every number already filtered, aggregated and priced.
// This file keeps the filter state, asks for data and draws it.

const TOOLS = { claude: 'Claude', codex: 'Codex', gemini: 'Gemini' };
const RANGES = [
  { id: '7d', label: '7D', days: 7 },
  { id: '30d', label: '30D', days: 30 },
  { id: '90d', label: '90D', days: 90 },
  { id: 'month', label: 'Month' },
  { id: 'all', label: 'All' },
];
const PARTS = [
  ['cache_read', 'Cache reads', 'context re-read on every response'],
  ['cache_write_1h', 'Cache writes · 1 hour', 'context stored for an hour'],
  ['cache_write_5m', 'Cache writes · 5 min', 'context stored for five minutes'],
  ['output', 'Output', 'text and reasoning produced'],
  ['input', 'Input', 'new, uncached prompt tokens'],
];
const PAGE = 15;

const query = new URLSearchParams(location.search);
const state = {
  range: RANGES.some(r => r.id === query.get('range')) ? query.get('range') : '30d',
  day: /^\d{4}-\d\d-\d\d$/.test(query.get('day') || '') ? query.get('day') : '', // one day, set by clicking a bar
  tool: query.get('tool') || '',
  account: query.get('account') || '',
  project: query.get('project') || '',
  models: query.get('models') ? query.get('models').split(',') : null, // null = all
  sort: 'cost',
  shown: PAGE,
};
let data = null;
let requestId = 0;

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function money(value, digits) {
  if (value === null || value === undefined) return '—';
  if (value > 0 && value < 0.01) return '<$0.01';
  const d = digits ?? (value < 100 ? 2 : 0);
  return '$' + value.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
const tokens = n => n >= 1e9 ? (n / 1e9).toFixed(2) + 'B' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'K' : String(n || 0);
const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;
const duration = m => !m ? '—' : m < 60 ? Math.round(m) + ' min' : m < 1440 ? (m / 60).toFixed(1) + ' h' : (m / 1440).toFixed(1) + ' d';
const isoDay = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const parseDay = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const shortDay = s => parseDay(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
const percent = (part, whole) => !whole || part == null ? '' : part / whole < 0.01 && part > 0 ? '<1%' : (part / whole * 100).toFixed(0) + '%';

function change(now, previous) {
  if (previous === null || previous === undefined) return '';
  if (!previous) return now ? 'NEW' : '';
  const pct = ((now || 0) - previous) / previous * 100;
  const size = Math.abs(pct);
  return (pct >= 0 ? '▲ ' : '▼ ') + (size >= 1000 ? Math.round(size / 100) + '×' : size.toFixed(0) + '%');
}

function spark(values) {
  if (!values || values.length < 2) return '<span></span>';
  const w = 96, h = 24, max = Math.max(...values, 1e-9);
  const line = values.map((v, i) => `${i ? 'L' : 'M'}${(i / (values.length - 1) * w).toFixed(1)} ${(h - 2 - v / max * (h - 4)).toFixed(1)}`).join('');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path class="area" d="${line}L${w} ${h}L0 ${h}Z"/><path d="${line}"/></svg>`;
}

// ---------- state, URL and loading ----------

function rangeDates() {
  if (state.day) return { start: state.day, end: state.day };
  const today = new Date();
  const range = RANGES.find(r => r.id === state.range);
  if (range.days) {
    const start = new Date(today);
    start.setDate(start.getDate() - range.days + 1);
    return { start: isoDay(start), end: isoDay(today) };
  }
  if (range.id === 'month') return { start: isoDay(new Date(today.getFullYear(), today.getMonth(), 1)), end: isoDay(today) };
  return {};
}

function syncUrl() {
  const q = new URLSearchParams();
  if (state.range !== '30d') q.set('range', state.range);
  for (const key of ['day', 'tool', 'account', 'project']) if (state[key]) q.set(key, state[key]);
  if (state.models) q.set('models', state.models.join(','));
  history.replaceState(null, '', q.toString() ? '?' + q : location.pathname);
}

async function load() {
  const id = ++requestId;
  syncUrl();
  const q = new URLSearchParams(rangeDates());
  if (state.tool) q.set('source', state.tool);
  if (state.account) q.set('account', state.account);
  if (state.project) q.set('project', state.project);
  if (state.models) q.set('models', state.models.join(','));
  $('content').classList.add('loading');
  try {
    const body = await (await fetch('/api/data?' + q)).json();
    if (id !== requestId) return; // a newer request is on its way
    if (body.error) return notice(body.error);
    data = body;
    $('notice').hidden = true;
    render();
  } catch (error) {
    if (id === requestId) notice('Could not load data: ' + error.message);
  } finally {
    if (id === requestId) $('content').classList.remove('loading');
  }
}

function set(changes) {
  Object.assign(state, changes, { shown: PAGE });
  load();
}

function notice(text) {
  $('notice').textContent = text;
  $('notice').hidden = false;
}

// ---------- filters ----------

function segButton(label, on, onClick, tool) {
  const button = document.createElement('button');
  button.type = 'button';
  if (on) button.className = 'on';
  button.innerHTML = (tool ? `<span class="dot" style="background:var(--${esc(tool)})"></span>` : '') + esc(label);
  button.addEventListener('click', onClick);
  return button;
}

function renderFilters() {
  $('range-seg').replaceChildren(...RANGES.map(r =>
    segButton(r.label, !state.day && state.range === r.id, () => set({ range: r.id, day: '' }))));

  $('tool-seg').replaceChildren(
    segButton('All', !state.tool, () => set({ tool: '', account: '' })),
    ...data.options.sources.map(s => segButton(TOOLS[s] || s, state.tool === s,
      () => set({ tool: s, account: s === 'claude' ? state.account : '' }), s)));

  // Only Claude has several accounts.
  const accounts = data.options.accounts.filter(([source]) => source === 'claude').map(([, account]) => account);
  $('account-group').hidden = accounts.length < 2 || (state.tool && state.tool !== 'claude');
  $('account-seg').replaceChildren(
    segButton('All', !state.account, () => set({ account: '' })),
    ...accounts.map(a => segButton(a, state.account === a, () => set({ account: a, tool: 'claude' }))));

  const models = data.options.models.filter(([source]) => !state.tool || source === state.tool);
  const selected = state.models ? new Set(state.models) : null;
  let html = '', group = '';
  for (const [source, model] of models) {
    if (source !== group) { group = source; html += `<div class="eyebrow model-group">${esc(TOOLS[source] || source)}</div>`; }
    html += `<label class="model-option"><input type="checkbox" value="${esc(model)}" ${!selected || selected.has(model) ? 'checked' : ''}>${esc(model)}</label>`;
  }
  $('model-list').innerHTML = html;
  $('model-summary').textContent = !selected ? 'All models' : selected.size === 1 ? [...selected][0] : `${selected.size} models`;

  // Filters set by clicking on the page are listed so they can be removed.
  const active = [];
  if (state.day) active.push(['Day: ' + shortDay(state.day), { day: '' }]);
  if (state.project) active.push(['Project: ' + state.project, { project: '' }]);
  if (state.models) active.push([state.models.length === 1 ? 'Model: ' + state.models[0] : `${state.models.length} models`, { models: null }]);
  $('active').hidden = !active.length;
  $('active').replaceChildren(
    Object.assign(document.createElement('span'), { className: 'eyebrow', textContent: 'Filtered by' }),
    ...active.map(([label, reset]) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'pill';
      button.textContent = label + '  ✕';
      button.addEventListener('click', () => set(reset));
      return button;
    }));
}

// ---------- headline ----------

function renderHero() {
  const t = data.totals, r = data.range;
  const period = state.day ? shortDay(state.day)
    : state.range === 'all' ? 'all time' : state.range === 'month' ? 'this month' : `last ${r.days} days`;
  $('h-eyebrow').textContent = `01 — Spend · ${period}` + (r.start && !state.day ? ` · ${shortDay(r.start)} – ${shortDay(r.end)}` : '');

  const cost = t.cost;
  if (cost === null) $('h-number').textContent = '—';
  else if (cost >= 1000) $('h-number').innerHTML = '<sup>$</sup>' + Math.round(cost).toLocaleString('en-US');
  else {
    const [whole, cents] = cost.toFixed(2).split('.');
    $('h-number').innerHTML = `<sup>$</sup>${whole}<span class="cents">.${cents}</span>`;
  }

  const prev = data.previous;
  const span = r.days === 1 ? 'day' : `${r.days} days`;
  if (prev && prev.cost) $('h-delta').innerHTML = `<b>${change(cost, prev.cost)}</b> vs the previous ${span} (${money(prev.cost)})`;
  else if (prev) $('h-delta').textContent = `No usage in the previous ${span}.`;
  else $('h-delta').textContent = t.turns ? '' : 'No usage for this selection.';

  const includes = [];
  if (t.estimated_cost) includes.push(`${money(t.estimated_cost)} estimated from ${t.estimated_events.toLocaleString()} old responses whose transcripts are gone`);
  if (t.unverified_cost) includes.push(`${money(t.unverified_cost)} at prices not confirmed with the vendor`);
  let note = 'API-equivalent value, not what you were billed.';
  if (includes.length) note += ' Includes ' + includes.join(' and ') + '.';
  const unpriced = (t.excluded || []).filter(x => x.tokens);
  if (unpriced.length) note += ` ${tokens(t.unpriced_tokens)} tokens have no price and are left out (${unpriced.map(x => x.model).join(', ')}).`;
  $('h-note').textContent = note;

  const m = data.month, cache = t.tokens ? t.cache_read_tokens / t.tokens : 0;
  const kpis = [
    ['Daily average', money(data.daily_average), r.days ? `over ${plural(r.days, 'day')}` : '', null],
    ['Month to date', money(m.cost), `day ${m.day} of ${m.days}`, m.day / m.days],
    ['Projected month', money(m.projected), m.last_month_cost ? `last month ${money(m.last_month_cost)}` : 'at the current pace',
      m.last_month_cost ? Math.min(m.projected / m.last_month_cost, 1) : null],
    ['Tokens', tokens(t.tokens), `${(cache * 100).toFixed(0)}% read from cache`, cache],
    ['Sessions', t.sessions.toLocaleString(), t.sessions ? `${money((cost || 0) / t.sessions)} each` : '', null],
  ];
  $('kpis').innerHTML = kpis.map(([label, value, sub, meter]) => `
    <div class="kpi"><span class="eyebrow">${label}</span>
      <div><div class="kpi-value display">${esc(value)}</div><div class="kpi-sub">${esc(sub) || '&nbsp;'}</div>
        <div class="meter" style="${meter === null ? 'visibility:hidden' : ''}"><i style="width:${((meter || 0) * 100).toFixed(0)}%"></i></div></div>
    </div>`).join('');

  $('updated').textContent = 'Updated ' + data.last_scan.slice(5);
  $('foot-right').textContent = r.start ? `${r.start} → ${r.end}` : '';
}

// ---------- daily chart ----------

function renderChart() {
  const svg = $('chart'), days = data.days;
  const sources = data.options.sources.filter(s => days.some(d => d[s]));
  $('legend').innerHTML = sources.length > 1
    ? sources.map(s => `<span><span class="dot" style="background:var(--${esc(s)})"></span>${esc(TOOLS[s] || s)}</span>`).join('') : '';
  const totals = days.map(d => sources.reduce((sum, s) => sum + (d[s] || 0), 0));

  $('daily-table').innerHTML = `<thead><tr><th>Day</th>${sources.map(s => `<th class="num">${esc(TOOLS[s] || s)}</th>`).join('')}<th class="num">Total</th></tr></thead><tbody>` +
    days.map((d, i) => `<tr><td>${d.day}</td>${sources.map(s => `<td class="num">${money(d[s] || 0, 2)}</td>`).join('')}<td class="num">${money(totals[i], 2)}</td></tr>`).reverse().join('') + '</tbody>';

  const showAverage = days.length > 1 && data.daily_average > 0;
  // The average is labelled in a right-hand margin so it never sits on top of a bar.
  const W = svg.clientWidth, H = svg.clientHeight, L = 48, R = showAverage ? 64 : 8, T = 16, B = 26;
  if (!days.length || !W) { svg.innerHTML = ''; return; }
  const peak = Math.max(...totals, 0);
  const rough = (peak || 1) / 4, magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map(k => k * magnitude).find(s => s >= rough);
  const max = Math.max(Math.ceil(peak / step) * step, step);
  const y = v => T + (H - T - B) * (1 - v / max);
  const slot = (W - L - R) / days.length, bar = Math.max(Math.min(slot * 0.72, 44), 1);
  const gap = bar > 6 ? 2 : 0; // surface gap between stacked segments
  let out = '';
  for (let v = 0; v <= max + 1e-9; v += step) {
    out += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 8}" y="${y(v) + 3.5}" text-anchor="end">${money(v, step < 1 ? 2 : 0)}</text>`;
  }
  const every = Math.ceil(days.length / Math.max(3, Math.floor((W - L) / 90)));
  days.forEach((d, i) => {
    const x = L + slot * i + (slot - bar) / 2;
    out += `<rect class="hit" data-i="${i}" x="${L + slot * i}" y="${T}" width="${slot}" height="${H - T - B}"/>`;
    let base = 0;
    sources.forEach(s => {
      const v = d[s] || 0;
      if (!v) return;
      const top = y(base + v), height = Math.max(y(base) - top - (base ? gap : 0), 1);
      out += `<rect x="${x}" y="${top}" width="${bar}" height="${height}" fill="var(--${s})" pointer-events="none"/>`;
      base += v;
    });
    if (i % every === 0) out += `<text x="${L + slot * i + slot / 2}" y="${H - 8}" text-anchor="middle">${esc(shortDay(d.day).toUpperCase())}</text>`;
  });
  if (showAverage) {
    const avg = y(data.daily_average);
    out += `<line class="avg" x1="${L}" x2="${W - R}" y1="${avg}" y2="${avg}"/><text x="${W - R + 8}" y="${avg - 3}" style="fill:var(--fg-2)">AVG</text><text x="${W - R + 8}" y="${avg + 10}" style="fill:var(--fg-2)">${money(data.daily_average)}</text>`;
  }
  if (peak) {
    const at = totals.indexOf(peak);
    out += `<text x="${L + slot * at + slot / 2}" y="${y(peak) - 6}" text-anchor="middle" class="label" style="fill:var(--fg)">${money(peak, 0)}</text>`;
  }
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.innerHTML = out;

  const tip = $('tip');
  svg.onmousemove = event => {
    const i = event.target.dataset ? event.target.dataset.i : undefined;
    if (i === undefined) { tip.style.display = 'none'; return; }
    const d = days[i];
    tip.innerHTML = `<div class="t">${parseDay(d.day).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}</div>` +
      sources.filter(s => d[s]).map(s => `<div><span><span class="dot" style="background:var(--${s})"></span>${TOOLS[s] || s}</span><b>${money(d[s], 2)}</b></div>`).join('') +
      `<div><span>Total</span><b>${money(totals[i], 2)}</b></div>` + (days.length > 1 ? '<div class="t" style="margin-top:4px">Click to open this day</div>' : '');
    tip.style.display = 'block';
    tip.style.left = Math.min(event.clientX + 14, innerWidth - 200) + 'px';
    tip.style.top = (event.clientY + 14) + 'px';
  };
  svg.onmouseleave = () => { tip.style.display = 'none'; };
  svg.onclick = event => {
    const i = event.target.dataset ? event.target.dataset.i : undefined;
    if (i !== undefined && days.length > 1) { tip.style.display = 'none'; set({ day: days[i].day }); }
  };
}

// ---------- ranked lists (click a row to filter the page by it) ----------

function rows(id, items, { trend = true, empty = 'Nothing in this selection.' } = {}) {
  const total = data.totals.cost || 0;
  const max = Math.max(...items.map(r => r.cost || 0), 0);
  const node = $(id);
  if (!items.length) { node.innerHTML = `<div class="empty">${esc(empty)}</div>`; return; }
  node.replaceChildren(...items.map(r => {
    const el = document.createElement(r.onClick ? 'button' : 'div');
    if (r.onClick) { el.type = 'button'; el.addEventListener('click', r.onClick); el.title = r.selected ? 'Remove this filter' : 'Filter the page by this'; }
    el.className = 'item' + (trend ? '' : ' wide') + (r.selected ? ' selected' : '');
    const track = trend ? '' : `<div class="track"><i style="width:${max ? ((r.cost || 0) / max * 100).toFixed(1) : 0}%"></i></div>`;
    el.innerHTML = `<div class="name"><b>${esc(r.label)}</b>${r.note ? `<small>${esc(r.note)}</small>` : ''}${track}</div>` +
      (trend ? spark(r.series) : '') +
      `<div class="amount">${money(r.cost)}</div>` +
      `<div class="chg">${trend && data.previous ? change(r.cost, r.previous_cost) : percent(r.cost, total)}</div>`;
    return el;
  }));
}

function renderLists() {
  const total = data.totals.cost || 0;
  $('tools').replaceChildren(...data.by_source.map(r => {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'tool-row' + (state.tool === r.source ? ' selected' : '');
    el.title = state.tool === r.source ? 'Remove this filter' : 'Filter the page by this tool';
    el.addEventListener('click', () => set(state.tool === r.source ? { tool: '', account: '' } : { tool: r.source, account: '' }));
    el.innerHTML = `<div class="tool-top"><span><span class="dot" style="background:var(--${esc(r.source)})"></span><b>${esc(TOOLS[r.source] || r.source)}</b> <span class="muted" style="font-size:12px">${tokens(r.tokens)} tokens · ${plural(r.sessions, 'session')}</span></span>` +
      `<span><span class="big display">${money(r.cost)}</span> <span class="chg">${percent(r.cost, total)}</span></span></div>` +
      `<div class="track"><i style="width:${total ? ((r.cost || 0) / total * 100).toFixed(1) : 0}%;background:var(--${esc(r.source)})"></i></div>`;
    return el;
  }));
  if (!data.by_source.length) $('tools').innerHTML = '<div class="empty">Nothing in this selection.</div>';

  rows('accounts-list', data.by_account.map(r => {
    const selected = r.source === 'claude' ? state.account === r.account : state.tool === r.source;
    return {
      label: r.source === 'claude' ? `Claude · ${r.account}` : (TOOLS[r.source] || r.source),
      note: plural(r.sessions, 'session'), cost: r.cost, selected,
      onClick: () => set(selected ? { account: '', tool: r.source === 'claude' ? state.tool : '' }
        : { tool: r.source, account: r.source === 'claude' ? r.account : '' }),
    };
  }), { trend: false });

  const hint = data.previous ? 'trend · vs previous period' : 'trend';
  $('projects-hint').textContent = hint;
  $('models-hint').textContent = hint;
  const projects = data.by_project.map(r => ({
    label: r.project, note: plural(r.sessions, 'session'), cost: r.cost, series: r.series, previous_cost: r.previous_cost,
    selected: state.project === r.project,
    onClick: () => set({ project: state.project === r.project ? '' : r.project }),
  }));
  if (data.project_count > data.by_project.length) {
    projects.push({ label: `${data.project_count - data.by_project.length} other projects`, cost: data.other_projects_cost, series: null, previous_cost: null });
  }
  rows('projects', projects);

  rows('models', data.by_model.filter(r => r.tokens).map(r => {
    const selected = !!state.models && state.models.length === 1 && state.models[0] === r.model;
    return {
      label: r.model, note: tokens(r.tokens) + ' tokens' + (r.cost === null ? ' · no price' : ''), cost: r.cost,
      series: r.series, previous_cost: r.previous_cost, selected,
      onClick: () => set({ models: selected ? null : [r.model] }),
    };
  }));

  const parts = data.totals.cost_parts;
  rows('parts', PARTS.map(([key, label, note]) => ({ label, note, cost: parts[key] })).filter(r => r.cost > 0).sort((a, b) => b.cost - a.cost), { trend: false });
  const cache = parts.cache_read + parts.cache_write_5m + parts.cache_write_1h;
  $('parts-hint').textContent = total ? `${(cache / total * 100).toFixed(0)}% is prompt caching` : '';
}

// ---------- sessions ----------

function renderSessions() {
  $('sort-seg').replaceChildren(
    segButton('By cost', state.sort === 'cost', () => { state.sort = 'cost'; state.shown = PAGE; renderSessions(); }),
    segButton('Recent', state.sort === 'recent', () => { state.sort = 'recent'; state.shown = PAGE; renderSessions(); }));
  const all = [...data.sessions].sort(state.sort === 'cost'
    ? (a, b) => (b.cost || 0) - (a.cost || 0)
    : (a, b) => b.last_timestamp.localeCompare(a.last_timestamp));
  $('sessions-title').textContent = `07 — Sessions · ${all.length.toLocaleString()}`;
  const top = Math.max(...all.map(s => s.cost || 0), 0);
  $('sessions').innerHTML = all.length ? all.slice(0, state.shown).map(s => `
    <tr data-session="${esc(s.session_id)}" data-project="${esc(s.project)}">
      <td class="proj"><b>${esc(s.project)}</b><small>${esc(s.git_branch || s.session_short)}</small>${s.quality !== 'exact' ? '<span class="tag" title="Built from old rows whose transcript no longer exists">estimated</span>' : ''}</td>
      <td><span class="dot" style="background:var(--${esc(s.source)})"></span>${esc(TOOLS[s.source] || s.source)}${s.source === 'claude' ? ` <span class="muted">· ${esc(s.account)}</span>` : ''}</td>
      <td class="hide-narrow">${esc(s.model)}${s.model_count > 1 ? ` <span class="muted">+${s.model_count - 1}</span>` : ''}</td>
      <td class="hide-narrow">${s.last ? esc(shortDay(s.last)) : '—'}</td>
      <td class="num hide-narrow">${duration(s.duration_min)}</td>
      <td class="num">${s.turns.toLocaleString()}</td>
      <td class="num">${tokens(s.tokens)}</td>
      <td class="hide-narrow"><span class="share"><i style="width:${top ? ((s.cost || 0) / top * 100).toFixed(0) : 0}%"></i></span></td>
      <td class="num cost">${money(s.cost, 2)}</td>
    </tr>`).join('') : '<tr><td colspan="9" class="empty">No sessions in this selection.</td></tr>';
  $('session-more').hidden = all.length <= state.shown;
  $('session-more').textContent = `Show more · ${all.length - state.shown} left`;
}

async function openSession(id, project) {
  $('dialog-title').textContent = project;
  $('dialog-sub').textContent = id;
  $('dialog-body').innerHTML = '<p class="empty">Loading…</p>';
  $('session-dialog').showModal();
  try {
    const detail = await (await fetch('/api/session?session_id=' + encodeURIComponent(id))).json();
    if (detail.error) { $('dialog-body').innerHTML = `<p class="empty">${esc(detail.error)}</p>`; return; }
    const u = detail.usage;
    $('dialog-sub').textContent = `${id.slice(0, 8)} · ${detail.summary.message_count} messages · ${tokens(u.tokens)} tokens · ${money(u.cost, 2)}`;
    $('dialog-body').innerHTML = detail.messages.map(m => `
      <div class="msg ${esc(m.role)}">
        <div class="eyebrow msg-meta">${esc(m.role)}${m.model ? ' · ' + esc(m.model) : ''}${m.timestamp ? ' · ' + esc(m.timestamp.slice(0, 19).replace('T', ' ')) : ''}</div>
        <pre>${esc(m.content.length > 6000 ? m.content.slice(0, 6000) + '\n… (truncated)' : m.content)}</pre>
      </div>`).join('');
  } catch (error) {
    $('dialog-body').innerHTML = `<p class="empty">Could not load the session: ${esc(error.message)}</p>`;
  }
}

// ---------- wiring ----------

function render() {
  renderFilters();
  renderHero();
  renderChart();
  renderLists();
  renderSessions();
}

function theme(name) {
  document.documentElement.dataset.theme = name;
  $('theme-seg').querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.theme === name));
}

function savedTheme() {
  try { return localStorage.getItem('usage-theme'); } catch { return null; }
}

async function rescan() {
  const button = $('rescan');
  button.disabled = true;
  button.textContent = 'Scanning…';
  try {
    const result = await (await fetch('/api/scan', { method: 'POST' })).json();
    if (result.error) notice('Scan failed: ' + result.error);
    else if (result.mismatches) notice(`Scan finished, but ${result.mismatches} files do not match their logs. Run: python cli.py verify`);
  } catch (error) {
    notice('Scan failed: ' + error.message);
  }
  button.disabled = false;
  button.textContent = 'Scan now';
  load();
}

document.addEventListener('DOMContentLoaded', () => {
  theme(savedTheme() || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  $('theme-seg').addEventListener('click', event => {
    const name = event.target.dataset.theme;
    if (!name) return;
    theme(name);
    try { localStorage.setItem('usage-theme', name); } catch { /* the choice just won't be remembered */ }
  });
  $('rescan').addEventListener('click', rescan);
  $('model-list').addEventListener('change', () => {
    const boxes = [...$('model-list').querySelectorAll('input')];
    const checked = boxes.filter(b => b.checked).map(b => b.value);
    if (checked.length) set({ models: checked.length === boxes.length ? null : checked });
  });
  $('models-all').addEventListener('click', () => set({ models: null }));
  $('models-none').addEventListener('click', () => $('model-list').querySelectorAll('input').forEach(b => { b.checked = false; }));
  $('session-more').addEventListener('click', () => { state.shown += PAGE * 2; renderSessions(); });
  $('sessions').addEventListener('click', event => {
    const row = event.target.closest('tr[data-session]');
    if (row) openSession(row.dataset.session, row.dataset.project);
  });
  $('dialog-close').addEventListener('click', () => $('session-dialog').close());
  $('session-dialog').addEventListener('click', event => { if (event.target === $('session-dialog')) $('session-dialog').close(); });
  document.addEventListener('click', event => { if (!$('model-filter').contains(event.target)) $('model-filter').open = false; });
  addEventListener('resize', () => data && renderChart());
  load();
  setInterval(() => { if (!$('session-dialog').open && !$('model-filter').open) load(); }, 60000);
});
