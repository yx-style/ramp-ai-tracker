'use strict';
const $ = id => document.getElementById(id);
const money = value => '$' + value.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 5});
const day = 86400000;
const preferred = ['A100', 'H100', 'H200', 'B200', 'B300', 'GB200', 'GB300', 'Vera Rubin'];
let data, contracts, visible = [], chosen;
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function link(label, url) {
  const a = node('a', label);
  a.href = url;
  return a;
}
function options(select, values) {
  select.replaceChildren(...values.map(([value, label]) => new Option(label, value)));
}
function models(values) {
  return [...new Set(values)].sort((a, b) =>
    (preferred.includes(a) ? preferred.indexOf(a) : 100) - (preferred.includes(b) ? preferred.indexOf(b) : 100) || a.localeCompare(b));
}
function tableRow(...cells) {
  const tr = node('tr');
  for (const children of cells) {
    const td = node('td');
    td.append(...children);
    tr.append(td);
  }
  return tr;
}
function detail(text) { return node('span', text, 'secondary'); }
function hourlyPrice(s, value) {
  return money(value) + '／GPU 小时' + (s.kind === '按需挂牌起价' ? '起' : '');
}
function latest(s) { return s.points[s.points.length - 1]; }
function label(s) { return [s.source, s.product.split(' · sku-')[0], s.region, s.kind, s.term].join(' · '); }

