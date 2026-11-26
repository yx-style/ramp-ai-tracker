'use strict';
const $ = id => document.getElementById(id);
const hour = 3600000, day = 24 * hour;
const money = value => value == null ? '—' : '$' + value.toFixed(2);
const mean = values => values.reduce((a, b) => a + b, 0) / values.length;
const stamp = (time, hourly) => new Date(time).toISOString().slice(0, hourly ? 16 : 10).replace('T', ' ');
const rateStart = Date.parse('2026-10-01');
const styles = {
  fixed: {label: '06.ai 日线（固定挂牌）', color: '#737477', dash: []},
  dynamic: {label: '06.ai 日线（动态现货）', color: '#345e82', dash: [5, 3]},
  official: {label: 'Nebius 官方接口', color: '#ab2a25', dash: []},
  mixed: {label: '06.ai 日线＋Nebius 官方接口', color: '#66557c', dash: [2, 3]},
  rate: {label: '最近核实的按需牌价（10/01 起）', color: '#555', dash: [4, 4]},
};
const regions = new Map();
let data, dynamicStart, plotted = [], hourly, xs = [];

function append(map, key, value) {
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(value);
}

function aggregate(model, region, byHour, range = 'all') {
  const byTime = new Map();
  for (const [name, quotes] of regions.get(model)) {
    if (region && region !== name) continue;
    for (const [t, values] of (byHour ? quotes.hourly : quotes.daily)) {
      if (!byHour && range === 'dynamic' && t < dynamicStart) continue;
      const selected = range === 'official' ? values.filter(p => p.source === 'official') : values;
      if (selected.length) append(byTime, t, {
        v: mean(selected.map(p => p.v)), sources: selected.map(p => p.source),
      });
    }
  }
  return Array.from(byTime, ([t, values]) => {
    const sources = new Set(values.flatMap(p => p.sources));
    return {t, v: mean(values.map(p => p.v)), n: values.length,
      source: sources.size === 1 ? sources.values().next().value : 'mixed'};
  }).sort((a, b) => a.t - b.t);
}

function showDetail(index) {
  const p = plotted[index];
  $('detail').textContent = p
    ? stamp(p.t, hourly) + ' UTC · ' + money(p.v) + '／GPU 小时 · ' + p.n + ' 个地区 · ' + styles[p.source].label
    : '此范围暂无完整报价。';
}

function renderLegend() {
  const active = new Set(plotted.map(p => p.source));
  if (data.onDemand.usd[$('model').value] != null && plotted.some(p => p.t >= rateStart)) active.add('rate');
  const entries = Object.entries(styles).filter(([key]) => active.has(key)).map(([, style]) => {
    const span = document.createElement('span'), swatch = document.createElement('i');
    swatch.style.color = style.color;
    if (style.dash.length) swatch.className = 'dash';
    span.append(swatch, style.label);
    return span;
  });
  $('nebius-legend').replaceChildren(...entries);
}

