(async function () {
  const D = await fetch("data/tracker.json", { cache: "no-cache" }).then(r => r.json());
  const css = getComputedStyle(document.documentElement);
  const v = name => css.getPropertyValue(name).trim();

  const W = D.weeks, L = W.length - 1;
  const GROUPS = {
    anthropic: { label: "Anthropic", color: v("--s-anthropic") },
    openai: { label: "OpenAI", color: v("--s-openai") },
    google: { label: "Google", color: v("--s-google") },
    xai: { label: "xAI", color: v("--s-xai") },
    other: { label: "其他", color: v("--s-other") },
  };
  const FAM_COLORS = { opus: "#7E1B16", fable: "#C6423B", sonnet: "#E3A19C", haiku: "#F2D9D7", mythos: "#1A1A1A", other: "#C8C8CB" };

  // ---------- data helpers ----------
  const zeros = () => W.map(() => 0);
  const add = (a, b) => a.map((x, i) => x + b[i]);
  const sum = (a, i0, i1) => { let s = 0; for (let i = Math.max(0, i0); i <= i1; i++) s += a[i] || 0; return s; };
  const roll = (a, n = 4) => a.map((_, i) => sum(a, i - n + 1, i));

  const groupSeries = metric => {
    const out = {};
    for (const g of D.groups) out[g] = zeros();
    for (const [k, m] of Object.entries(D.makers)) {
      const g = GROUPS[k] ? k : "other";
      out[g] = add(out[g], m[metric]);
    }
    return out;
  };
  const SP = groupSeries("spend"), VO = groupSeries("volume");
  const TOT_S = Object.values(SP).reduce(add, zeros()), TOT_V = Object.values(VO).reduce(add, zeros());
  const share = (s, t) => s.map((x, i) => (t[i] ? (x / t[i]) * 100 : null));

  // ---------- formatting ----------
  const pct = (x, d = 1) => (x == null || !isFinite(x) ? "—" : x.toFixed(d) + "%");
  const chg = (x, d = 1) => (x == null || !isFinite(x) ? "—" : (x >= 0 ? "+" : "−") + Math.abs(x * 100).toFixed(d) + "%");
  const pts = (x, d = 1) => (x == null || !isFinite(x) ? "—" : (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(d) + " 个百分点");
  const ptsShort = (x, d = 1) => (x == null || !isFinite(x) ? "—" : +Math.abs(x).toFixed(d) === 0 ? (0).toFixed(d) : (x > 0 ? "+" : "−") + Math.abs(x).toFixed(d));
  const verb = (x, d = 1) => (x >= 0 ? "增加 " : "下降 ") + Math.abs(x * 100).toFixed(d) + "%";
  const cls = x => (x > 0 ? "up" : x < 0 ? "down" : "");
  const dLong = s => { const [y, m, d] = s.split("-").map(Number); return `${y}年${m}月${d}日`; };
  const dShort = s => { const [, m, d] = s.split("-").map(Number); return `${m}月${d}日`; };
  const wkRange = i => `${D.week_start[i]} 至 ${W[i]}`;

  // ---------- headline numbers ----------
  const a4 = [L - 3, L], p4 = [L - 7, L - 4];
  const s4 = (arr, [i0, i1]) => sum(arr, i0, i1);
  const aSpendShare = share(SP.anthropic, TOT_S), aVolShare = share(VO.anthropic, TOT_V);
  const oSpendShare = share(SP.openai, TOT_S);
  const gSpend = s4(SP.anthropic, a4) / s4(SP.anthropic, p4) - 1;
  const gVol = s4(VO.anthropic, a4) / s4(VO.anthropic, p4) - 1;
  const unit = (s, vv, r) => s4(s, r) / s4(vv, r);
  const gUnit = unit(SP.anthropic, VO.anthropic, a4) / unit(SP.anthropic, VO.anthropic, p4) - 1;
  const aoRatio = unit(SP.anthropic, VO.anthropic, a4) / unit(SP.openai, VO.openai, a4);
  const totSpendG = s4(TOT_S, a4) / s4(TOT_S, p4) - 1;
  const yoy = L - 52 >= 0 ? L - 52 : null;

  document.getElementById("dek").innerHTML =
    `截至 ${dLong(W[L])} 那周，Anthropic 拿走了 Ramp 企业客户 token 支出的 <b>${pct(aSpendShare[L])}</b>，四周前是 ${pct(aSpendShare[L - 4])}；` +
    `OpenAI 是 ${pct(oSpendShare[L])}。最近四周 Anthropic 的 token 用量比前四周<b>${verb(gVol)}</b>，支出<b>${verb(gSpend)}</b>，` +
    `每个 token 的支出${verb(gUnit)}。同期所有厂商合计的 token 支出${verb(totSpendG)}。`;
  document.getElementById("meta").textContent =
    `数据截至 ${W[L]}（${wkRange(L)} 那周）· 本站构建于 ${D.built_at.replace("T", " ").replace("+00:00", " UTC")}`;

  const kpis = [
    { k: "Anthropic 支出份额，最近一周", v: pct(aSpendShare[L]), n: `四周前 ${pct(aSpendShare[L - 4])}，${pts(aSpendShare[L] - aSpendShare[L - 4])}` + (yoy != null ? `；一年前 ${pct(aSpendShare[yoy])}` : ""), hl: true },
    { k: "Anthropic 用量份额，最近一周", v: pct(aVolShare[L]), n: `四周前 ${pct(aVolShare[L - 4])}，${pts(aVolShare[L] - aVolShare[L - 4])}` },
    { k: "Anthropic 相对 OpenAI 的单位 token 支出", v: aoRatio.toFixed(2) + "×", n: "最近四周。1.00 表示每个 token 花的钱一样多" },
    { k: "Anthropic token 支出，近四周比前四周", v: chg(gSpend), n: `最近一周比四周前 ${chg(SP.anthropic[L] / SP.anthropic[L - 4] - 1)}` },
    { k: "Anthropic token 用量，近四周比前四周", v: chg(gVol), n: `最近一周比四周前 ${chg(VO.anthropic[L] / VO.anthropic[L - 4] - 1)}` },
    { k: "Anthropic 单位 token 支出，近四周比前四周", v: chg(gUnit), n: `最近一周比四周前 ${chg((SP.anthropic[L] / VO.anthropic[L]) / (SP.anthropic[L - 4] / VO.anthropic[L - 4]) - 1)}` },
  ];
  document.getElementById("kpis").innerHTML = kpis.map(x =>
    `<div class="kpi${x.hl ? " hl" : ""}"><div class="k">${x.k}</div><div class="v">${x.v}</div><div class="n">${x.n}</div></div>`).join("");

  // ---------- legends ----------
  const legendHTML = (items, box) => items.map(it =>
    `<span><i class="${box ? "box" : ""}${it.dash ? " dash" : ""}" style="${box ? "background" : "border-top-color"}:${it.color}"></i>${it.label}</span>`).join("");
  const groupItems = D.groups.map(g => ({ label: GROUPS[g].label, color: GROUPS[g].color }));
  document.querySelectorAll('[data-legend="groups"]').forEach(el => (el.innerHTML = legendHTML(groupItems)));

  // ---------- share bars ----------
  document.getElementById("split-note").textContent =
    `${D.week_start[L - 3]} 至 ${W[L]}，四周合计。Cursor、DeepSeek、Moonshot、Zhipu 合并为「其他」。`;
  const barRow = (label, S, T) => {
    const segs = D.groups.map(g => ({ g, p: (s4(S[g], a4) / s4(T, a4)) * 100 }));
    return `<div class="row"><span>${label}</span><div class="track">` + segs.map(s =>
      `<div class="seg-b${s.g === "other" ? " darkt" : ""}" style="flex:${s.p} 1 0;background:${GROUPS[s.g].color}" title="${GROUPS[s.g].label} ${pct(s.p)}">${s.p >= 6 ? pct(s.p, 0) : ""}</div>`).join("") + `</div></div>`;
  };
  document.getElementById("bars").innerHTML = barRow("支出", SP, TOT_S) + barRow("用量", VO, TOT_V);
  document.getElementById("bars-legend").innerHTML = legendHTML(groupItems, true);

  // ---------- SVG chart engine ----------
  const NS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs = {}, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, val] of Object.entries(attrs)) e.setAttribute(k, val);
    if (parent) parent.appendChild(e);
    return e;
  };
  const niceTicks = (lo, hi, n = 5) => {
    const span = hi - lo || 1, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || mag * 10;
    const out = []; for (let t = Math.ceil(lo / step) * step; t <= hi + 1e-9; t += step) out.push(+t.toFixed(10));
    return out;
  };
  const logTicks = (lo, hi) => {
    const out = [], mult = Math.log10(hi / lo) > 2.5 ? [1] : [1, 2, 5];
    for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++)
      for (const m of mult) { const t = m * Math.pow(10, e); if (t >= lo * 0.999 && t <= hi * 1.001) out.push(t); }
    return out;
  };
  let RANGE = "all";
  const rangeStart = () => (RANGE === "all" ? 0 : Math.max(0, W.length - Number(RANGE)));

  function lineChart(id, opt) {
    const box = document.getElementById(id);
    box.innerHTML = "";
    const width = box.clientWidth, height = opt.height || (width < 560 ? 240 : 300);
    const m = { t: 10, r: opt.endLabels === false ? 12 : width < 560 ? 88 : 104, b: 26, l: 40 };
    const i0 = rangeStart(), idx = []; for (let i = i0; i <= L; i++) idx.push(i);
    const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img", "aria-label": opt.aria || "" }, box);
    const vals = opt.series.flatMap(s => idx.map(i => s.values[i])).filter(x => x != null && isFinite(x) && (!opt.log || x > 0));
    let lo = opt.yMin != null ? opt.yMin : Math.min(...vals), hi = Math.max(...vals);
    if (opt.stack) { lo = 0; hi = 100; }
    let ticks, y;
    const ih = height - m.t - m.b, iw = width - m.l - m.r;
    if (opt.log) {
      lo = Math.max(lo, hi / 1e3);
      const a = Math.log10(lo * 0.9), b = Math.log10(hi * 1.1);
      y = val => m.t + ih - ((Math.log10(val) - a) / (b - a)) * ih;
      ticks = logTicks(lo * 0.9, hi * 1.1);
    } else {
      if (opt.yMin == null && !opt.stack) { const pad = (hi - lo) * 0.08; lo = Math.max(0, lo - pad); hi += pad; } else if (!opt.stack) hi *= 1.06;
      ticks = niceTicks(lo, hi);
      lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
      y = val => m.t + ih - ((val - lo) / (hi - lo)) * ih;
    }
    const x = i => m.l + (idx.length < 2 ? 0 : ((i - i0) / (idx.length - 1)) * iw);
    const floor = opt.log ? lo * 0.9 : -Infinity;
    const ok = val => val != null && isFinite(val) && val >= floor && (!opt.log || val > 0);

    // grid + y ticks
    for (const t of ticks) {
      el("line", { class: "grid", x1: m.l, x2: m.l + iw, y1: y(t), y2: y(t) }, svg);
      const lab = el("text", { x: m.l - 6, y: y(t) + 4, "text-anchor": "end" }, svg);
      lab.textContent = opt.yFmt ? opt.yFmt(t) : t;
    }
    // x ticks: quarter starts, year label on January
    el("line", { class: "axis", x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih }, svg);
    let lastMonth = null;
    for (const i of idx) {
      const [yy, mm] = D.week_start[i].split("-").map(Number);
      const key = yy * 12 + mm;
      if (key === lastMonth) continue;
      lastMonth = key;
      // keep roughly one label per 55px so narrow screens don't collide
      const months = idx.length / 4.35, slots = Math.max(2, Math.floor(iw / 55));
      const every = [1, 2, 3, 6, 12].find(e => months / e <= slots) || 12;
      if ((mm - 1) % every !== 0) continue;
      const t = el("text", { x: x(i), y: m.t + ih + 17, "text-anchor": "middle" }, svg);
      t.textContent = mm === 1 ? String(yy) : `${mm}月`;
    }

    // stacked areas
    if (opt.stack) {
      let base = idx.map(() => 0);
      for (const s of opt.series) {
        const top = idx.map((i, k) => base[k] + (s.values[i] || 0));
        const d = idx.map((i, k) => `${k ? "L" : "M"}${x(i).toFixed(1)},${y(top[k]).toFixed(1)}`).join("") +
          idx.slice().reverse().map((i, k) => `L${x(i).toFixed(1)},${y(base[idx.length - 1 - k]).toFixed(1)}`).join("") + "Z";
        el("path", { d, fill: s.color, stroke: "#fff", "stroke-width": 1 }, svg);
        base = top;
      }
    } else {
      for (const s of opt.series) {
        let d = "", pen = false;
        for (const i of idx) {
          const val = s.values[i];
          if (!ok(val)) { pen = false; continue; }
          d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(val).toFixed(1)}`; pen = true;
        }
        el("path", { d, fill: "none", stroke: s.color, "stroke-width": s.width || 2, "stroke-linejoin": "round", "stroke-linecap": "round", "stroke-dasharray": s.dash ? "5 4" : "none" }, svg);
      }
    }

    // direct end labels, nudged apart
    if (opt.endLabels !== false) {
      const labs = opt.series.map(s => {
        let yy;
        if (opt.stack) return null;
        const val = s.values[L];
        if (!ok(val)) return null;
        yy = y(val);
        return { s, y: yy, val };
      }).filter(Boolean).sort((a, b) => a.y - b.y);
      for (let k = 1; k < labs.length; k++) if (labs[k].y - labs[k - 1].y < 14) labs[k].y = labs[k - 1].y + 14;
      for (const lb of labs) {
        const t = el("text", { class: "endlab", x: m.l + iw + 8, y: lb.y + 4 }, svg);
        const n1 = el("tspan", {}, t); n1.textContent = (lb.s.short || lb.s.label) + " ";
        const n2 = el("tspan", { class: "val" }, t); n2.textContent = opt.tipFmt(lb.val);
      }
    }

    // hover: crosshair + tooltip
    const tip = document.createElement("div"); tip.className = "tip hidden"; box.appendChild(tip);
    const xh = el("line", { class: "xhair hidden", y1: m.t, y2: m.t + ih }, svg);
    const dots = opt.stack ? [] : opt.series.map(s => el("circle", { r: 4, fill: s.color, stroke: "#fff", "stroke-width": 2, class: "hidden" }, svg));
    const hit = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" }, svg);
    const move = ev => {
      const r = svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * width;
      const k = Math.round(((px - m.l) / iw) * (idx.length - 1));
      const i = idx[Math.max(0, Math.min(idx.length - 1, k))];
      xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.classList.remove("hidden");
      opt.series.forEach((s, j) => {
        if (!dots[j]) return;
        const val = s.values[i];
        if (!ok(val)) { dots[j].classList.add("hidden"); return; }
        dots[j].setAttribute("cx", x(i)); dots[j].setAttribute("cy", y(val)); dots[j].classList.remove("hidden");
      });
      const rows = opt.series.map(s => ({ s, val: s.values[i] })).filter(o => o.val != null && isFinite(o.val));
      if (!opt.stack) rows.sort((a, b) => b.val - a.val);
      tip.innerHTML = `<div class="h">${wkRange(i)}</div>` + rows.map(o =>
        `<div class="r"><span><i style="background:${o.s.color}"></i>${o.s.label}</span><b>${opt.tipFmt(o.val)}</b></div>`).join("");
      tip.classList.remove("hidden");
      const bx = box.getBoundingClientRect(), tx = (x(i) / width) * bx.width;
      tip.style.left = Math.min(bx.width - tip.offsetWidth - 4, Math.max(4, tx + (tx > bx.width / 2 ? -tip.offsetWidth - 14 : 14))) + "px";
      tip.style.top = "8px";
    };
    hit.addEventListener("mousemove", move);
    hit.addEventListener("touchstart", e => move(e.touches[0]), { passive: true });
    hit.addEventListener("mouseleave", () => { tip.classList.add("hidden"); xh.classList.add("hidden"); dots.forEach(d => d.classList.add("hidden")); });
  }

  // ---------- chart definitions ----------
  const scale = { "c-spend": "lin", "c-volume": "lin" };
  let famMetric = "spend";
  const idxFmt = t => (t >= 10 ? t.toFixed(0) : t >= 1 ? t.toFixed(1) : t.toPrecision(1));

  function drawAll() {
    for (const [id, src] of [["c-spend", SP], ["c-volume", VO]]) {
      lineChart(id, {
        log: scale[id] === "log", yMin: scale[id] === "log" ? null : 0,
        series: D.groups.map(g => ({ label: GROUPS[g].label, color: GROUPS[g].color, values: src[g] })),
        yFmt: idxFmt, tipFmt: t => t.toFixed(1), aria: id === "c-spend" ? "每周 token 支出指数" : "每周 token 用量指数",
      });
    }

    const rs = (S, T) => share(roll(S), roll(T));
    const shareSeries = [
      { label: "Anthropic 支出", short: "A 支出", color: GROUPS.anthropic.color, values: rs(SP.anthropic, TOT_S) },
      { label: "Anthropic 用量", short: "A 用量", color: GROUPS.anthropic.color, dash: true, values: rs(VO.anthropic, TOT_V) },
      { label: "OpenAI 支出", short: "O 支出", color: GROUPS.openai.color, values: rs(SP.openai, TOT_S) },
      { label: "OpenAI 用量", short: "O 用量", color: GROUPS.openai.color, dash: true, values: rs(VO.openai, TOT_V) },
    ];
    document.getElementById("share-legend").innerHTML = legendHTML(shareSeries);
    lineChart("c-share", { series: shareSeries, yMin: 0, yFmt: t => t + "%", tipFmt: t => pct(t), aria: "份额走势" });

    const base = unit(SP.anthropic, VO.anthropic, a4);
    const up = (S, V) => roll(S).map((s, i) => { const vv = roll(V)[i]; return vv ? (s / vv / base) * 100 : null; });
    const priceSeries = [
      { label: "Anthropic", color: GROUPS.anthropic.color, values: up(SP.anthropic, VO.anthropic) },
      { label: "OpenAI", color: GROUPS.openai.color, values: up(SP.openai, VO.openai) },
      { label: "全部厂商", color: v("--faint"), dash: true, values: up(TOT_S, TOT_V) },
    ];
    document.getElementById("price-legend").innerHTML = legendHTML(priceSeries);
    lineChart("c-price", { series: priceSeries, yMin: 0, yFmt: t => t, tipFmt: t => t.toFixed(0), aria: "单位 token 支出指数" });

    const famTot = D.family_order.reduce((acc, f) => add(acc, roll(D.families[f][famMetric])), zeros());
    const famSeries = D.family_order.map(f => ({
      label: D.families[f].label, color: FAM_COLORS[f],
      values: roll(D.families[f][famMetric]).map((x, i) => (famTot[i] ? (x / famTot[i]) * 100 : null)),
    })).filter(s => s.values.some(x => x > 0.05));
    document.getElementById("fam-legend").innerHTML = legendHTML(famSeries, true);
    lineChart("c-fam", { stack: true, endLabels: false, series: famSeries, yFmt: t => t + "%", tipFmt: t => pct(t), aria: "Anthropic 各模型系列占比" });
  }

  // ---------- tables ----------
  (function makerTable() {
    const keys = Object.keys(D.makers);
    const rows = keys.map(k => {
      const m = D.makers[k];
      const ss = s4(m.spend, a4) / s4(TOT_S, a4) * 100, ssp = s4(m.spend, p4) / s4(TOT_S, p4) * 100;
      const vs = s4(m.volume, a4) / s4(TOT_V, a4) * 100, vsp = s4(m.volume, p4) / s4(TOT_V, p4) * 100;
      const gs = s4(m.spend, a4) / s4(m.spend, p4) - 1, gv = s4(m.volume, a4) / s4(m.volume, p4) - 1;
      const u = s4(m.volume, a4) ? (s4(m.spend, a4) / s4(m.volume, a4)) / unit(SP.anthropic, VO.anthropic, a4) : null;
      return { k, label: m.label, ss, ssp, vs, vsp, gs, gv, u };
    }).sort((a, b) => b.ss - a.ss);
    const color = k => (GROUPS[k] || GROUPS.other).color;
    document.getElementById("makers-note").textContent =
      `${D.week_start[L - 3]} 至 ${W[L]}，对比前四周（${D.week_start[L - 7]} 至 ${W[L - 4]}）。单位支出以 Anthropic = 1.00。`;
    document.getElementById("t-makers").innerHTML =
      `<thead><tr><th>厂商</th><th>支出份额</th><th>变化</th><th>用量份额</th><th>变化</th><th>支出环比</th><th>用量环比</th><th>单位支出</th></tr></thead><tbody>` +
      rows.map(r => `<tr class="${r.k === "anthropic" ? "focus" : ""}"><td><i style="background:${color(r.k)}"></i>${r.label}</td>` +
        `<td>${pct(r.ss)}</td><td>${ptsShort(r.ss - r.ssp)}</td><td>${pct(r.vs)}</td><td>${ptsShort(r.vs - r.vsp)}</td>` +
        `<td>${isFinite(r.gs) ? chg(r.gs) : "新"}</td><td>${isFinite(r.gv) ? chg(r.gv) : "新"}</td><td>${r.u == null ? "—" : r.u.toFixed(2)}</td></tr>`).join("") + "</tbody>";
  })();

  (function modelTable() {
    const ms = Object.entries(D.anthropic_models);
    const tS = ms.reduce((a, [, m]) => a + s4(m.spend, a4), 0), tV = ms.reduce((a, [, m]) => a + s4(m.volume, a4), 0);
    const rows = ms.map(([k, m]) => {
      const ss = s4(m.spend, a4) / tS * 100, vs = s4(m.volume, a4) / tV * 100;
      const prev = s4(m.spend, p4);
      return { k, label: m.label, fam: m.family, ss, vs, u: vs ? ss / vs : null, g: prev ? s4(m.spend, a4) / prev - 1 : null };
    }).filter(r => r.ss >= 0.3 || r.vs >= 0.3).sort((a, b) => b.ss - a.ss);
    document.getElementById("models-note").textContent =
      `${D.week_start[L - 3]} 至 ${W[L]}。占比为 Anthropic 模型层合计的比例；单位支出以 Anthropic 平均 = 1.00；只列支出或用量占比 ≥ 0.3% 的模型。名字里带 EAP 的是未公开发布的内测代号。`;
    document.getElementById("t-models").innerHTML =
      `<thead><tr><th>模型</th><th>占支出</th><th>占用量</th><th>单位支出</th><th>支出环比</th></tr></thead><tbody>` +
      rows.map(r => `<tr><td><i style="background:${FAM_COLORS[r.fam]}"></i>${r.label}${/eap/i.test(r.k) ? '<span class="tag">EAP</span>' : ""}</td>` +
        `<td>${pct(r.ss)}</td><td>${pct(r.vs)}</td><td>${r.u == null ? "—" : r.u.toFixed(2)}</td><td>${r.g == null ? "新" : chg(r.g)}</td></tr>`).join("") + "</tbody>";
  })();

  (function dashTable() {
    const hist = D.dashboard || [];
    const t = document.getElementById("t-dash");
    if (!hist.length) { t.outerHTML = '<p class="note">本次构建没有抓到看板表格。</p>'; return; }
    const get = (h, metric, who) => (h.rows.find(r => r.metric === metric && r.who === who) || {});
    const cols = [
      ["Share of token spend", "Anthropic", "Anthropic 支出份额", x => pct(x)],
      ["Share of token spend", "OpenAI", "OpenAI 支出份额", x => pct(x)],
      ["Share of token volume", "Anthropic", "Anthropic 用量份额", x => pct(x)],
      ["Blended price per million tokens", "Anthropic", "Anthropic $/百万 token", x => "$" + x.toFixed(2)],
      ["Blended price per million tokens", "OpenAI", "OpenAI $/百万 token", x => "$" + x.toFixed(2)],
      ["Blended price per million tokens", "OpenAI & Anthropic", "两家合计 $/百万 token", x => "$" + x.toFixed(2)],
    ];
    // Each scrape carries the current week and the comparison week four weeks back; show both, newest first.
    const byWeek = new Map();
    for (const h of hist) {
      byWeek.set(h.week_end, cols.map(c => get(h, c[0], c[1]).cur));
      if (!byWeek.has(h.prev_week_end)) byWeek.set(h.prev_week_end, cols.map(c => get(h, c[0], c[1]).prev));
    }
    const weeks = [...byWeek.keys()].sort().reverse();
    t.innerHTML = `<thead><tr><th>周（截至）</th>${cols.map(c => `<th>${c[2]}</th>`).join("")}</tr></thead><tbody>` +
      weeks.map(w => `<tr><td>${w}</td>${byWeek.get(w).map((x, j) => `<td>${x == null ? "—" : cols[j][3](x)}</td>`).join("")}</tr>`).join("") + "</tbody>";
  })();

  (function revTable() {
    const R = D.revisions, note = document.getElementById("rev-note"), t = document.getElementById("t-rev");
    if (!R) { note.textContent = "目前只有一份接口快照，下次接口数据变化后，这里会列出 Anthropic 份额被回补修改的周。"; t.remove(); return; }
    if (!R.changes.length) { note.textContent = `和 ${R.compared_with} 的快照相比，Anthropic 各周份额的变化都小于 0.1 个百分点。`; t.remove(); return; }
    note.textContent = `和 ${R.compared_with} 的快照相比，Anthropic 份额变化 ≥ 0.1 个百分点的周。`;
    t.innerHTML = `<thead><tr><th>周（截至）</th><th>指标</th><th>修订前</th><th>修订后</th><th>变化</th></tr></thead><tbody>` +
      R.changes.slice().reverse().map(c => `<tr><td>${c.week}</td><td>${c.metric}</td><td>${pct(c.old, 2)}</td><td>${pct(c.new, 2)}</td><td>${ptsShort(c.delta_pts, 2)}</td></tr>`).join("") + "</tbody>";
  })();

  // ---------- controls ----------
  const press = (group, btn) => group.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", b === btn));
  document.querySelectorAll("#range button").forEach(b => b.addEventListener("click", () => { RANGE = b.dataset.n; press(b.parentNode, b); drawAll(); }));
  document.querySelectorAll(".seg.scale").forEach(g => g.querySelectorAll("button").forEach(b => b.addEventListener("click", () => { scale[g.dataset.for] = b.dataset.v; press(g, b); drawAll(); })));
  document.querySelectorAll("#fam-metric button").forEach(b => b.addEventListener("click", () => { famMetric = b.dataset.v; press(b.parentNode, b); drawAll(); }));
  let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(drawAll, 120); });
  drawAll();
})();
