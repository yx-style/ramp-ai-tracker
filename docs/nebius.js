'use strict';
const $ = id => document.getElementById(id);
const hour = 3600000, day = 24 * hour;
const money = value => value == null ? '—' : '$' + value.toFixed(2);
const mean = values => values.reduce((a, b) => a + b, 0) / values.length;
const stamp = (time, hourly) => new Date(time).toISOString().slice(0, hourly ? 16 : 10).replace('T', ' ');
const regions = new Map();
let data, plotted = [], hourly, xs = [];

function append(map, key, value) {
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(value);
}

function aggregate(model, region, byHour) {
  const byTime = new Map();
  for (const [name, times] of regions.get(model)) {
    if (region && region !== name) continue;
    const days = new Map();
    for (const [t, values] of times) {
      const value = mean(values);
      if (byHour) append(byTime, t, value);
      else append(days, Math.floor(t / day) * day, value);
    }
    for (const [t, values] of days) {
      if (values.length === 24) append(byTime, t, mean(values));
    }
  }
  return Array.from(byTime, ([t, values]) => ({t, v: mean(values), n: values.length})).sort((a, b) => a.t - b.t);
}

function showDetail(index) {
  const p = plotted[index];
  $('detail').textContent = p
    ? stamp(p.t, hourly) + ' UTC · ' + money(p.v) + '／GPU 小时 · ' + p.n + ' 个地区'
    : '此范围暂无完整报价。';
}

function draw() {
  const canvas = $('nebius-plot'), ctx = canvas.getContext('2d');
  const w = canvas.clientWidth, h = canvas.clientHeight, scale = window.devicePixelRatio || 1;
  canvas.width = w * scale;
  canvas.height = h * scale;
  ctx.scale(scale, scale);
  if (!plotted.length) return;
  const left = 42, right = w - 10, bottom = h - 32, top = 14;
  const rate = data.onDemand.usd[$('model').value];
  const max = Math.ceil(plotted.reduce((a, p) => Math.max(a, p.v), rate || 1) * 1.08);
  const y = v => bottom - v / max * (bottom - top);
  const start = plotted[0].t, span = plotted[plotted.length - 1].t - start || 1;
  xs = plotted.map(p => left + (p.t - start) / span * (right - left));
  ctx.font = '12px Arial, sans-serif';
  ctx.fillStyle = '#58585a';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const v = max * i / 4;
    ctx.fillText('$' + v.toFixed(2), left - 6, y(v) + 4);
    ctx.strokeStyle = '#e8e6e6';
    ctx.beginPath(); ctx.moveTo(left, y(v)); ctx.lineTo(right, y(v)); ctx.stroke();
  }
  if (rate != null) {
    ctx.setLineDash([5, 4]); ctx.strokeStyle = '#555';
    ctx.beginPath(); ctx.moveTo(left, y(rate)); ctx.lineTo(right, y(rate)); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.strokeStyle = '#ab2a25'; ctx.lineWidth = hourly ? 1.4 : 2;
  ctx.beginPath();
  plotted.forEach((p, i) => {
    if (!i || p.t - plotted[i-1].t > (hourly ? hour : day)) ctx.moveTo(xs[i], y(p.v));
    else ctx.lineTo(xs[i], y(p.v));
  });
  ctx.stroke();
  if (!hourly || plotted.length === 1) {
    ctx.fillStyle = '#ab2a25';
    plotted.forEach((p, i) => {
      ctx.beginPath(); ctx.arc(xs[i], y(p.v), 2.4, 0, Math.PI * 2); ctx.fill();
    });
  }
  const ticks = Math.min(plotted.length, w < 440 ? 3 : 6);
  ctx.fillStyle = '#58585a';
  for (let i = 0; i < ticks; i++) {
    const k = Math.round((plotted.length - 1) * i / Math.max(1, ticks - 1));
    ctx.textAlign = !i ? 'left' : i === ticks - 1 ? 'right' : 'center';
    ctx.fillText(stamp(plotted[k].t, false).slice(5), xs[k], bottom + 22);
  }
}

function render() {
  hourly = $('grain').value === 'hour';
  plotted = aggregate($('model').value, $('region').value, hourly);
  $('values').replaceChildren();
  if (document.querySelector('#charts details').open) fillTable();
  showDetail(plotted.length - 1);
  draw();
}

function fillTable() {
  const frag = document.createDocumentFragment();
  for (const p of [...plotted].reverse()) {
    const row = document.createElement('tr');
    for (const text of [stamp(p.t, hourly), p.v.toFixed(4), p.n]) row.insertCell().textContent = text;
    frag.append(row);
  }
  $('values').replaceChildren(frag);
}

function chooseModel() {
  $('region').replaceChildren(new Option('跨地区等权均价', ''));
  for (const name of regions.get($('model').value).keys()) $('region').add(new Option(name, name));
  render();
}

async function init() {
  const res = await fetch('data/nebius.json');
  if (!res.ok) throw new Error('HTTP ' + res.status);
  data = await res.json();
  let first = Infinity, last = 0, count = 0;
  for (const s of data.series) {
    if (!regions.has(s.model)) regions.set(s.model, new Map());
    const model = regions.get(s.model);
    if (!model.has(s.region)) model.set(s.region, new Map());
    for (const [end, value] of s.points) {
      const t = Date.parse(end) - hour;
      append(model.get(s.region), t, value);
      first = Math.min(first, t); last = Math.max(last, t + hour); count++;
    }
  }
  $('status').textContent = stamp(first, true) + ' 至 ' + stamp(last, true) + ' UTC · ' +
    count.toLocaleString() + ' 条小时报价';
  const gaps = data.gaps || [];
  const revised = (data.revisions || []).length;
  $('coverage').textContent = (gaps.length ? '已存序列有 ' + gaps.length + ' 段缺报。'
    : '各序列在各自覆盖期内均无缺报。') + '累计记录 ' + revised + ' 条小时报价修订。';
  const bySku = new Map(data.series.map(s => [s.skuId, s]));
  for (const gap of gaps) {
    const s = bySku.get(gap.skuId), li = document.createElement('li');
    li.textContent = s.model + ' · ' + s.region + ' · ' + stamp(Date.parse(gap.start), true) +
      ' 至 ' + stamp(Date.parse(gap.end), true) + ' UTC，缺 ' + gap.hours + ' 小时。';
    if (gap.outsideRetentionHours) li.textContent += '其中 ' + gap.outsideRetentionHours + ' 小时已超出接口保留期。';
    $('gap-list').append(li);
  }
  $('gap-list').hidden = !gaps.length;
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
  $('region').onchange = $('grain').onchange = render;
  document.querySelector('#charts details').ontoggle = event => { if (event.target.open) fillTable(); };
  $('nebius-plot').onpointermove = e => {
    if (xs.length && plotted.length) {
      const index = xs.reduce((best, x, i) => Math.abs(x-e.offsetX) < Math.abs(xs[best]-e.offsetX) ? i : best, 0);
      showDetail(index);
    }
  };
  chooseModel();
  new ResizeObserver(draw).observe($('nebius-plot'));
}
init().catch(error => { $('status').textContent = '报价读取失败：' + error.message; console.error(error); });