function draw() {
  const canvas = $('nebius-plot'), ctx = canvas.getContext('2d');
  const w = canvas.clientWidth, h = canvas.clientHeight, scale = window.devicePixelRatio || 1;
  canvas.width = w * scale;
  canvas.height = h * scale;
  ctx.scale(scale, scale);
  xs = [];
  if (!plotted.length) return;
  const start = plotted[0].t, end = plotted[plotted.length - 1].t;
  const markers = [];
  if (!hourly) {
    if (start <= dynamicStart && dynamicStart <= end) markers.push([dynamicStart, '10/8 动态定价']);
    const official = plotted.find(p => p.source === 'official' || p.source === 'mixed');
    if (official) {
      const date = new Date(official.t);
      const label = plotted.some(p => p.source === 'mixed') ? ' 开始纳入官方' : ' 官方归档';
      markers.push([official.t, (date.getUTCMonth() + 1) + '/' + date.getUTCDate() + label]);
    }
  }
  const left = 46, right = w - 10, bottom = h - 32, top = markers.length ? 22 + markers.length * 15 : 14;
  const rate = end >= rateStart ? data.onDemand.usd[$('model').value] : null;
  const max = Math.ceil(plotted.reduce((a, p) => Math.max(a, p.v), rate || 1) * 1.08);
  const y = v => bottom - v / max * (bottom - top);
  const x = t => start === end ? (left + right) / 2 : left + (t - start) / (end - start) * (right - left);
  xs = plotted.map(p => x(p.t));
  ctx.font = '12px Arial, sans-serif';
  ctx.fillStyle = '#58585a';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const v = max * i / 4;
    ctx.fillText('$' + v.toFixed(2), left - 6, y(v) + 4);
    ctx.strokeStyle = '#e8e6e6';
    ctx.beginPath(); ctx.moveTo(left, y(v)); ctx.lineTo(right, y(v)); ctx.stroke();
  }
  markers.forEach(([t, label], i) => {
    const at = x(t), labelY = 15 + i * 15;
    ctx.font = '11px Arial, sans-serif';
    ctx.textAlign = at + 5 + ctx.measureText(label).width <= right ? 'left' : 'right';
    ctx.fillText(label, at + (ctx.textAlign === 'left' ? 5 : -5), labelY);
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = '#a09a99';
    ctx.beginPath(); ctx.moveTo(at, labelY + 3); ctx.lineTo(at, bottom); ctx.stroke();
  });
  if (rate != null) {
    ctx.setLineDash(styles.rate.dash); ctx.strokeStyle = styles.rate.color;
    const at = x(Math.max(start, rateStart));
    const short = start === end || at === right;
    ctx.beginPath();
    ctx.moveTo(short ? at - 12 : at, y(rate));
    ctx.lineTo(short ? Math.min(right, at + 12) : right, y(rate));
    ctx.stroke();
  }
  ctx.lineWidth = hourly ? 1.4 : 2;
  for (const source of new Set(plotted.map(p => p.source))) {
    const {color, dash} = styles[source];
    ctx.strokeStyle = color;
    ctx.setLineDash(dash);
    ctx.beginPath();
    plotted.forEach((p, i) => {
      if (p.source !== source) return;
      const prev = plotted[i-1];
      if (prev?.source === source && p.t - prev.t === (hourly ? hour : day)) ctx.lineTo(xs[i], y(p.v));
      else ctx.moveTo(xs[i], y(p.v));
    });
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color;
    plotted.forEach((p, i) => {
      if (p.source !== source) return;
      if (hourly && (p.t - plotted[i - 1]?.t === hour || plotted[i + 1]?.t - p.t === hour)) return;
      ctx.beginPath(); ctx.arc(xs[i], y(p.v), 2.4, 0, Math.PI * 2); ctx.fill();
    });
  }
  const ticks = Math.min(plotted.length, w < 440 ? 3 : 6);
  ctx.font = '12px Arial, sans-serif';
  ctx.fillStyle = '#58585a';
  for (let i = 0; i < ticks; i++) {
    const k = Math.round((plotted.length - 1) * i / Math.max(1, ticks - 1));
    ctx.textAlign = ticks === 1 ? 'center' : !i ? 'left' : i === ticks - 1 ? 'right' : 'center';
    ctx.fillText(stamp(plotted[k].t, false).slice(5), xs[k], bottom + 22);
  }
}

function render() {
  hourly = $('grain').value === 'hour';
  $('range').disabled = hourly;
  $('hour-note').hidden = !hourly;
  plotted = aggregate($('model').value, $('region').value, hourly, hourly ? 'official' : $('range').value);
  renderLegend();
  $('values').replaceChildren();
  if (document.querySelector('#charts details').open) fillTable();
  showDetail(plotted.length - 1);
  draw();
}

function fillTable() {
  const frag = document.createDocumentFragment();
  for (const p of [...plotted].reverse()) {
    const row = document.createElement('tr');
    for (const text of [stamp(p.t, hourly), p.v.toFixed(4), p.n, styles[p.source].label]) {
      row.insertCell().textContent = text;
    }
    frag.append(row);
  }
  $('values').replaceChildren(frag);
}

function chooseModel() {
  $('region').replaceChildren(new Option('跨地区等权均价', ''));
  for (const name of regions.get($('model').value).keys()) $('region').add(new Option(name, name));
  render();
}

function regionQuotes({model, region}) {
  if (!regions.has(model)) regions.set(model, new Map());
  const byRegion = regions.get(model);
  if (!byRegion.has(region)) byRegion.set(region, {hourly: new Map(), daily: new Map()});
  return byRegion.get(region);
}