function draw() {
  const canvas = $('rent-plot'), ctx = canvas.getContext('2d');
  const w = canvas.clientWidth, h = canvas.clientHeight, scale = window.devicePixelRatio || 1;
  canvas.width = w * scale;
  canvas.height = h * scale;
  ctx.scale(scale, scale);
  if (!chosen) return;
  const points = chosen.points.map(([date, price]) => [Date.parse(date), price]);
  const left = 48, right = w - 8, top = 15, bottom = h - 32;
  const start = points[0][0], end = points[points.length - 1][0];
  const values = points.map(p => p[1]);
  const hi = Math.max(...values), lo = Math.min(...values);
  const pad = Math.max(0.05, (hi - lo) / 8, hi * 0.015);
  const low = Math.max(0, lo - pad), high = hi + pad;
  const x = t => start === end ? (left + right) / 2 : left + (t - start) / (end - start) * (right - left);
  const y = v => bottom - (v - low) / (high - low) * (bottom - top);
  ctx.font = '12px Arial, sans-serif';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const v = low + i / 4 * (high - low);
    ctx.fillStyle = '#58585a';
    ctx.fillText('$' + v.toFixed(2), left - 5, y(v) + 4);
    ctx.strokeStyle = '#e8e6e6';
    ctx.beginPath(); ctx.moveTo(left, y(v)); ctx.lineTo(right, y(v)); ctx.stroke();
  }
  ctx.strokeStyle = ctx.fillStyle = '#ab2a25';
  ctx.lineWidth = 2;
  points.forEach(([t, v], i) => {
    const prev = points[i - 1];
    if (prev && t - prev[0] <= day) {
      ctx.beginPath(); ctx.moveTo(x(prev[0]), y(prev[1])); ctx.lineTo(x(t), y(v)); ctx.stroke();
    }
    ctx.beginPath(); ctx.arc(x(t), y(v), points.length > 50 ? 1.6 : 3, 0, 2 * Math.PI); ctx.fill();
  });
  ctx.fillStyle = '#58585a';
  ctx.textAlign = start === end ? 'center' : 'left';
  ctx.fillText(chosen.points[0][0], x(start), h - 10);
  if (end > start) {
    ctx.textAlign = 'right';
    ctx.fillText(latest(chosen)[0], right, h - 10);
  }
}
function selectSeries() {
  chosen = visible[Number($('series').value)];
  const point = latest(chosen);
  $('basis').textContent = chosen.source + ' · ' + chosen.product + ' · ' + point[0] + '　' + hourlyPrice(chosen, point[1]) +
    '。' + point[2];
  $('history').tBodies[0].replaceChildren(...chosen.points.slice().reverse().map(([date, price, note, url, raw]) => {
    const evidence = [link('原文', url), detail(note)];
    if (raw) evidence.push(link('原始快照（gzip）', 'https://raw.githubusercontent.com/yx-style/ramp-ai-tracker/main/' +
      raw.split('/').map(encodeURIComponent).join('/')));
    return tableRow([date], [hourlyPrice(chosen, price)], evidence);
  }));
  draw();
}
function renderQuotes() {
  const model = $('model').value, kind = $('kind').value;
  visible = data.series.filter(s => s.model === model && (!kind || s.kind === kind));
  visible.sort((a, b) => latest(b)[0].localeCompare(latest(a)[0]) ||
    (a.kind === '按需挂牌' ? 0 : 1) - (b.kind === '按需挂牌' ? 0 : 1) || a.source.localeCompare(b.source));
  $('quotes').tBodies[0].replaceChildren(...visible.map((s, index) => {
    const [date, value, , url] = latest(s);
    const btn = node('button', hourlyPrice(s, value));
    btn.type = 'button';
    btn.title = '查看这条报价的走势';
    btn.onclick = () => { $('series').value = index; selectSeries(); $('series').focus(); };
    return tableRow([link(s.source, url), detail(s.product.split(' · sku-')[0])],
      [s.kind + ' · ' + s.term, detail(s.region)], [btn, detail(date)]);
  }));
  const more = $('more-quotes'), rows = [...$('quotes').tBodies[0].rows];
  const showRows = expanded => {
    rows.forEach((r, i) => r.classList.toggle('hidden', !expanded && i >= 8));
    more.hidden = rows.length <= 8;
    more.setAttribute('aria-expanded', String(expanded));
    more.textContent = expanded ? '收起报价' : '查看其余 ' + (rows.length - 8) + ' 条报价';
  };
  showRows(false);
  more.onclick = () => showRows(more.getAttribute('aria-expanded') !== 'true');
  options($('series'), visible.map((s, i) => [i, label(s)]));
  const best = visible.findIndex(s => s.source === 'Nebius' && s.kind === '按需挂牌' && s.points.length > 1);
  $('series').selectedIndex = Math.max(best, 0);
  const index = $('index-citation');
  index.replaceChildren();
  const snapshot = {H100: 2.94, H200: 3.60, B200: 6.35, B300: 7.25};
  if (!kind && model in snapshot) {
    const p = node('p');
    p.append(link('DESK' + model, 'https://www.computedesk.com/indexes/' + model.toLowerCase()),
      '　' + money(snapshot[model]) + '／GPU 小时 · 美国一年期预留指数 · 2026-11-25。来源：Compute Desk／',
      link('General Index', 'https://www.general-index.com/'), '。');
    index.append(p);
  }
  if (!kind && model === 'B300') {
    const p = node('p');
    p.append(link('CCIR 三年期合约', 'https://ccir.io/term'),
      '　$4.66／GPU 小时 · 2026 下半年签约样本的调整后均价，引用 2026-11-26 版。');
    index.append(p);
  }
  const silicon = ['A100', 'H100', 'H200', 'B200', 'B300', 'MI300X'].includes(model);
  const ornn = ['A100', 'H100', 'H200', 'B200', 'B300', 'RTX PRO 6000'].includes(model);
  if (!kind && (silicon || ornn)) {
    const p = node('p', '同型号还可查');
    if (silicon) p.append(' Silicon Data 的 neocloud 指数');
    if (silicon && ornn) p.append('和');
    if (ornn) p.append(' ', link('Ornn 按需成交指数', 'https://data.ornn.com/markets/'));
    p.append('。', link('查看来源与口径', '#sources'));
    index.append(p);
  }
  selectSeries();
}
function selectModel() {
  const kinds = [...new Set(data.series.filter(s => s.model === $('model').value).map(s => s.kind))];
  options($('kind'), [['', '全部口径'], ...kinds.map(k => [k, k])]);
  renderQuotes();
}
function renderContracts() {
  const model = $('deal-model').value;
  $('contracts').tBodies[0].replaceChildren(...contracts.filter(r => !model || r.model === model).map(r => {
    const refs = node('span', null, 'secondary');
    r.links.forEach(([title, url], i) => { if (i) refs.append(' · '); refs.append(link(title, url)); });
    return tableRow([r.date, detail(r.party)], [r.model + ' · ' + r.term, detail('地区：' + r.region)],
      [node('strong', r.price), detail('单位：' + r.unit), detail(r.basis), refs]);
  }));
}
async function read(url) {
  const res = await fetch(url);
  if (!res.ok) throw Error(url + ' HTTP ' + res.status);
  return res.json();
}
read('data/gpu-rent.json').then(prices => {
  data = prices;
  options($('model'), models(data.series.map(s => s.model)).map(m => [m, m]));
  $('model').value = 'B300';
  $('model').onchange = selectModel;
  $('kind').onchange = renderQuotes;
  $('series').onchange = selectSeries;
  const feeds = Object.values(data.status), failed = Object.keys(data.status).filter(k => data.status[k].error);
  $('quote-status').textContent = '最近检查 ' + feeds.map(v => v.checked).sort().pop() + ' UTC' +
    (failed.length ? ' · 以下来源有缺口，详见各行日期：' + failed.join('、') : '');
  selectModel();
  new ResizeObserver(draw).observe($('rent-plot'));
}).catch(err => { $('quote-status').textContent = '报价加载失败：' + err.message; });
read('data/gpu-contracts.json').then(deals => {
  contracts = deals;
  options($('deal-model'), [['', '全部型号'], ...models(deals.map(r => r.model)).map(m => [m, m])]);
  $('deal-model').onchange = renderContracts;
  renderContracts();
  $('deal-model').disabled = false;
  $('contract-count').textContent = ' · ' + deals.length + ' 条合约价格记录';
}).catch(err => {
  $('deal-status').hidden = false;
  $('deal-status').textContent = '合约加载失败：' + err.message;
});
