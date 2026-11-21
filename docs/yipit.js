(async function () {
  const $ = id => document.getElementById(id);
  const element = (tag, text, parent, cls) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (cls) node.className = cls;
    parent.append(node);
    return node;
  };
  const colors = ["#ab2a25", "#212123", "#81716b", "#c07761"];
  const captions = {
    "All Other": "其余", "Comm. Services": "通信服务", Consumer: "消费", "Energy & Industrials": "能源与工业",
    Financials: "金融", Healthcare: "医疗", "Software/Tech": "软件与科技", Consumption: "API／用量",
    Subscription: "订阅", Total: "合计", "Combined Total": "综合", Other: "其他", "non subscription": "非订阅",
    "Top 1%": "前 1%", "Top 10%": "前 10% 档", "Top 50%": "前 50% 档", Churn: "流失",
  };
  const label = s => captions[s] || s.replace("New Signup - ", "新增 · ").replace("Re-Signup - ", "重新订阅 · ")
    .replace("Upgrade - ", "升级 · ").replace("Downgrade - ", "降级 · ").replace(" -> ", " → ");
  const format = (n, unit) => n == null ? "—" : (unit === "%" ? n * 100 : n)
    .toLocaleString("zh-CN", { maximumFractionDigits: 3 }) + (unit === "%" ? "%" : "");
  function select(parent, title, items) {
    const field = element("label", null, parent);
    element("span", title, field);
    const menu = element("select", null, field);
    for (const [key, name] of items) menu.add(new Option(name, key));
    return menu;
  }
  function table(parent, headers, rows) {
    const wrap = element("div", null, parent, "tablewrap");
    const t = element("table", null, wrap, "t");
    const tr = t.createTHead().insertRow();
    headers.forEach(h => element("th", h, tr));
    const body = t.createTBody();
    for (const row of rows) {
      const r = body.insertRow();
      row.forEach(v => element("td", v, r));
    }
  }

  function completeMonths(g, data) {
    if (g.grain !== "月度" && g.grain !== "测量日") return g;
    // The combined model panel requires both component panels to close.
    const dates = g.sheet === "Combined Cloud and OR"
      ? [data.through["OpenRouter"], data.through["Cloud API"]]
      : [data.through[g.sheet]];
    const cutoff = [data.date, ...dates.filter(Boolean)].sort()[0];
    const count = g.periods.findIndex(p => {
      const [year, month] = p.split("-").map(Number);
      const end = new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
      return end > cutoff || (g.grain === "测量日" && p !== end);
    });
    if (count < 0) return g;
    return { ...g, periods: g.periods.slice(0, count), columns: g.columns.slice(0, count),
      series: g.series.map(s => ({ ...s, values: s.values.slice(0, count) }))
        .filter(s => s.values.some(v => v != null)) };
  }

  // Periods occupy source-column order, including missing interior observations.
  function plot(box, g, lines) {
    box.replaceChildren();
    const points = g.periods.map((_, i) => i).filter(i => lines.some(s => s.values[i] != null));
    const start = points[0], end = points.at(-1);
    const width = box.clientWidth;
    const left = 58, right = width - 12, top = 12, bottom = width < 540 ? 168 : 208;
    const values = lines.flatMap(s => s.values.filter(n => n != null));
    if (!values.length) {
      element("p", "暂无完整期间的数据。", box, "note");
      return;
    }
    const min = Math.min(0, ...values), max = Math.max(0, ...values);
    const rough = (max - min || 1) / 4;
    const power = 10 ** Math.floor(Math.log10(rough));
    const step = [1, 2, 5, 10].find(n => n * power >= rough) * power;
    const lowTick = Math.floor(min / step), highTick = Math.ceil(max / step);
    const lo = lowTick * step, hi = highTick * step;
    const x = i => start === end ? (left + right) / 2 : left + (i - start) / (end - start) * (right - left);
    const y = n => bottom - (n - lo) / (hi - lo || 1) * (bottom - top);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    box.append(svg);
    svg.setAttribute("viewBox", `0 0 ${width} ${bottom + 30}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", g.title + "，" + g.unit);
    function mark(tag, attrs, text) {
      const n = document.createElementNS(svg.namespaceURI, tag);
      for (const [k,v] of Object.entries(attrs)) n.setAttribute(k, v);
      if (text != null) n.textContent = text;
      svg.append(n);
      return n;
    }
    for (let j = lowTick; j <= highTick; j++) {
      const n = j * step;
      mark("line", { x1: left, x2: right, y1: y(n), y2: y(n), class: "grid" });
      const tick = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumSignificantDigits: 3 })
        .format(g.unit === "%" ? n * 100 : n);
      mark("text", { x: left - 6, y: y(n) + 4, "text-anchor": "end" }, tick);
    }
    const steps = Math.min(end - start, Math.max(1, Math.floor((right - left) / 110)));
    for (let j = 0; j <= steps; j++) {
      const i = steps ? start + Math.round((end - start) * j / steps) : start;
      mark("text", { x: x(i), y: bottom + 20, "text-anchor": steps && !j ? "start" : j === steps && steps ? "end" : "middle" }, g.periods[i]);
    }
    lines.forEach((s, j) => {
      const color = colors[j];
      let path = "", pen = false;
      s.values.forEach((n,i) => {
        if (n == null) { pen = false; return; }
        path += (pen ? "L" : "M") + x(i).toFixed(2) + " " + y(n).toFixed(2);
        pen = true;
        const dot = mark("circle", { cx: x(i), cy: y(n), r: 2.1, fill: color });
        const title = document.createElementNS(svg.namespaceURI, "title");
        title.textContent = g.periods[i] + " · " + label(s.label) + " · " + format(n, g.unit);
        dot.append(title);
      });
      mark("path", { d: path, stroke: color, "stroke-width": 2, fill: "none", "pointer-events": "none" });
    });
  }
  let observers = [];
  function chartCard(data, heading, choices, note, channels) {
    const box = element("section", null, $("charts"), "card");
    element("h2", heading, box);
    if (note) element("p", note, box, "note");
    const controls = element("div", null, box, "y-selectors");
    const channel = channels ? select(controls, "渠道", channels) : null;
    const metric = select(controls, "指标", choices);
    if (choices.length === 1) metric.parentElement.hidden = true;
    const source = element("p", null, box, "note");
    const legend = element("div", null, box, "legend");
    const graph = element("div", null, box, "chart");
    const details = element("details", null, box);
    element("summary", "查看数值与来源单元格", details);
    const cells = element("div", null, details);
    let selector, g, lines;
    function draw() {
      plot(graph, g, lines);
    }
    function chooseSeries() {
      const lastValue = s => s.values.findLast(v => v != null);
      let indexes;
      if (selector.value !== "*") indexes = [Number(selector.value)];
      else if (g.series.length <= 4) indexes = g.series.map((_,i) => i);
      else if (g.grain === "观察月序") {
        indexes = g.series.map((s,i) => i).filter(i => g.series[i].values.filter(v => v != null).length >= 6).slice(-3);
        if (!indexes.length) indexes = [0];
      } else {
        const latest = Math.max(...g.series.map(s => s.values.findLastIndex(v => v != null)));
        indexes = g.series.map((_,i) => i).filter(i => g.series[i].values[latest] != null && g.series[i].label !== "Total")
          .sort((a,b) => Math.abs(lastValue(g.series[b])) - Math.abs(lastValue(g.series[a]))).slice(0,3);
        if (!indexes.length) indexes = [0];
      }
      lines = indexes.map(i => g.series[i]);
      legend.replaceChildren();
      lines.forEach((s,j) => {
        const entry = element("span", null, legend);
        element("i", null, entry).style.borderColor = colors[j];
        entry.append(label(s.label));
      });
      cells.replaceChildren();
      table(cells, [g.grain, ...lines.map(s => label(s.label))],
        g.periods.map((p,i) => [p, ...lines.map(s => s.values[i] == null ? "—" :
          format(s.values[i],g.unit) + " · " + g.columns[i] + s.row)]).reverse());
      draw();
    }
    function changeMetric() {
      const key = channel ? channel.value + "-" + metric.value : metric.value;
      g = completeMonths(data.charts[key], data);
      if (selector) selector.parentElement.remove();
      const overview = g.series.length <= 4 ? "全部" : g.grain === "观察月序" ? "最近三个满六期的批次" : "最近一期按绝对值取前三项";
      selector = select(controls, "序列", [["*", overview], ...g.series.map((s,i) => [i,label(s.label)])]);
      selector.onchange = chooseSeries;
      source.textContent = [g !== data.charts[key] ? g.grain + "（仅展示完整月份）" : g.grain, g.unit, g.sheet, data.date + " 期"].join(" · ");
      chooseSeries();
    }
    metric.onchange = changeMetric;
    if (channel) channel.onchange = changeMetric;
    changeMetric();
    const watcher = new ResizeObserver(draw);
    watcher.observe(box);
    observers.push(watcher);
  }

  function kpis(data) {
    const box = $("kpis");
    box.replaceChildren();
    function add(title, value, note) {
      const card = element("article", null, box, box.children.length ? "kpi" : "kpi hl");
      element("div", title, card, "k");
      element("div", format(Math.round(value * 10) / 10) + " 亿美元", card, "v");
      element("div", note, card, "n");
    }
    function last(key, index, title, factor) {
      const g = data.charts[key];
      if (!g || !g.series[index]) return;
      const s = g.series[index];
      const i = s.values.findLastIndex(v => v != null);
      const partial = key === "adds" && !isMonthEnd(g.periods[i]);
      add(title, s.values[i] * factor, g.periods[i] + (partial ? " · 月内数据外推全月" : "") + " · " + g.sheet + "!" + g.columns[i] + s.row);
    }
    last("month", 0, "最新月收入估算", .01);
    last("quarter", 1, "最近完整季度收入（原表披露口径）", .01);
    if (data.extrapolation) {
      const e = data.extrapolation;
      add(e.period + " 收入 · 低置信度外推", e.value / 100, e.sheet + " · " + e.formula);
    }
    last("arr", 0, "最新 run-rate ARR 估算", 10);
    last("adds", 0, "ARR 月新增估算", 10);
  }
  function isMonthEnd(iso) {
    const d = new Date(iso + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + 1);
    return d.getUTCDate() === 1;
  }
  function history(editions, data) {
    const box = $("revisions");
    box.replaceChildren();
    $("history-note").textContent = "已纳入 " + editions.map(e => e.date).join("、") + "。";
    const index = editions.indexOf(data);
    if (!index) {
      element("p", "这是首个期次，可作为后续修订的比较基准。", box, "note");
      return;
    }
    const before = editions[index-1];
    const controls = element("div", null, box, "y-selectors");
    const available = Object.keys(data.charts).filter(k => before.charts[k]);
    const choice = select(controls, "比较指标", available.map(k => [k, data.charts[k].sheet + " · " + data.charts[k].title]));
    const result = element("div", null, box);
    function compare() {
      const g = data.charts[choice.value], old = before.charts[choice.value];
      const periods = new Map(old.periods.map((p,i) => [p,i]));
      const rows = [];
      for (const s of g.series) {
        const prev = old.series.find(r => r.label === s.label);
        if (!prev) continue;
        g.periods.forEach((p,i) => {
          if (!periods.has(p)) return;
          const a = prev.values[periods.get(p)], b = s.values[i];
          if (a !== b) rows.push([p,label(s.label),format(a,g.unit),format(b,g.unit)]);
        });
      }
      result.replaceChildren();
      if (rows.length) table(result, [g.grain,g.unit,before.date,data.date], rows);
      else element("p", "两期重叠期间的同一序列无修订。", result, "note");
    }
    choice.onchange = compare;
    compare();
  }

  try {
    const response = await fetch("data/yipit.json", {cache:"no-cache"});
    if (!response.ok) throw Error("HTTP " + response.status);
    const {editions} = await response.json();
    const menu = $("edition");
    for (const e of editions.toReversed()) menu.add(new Option(e.date,e.date));
    function render() {
      observers.forEach(o => o.disconnect());
      observers = [];
      const data = editions.find(e => e.date === menu.value);
      $("nov-summary").hidden = data.date !== "2026-11-18";
      $("status").textContent = $("nov-summary").hidden ? "本期工作簿已载入，暂未收录配套报告摘要。" : "";
      kpis(data);
      $("charts").replaceChildren();
      function card(title, keys, note) {
        const choices = keys.filter(k => data.charts[k]).map(k => [k,data.charts[k].title]);
        if (choices.length) chartCard(data, title, choices, note);
      }
      card("收入估算与披露口径", ["month","quarter"], "月度对照值按季度收入分配。季度图只含完整季度。");
      card("年化收入与月新增", ["arr","adds"], "图中展示月末数据。页首列出最新测量日的估算。新增以此前一月的披露或插值为基数。");
      chartCard(data, "各模型的 token 支出、用量与价格",
        [["spend","支出"],["tokens","用量"],["price","每百万 token 价格"],["blended","综合单价"]],
        "云 API 周表以周一标记，OpenRouter 周表以周日标记。OpenRouter 在 2025 年 6 月前的价格和支出未充分反映提示缓存。",
        [["combined","企业云与 OpenRouter 加权 · 月度"],["cloud","Cloud API · 月度"],["cloud-w","Cloud API · 周度"],
         ["router","OpenRouter · 月度"],["router-w","OpenRouter · 周度"]]);
      card("B2B 支出面板", ["b2b","median","large"], "大客户按当月支出年化。2026-11-18 报告中的 24% 采用过去 12 个月累计支出。");
      card("B2B 客户留存与支出扩张", ["retain","expand"], "按首次交易月分组。横轴按原表从左至右的观察次序排列，首列为基期。");
      card("各行业的 Anthropic 采用率与支出", ["adopt","sector"], "覆盖企业云和 B2B 面板。两家公司在各行业的支出份额见 11 月 18 日报告摘要，工作簿未列该序列。");
      card("Claude 订阅", ["active","new","net"], "来自电子邮件收据固定面板。");
      card("消费者交易面板", ["consumer","users","arpu"], "ARPU 包含额外用量费用，年付订阅摊入各月。");
      card("客户支出分层", ["tiers","tier-median","tier-avg","tier-count"], "企业云和 B2B 客户合并统计。前 1%、其后的前 10% 档、前 50% 档及其余客户分别计数。");
      card("历史估算与误差", ["rev-test","rev-error","arr-test","arr-error"], "误差按估算值除以参照值再减 1 计算。ARR 参照值含插值，本期回测和当时发布的初步估算分别列示。");
      $("cutoffs").textContent = "各面板数据截至日（Max Dates）：" + Object.entries(data.through).map(([k,v]) => k + " " + v).join("，") + "。";
      $("cover-yipit").textContent = data.definitions.yipit;
      $("cover-company").textContent = data.definitions.company;
      history(editions, data);
    }
    menu.onchange = render;
    render();
  } catch (err) {
    $("status").textContent = "数据载入失败：" + err.message;
    console.error(err);
  }
})();