async function init() {
  const res = await fetch('data/nebius.json');
  if (!res.ok) throw new Error('HTTP ' + res.status);
  data = await res.json();
  dynamicStart = Date.parse(data.dailyBackfill?.dynamicStart || '2026-10-08');
  let first = Infinity, last = -Infinity, count = 0;
  for (const s of data.series) {
    if (!s.points.length) continue;
    const quotes = regionQuotes(s);
    for (const [end, value] of s.points) {
      const t = Date.parse(end) - hour;
      append(quotes.hourly, t, {v: value, source: 'official'});
      first = Math.min(first, t); last = Math.max(last, t + hour); count++;
    }
  }
  const bySku = new Map((data.dailyBackfill?.series || []).map(s => [s.skuId, s]));
  for (const s of data.series) bySku.set(s.skuId, s);
  for (const s of bySku.values()) {
    for (const [date, value] of data.dailyBackfill?.points?.[s.skuId] || []) {
      const t = Date.parse(date);
      append(regionQuotes(s).daily, t, {v: value, source: t < dynamicStart ? 'fixed' : 'dynamic'});
    }
  }
  let dayFirst = Infinity, dayLast = -Infinity, backfillFirst = Infinity, backfillLast = -Infinity;
  for (const byRegion of regions.values()) {
    for (const quotes of byRegion.values()) {
      const days = new Map();
      for (const [t, values] of quotes.hourly) {
        append(days, Math.floor(t / day) * day, mean(values.map(p => p.v)));
      }
      for (const [t, values] of days) {
        if (values.length === 24) quotes.daily.set(t, [{v: mean(values), source: 'official'}]);
      }
      for (const [t, values] of quotes.daily) {
        dayFirst = Math.min(dayFirst, t); dayLast = Math.max(dayLast, t);
        if (values[0].source !== 'official') {
          backfillFirst = Math.min(backfillFirst, t); backfillLast = Math.max(backfillLast, t);
        }
      }
    }
  }
  const dateRange = (a, b) => stamp(a, false) + ' 至 ' + stamp(b, false);
  const dayStatus = Number.isFinite(dayFirst)
    ? '日线 ' + dateRange(dayFirst, dayLast) +
      (Number.isFinite(backfillFirst) ? '（06.ai 补齐 ' + dateRange(backfillFirst, backfillLast) + '）' : '')
    : '暂无完整日线';
  const hourStatus = count ? '官方小时归档 ' + stamp(first, true) + ' 至 ' + stamp(last, true) +
    ' UTC · ' + count.toLocaleString() + ' 条' : '暂无官方小时归档';
  $('status').textContent = dayStatus + ' · ' + hourStatus;
  const gaps = (data.gaps || []).filter(gap => bySku.has(gap.skuId));
  const revised = (data.revisions || []).length;
  $('coverage').textContent = (gaps.length ? '已存序列有 ' + gaps.length + ' 段缺报。'
    : count ? '各序列在各自覆盖期内均无缺报。' : '暂无官方小时归档。') +
    '累计记录 ' + revised + ' 条小时报价修订。';
  for (const gap of gaps) {
    const s = bySku.get(gap.skuId), li = document.createElement('li');
    li.textContent = s.model + ' · ' + s.region + ' · ' + stamp(Date.parse(gap.start), true) +
      ' 至 ' + stamp(Date.parse(gap.end), true) + ' UTC，缺 ' + gap.hours + ' 小时。';
    if (gap.outsideRetentionHours) li.textContent += '其中 ' + gap.outsideRetentionHours + ' 小时已超出接口保留期。';
    $('gap-list').append(li);
  }
  $('gap-list').hidden = !gaps.length;
  if (!regions.size) {
    for (const id of ['model', 'region', 'grain', 'range']) $(id).disabled = true;
    $('detail').textContent = '暂无现货报价可显示。';
    return;
  }
  if (count) $('hour-note').textContent += ' 官方小时归档自 ' + stamp(first, true) + ' UTC 起。';
  const models = [...new Set(['B300', 'B200', 'H200', 'H100', ...regions.keys()])].filter(m => regions.has(m));
  for (const model of models) {
    $('model').add(new Option(model, model));
    const daily = aggregate(model, '', false), p = daily[daily.length - 1];
    const rate = data.onDemand.usd[model], el = document.createElement('article');
    el.className = 'kpi';
    const lines = [['k', model + ' · 现货日均'], ['v', money(p?.v)], ['n', '按需 ' + money(rate) +
      '　现货／按需 ' + (p && rate ? (p.v / rate * 100).toFixed(1) + '%' : '—')],
      ['n', p ? stamp(p.t, false) + ' · ' + p.n + ' 个地区' : '尚无完整日']];
    const info = data.onDemand.byModel?.[model];
    const checked = info ? info.checked : data.onDemand.checked;
    if (checked) lines.push(['n', '牌价核实于 ' + checked]);
    if (info?.status === 'unverified') lines.push(['n', info.attempted + ' 未核实：' + info.reason]);
    if (info?.lastChange) {
      const change = info.lastChange;
      lines.push(['n', change.observed + ' 核实调价：' + money(change.from) + ' → ' + money(change.to)]);
    }
    for (const [cls, text] of lines) {
      const line = document.createElement('div');
      line.className = cls; line.textContent = text; el.append(line);
    }
    $('kpis').append(el);
  }
  $('model').onchange = chooseModel;
  $('region').onchange = $('grain').onchange = $('range').onchange = render;
  document.querySelector('#charts details').ontoggle = event => { if (event.target.open) fillTable(); };
  $('nebius-plot').onpointermove = e => {
    if (xs.length && plotted.length) {
      const index = xs.reduce((best, x, i) => Math.abs(x-e.offsetX) < Math.abs(xs[best]-e.offsetX) ? i : best, 0);
      showDetail(index);
    }
  };
  const initialModel = $('model').value;
  if (aggregate(initialModel, '', false).length && !aggregate(initialModel, '', false, 'dynamic').length) {
    $('range').value = 'all';
  }
  chooseModel();
  new ResizeObserver(draw).observe($('nebius-plot'));
}
init().catch(error => { $('status').textContent = '报价读取失败：' + error.message; console.error(error); });
