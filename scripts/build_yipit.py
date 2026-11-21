"""Build a local static tracker from dated YipitData Anthropic workbooks."""
import argparse
import calendar
import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

OUTPUT = Path(__file__).resolve().parents[1] / "docs/data/yipit.json"
QUARTER = re.compile(r"([1-4])Q\s*'?(\d{2})$")
MONTHLY = "Monthly Combined Spend"
REV = "Key Metrics - Revenue"
ARR = "Key Metrics - ARR"
HISTORY = "Historical Accuracy"
COHORT = "B2B Spend Cohorts"
SECTOR = "Sector Data"
TIERS = "Cloud and B2B Customer Cohorts"


def read_workbook(path):
    """Preserve literal input precision. Fill missing formula results from a private copy."""
    book = load_workbook(path, data_only=True)
    formulas = load_workbook(path)
    missing = [(s.title, c.coordinate) for s in formulas for row in s.iter_rows()
               for c in row if c.data_type == "f" and book[s.title][c.coordinate].value is None]
    formulas.close()
    if not missing:
        return book
    candidates = [shutil.which("soffice"), Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice",
                  "/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    engine = next((p for p in candidates if p and Path(p).is_file()), None)
    if not engine:
        raise ValueError(f"{path.name} 的公式缺少缓存。请安装 LibreOffice，或用 Excel 重算并保存库外副本。")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        subprocess.run([str(engine), "-env:UserInstallation=" + (folder / "profile").as_uri(),
                        "--headless", "--convert-to", "xlsx", "--outdir", tmp, str(path.resolve())],
                       check=True, capture_output=True, timeout=180)
        calculated = load_workbook(folder / path.name, data_only=True)
        for sheet, addr in missing:
            book[sheet][addr].value = calculated[sheet][addr].value
        calculated.close()
    return book


def find_row(sheet, column, label, start=1):
    for row in sheet.iter_rows(min_row=start, min_col=column, max_col=column):
        if str(row[0].value).strip() == label:
            return row[0].row
    raise ValueError(f"{sheet.title}: 缺少表头 {label!r}")


def group(sheet, title, unit, header, rows, *, grain="月度", quarter_ends=None):
    periods, columns = [], []
    for cell in sheet[header]:
        v = cell.value
        if grain == "季度":
            match = QUARTER.fullmatch(str(v))
            if not match:
                continue
            q, year = match.groups()
            period = f"20{year} Q{q}"
            if quarter_ends:
                end = sheet.cell(quarter_ends, cell.column).value
                month = int(q) * 3
                if end.date() != date(2000 + int(year), month, calendar.monthrange(2000 + int(year), month)[1]):
                    continue
        elif grain == "观察月序":
            if cell.column < 4 or v is None:
                continue
            period = str(len(periods) + 1)
        elif isinstance(v, date) and v.year > 2000:
            period = v.strftime("%Y-%m-%d" if grain in ("周起始日", "周结束日", "测量日") else "%Y-%m")
        else:
            continue
        periods.append(period)
        columns.append(cell.column)
    if len(set(periods)) != len(periods):
        raise ValueError(f"{sheet.title} {title}: 重复期间")
    series = []
    for r, label in rows:
        vals = []
        for col in columns:
            cell = sheet.cell(r, col)
            if cell.data_type == "e":
                raise ValueError(f"{sheet.title}!{cell.coordinate}: {cell.value}")
            vals.append(cell.value if isinstance(cell.value, (int, float)) else None)
        if any(x is not None for x in vals):
            series.append(dict(label=label, row=r, values=vals))
    if not series:
        raise ValueError(f"{sheet.title} {title}: 没有可用数据")
    return dict(title=title, sheet=sheet.title, unit=unit, grain=grain, periods=periods,
                columns=[get_column_letter(c) for c in columns], series=series)


def block(sheet, section, metric, title, unit, grain="月度"):
    section_row = find_row(sheet, 1, section)
    header = section_row + 1
    start = find_row(sheet, 2, metric, header + 1) + 1
    rows = []
    for r in range(start, sheet.max_row + 1):
        label = sheet.cell(r, 3).value
        if label is None:
            break
        rows.append((r, label.strftime("%Y-%m") if isinstance(label, date) else label))
    return group(sheet, title, unit, header, rows, grain=grain)


def named(sheet, title, unit, header_col, header_label, label_col, labels, **options):
    header = find_row(sheet, header_col, header_label)
    rows = [(find_row(sheet, label_col, name, header + 1), zh) for name, zh in labels]
    return group(sheet, title, unit, header, rows, **options)


def extract(path, edition):
    book = read_workbook(path)
    charts = {}
    s = book[MONTHLY]
    rev_block = find_row(s, 2, "Revenue ($M)")
    charts["month"] = group(s, "月度收入", "百万美元", find_row(s, 2, "Month"),
                            [(find_row(s, 3, k, rev_block), v) for k, v in
                             [("Estimated", "Yipit 估算"), ("Reported", "按季度口径分配的月收入")]])
    s = book[REV]
    charts["quarter"] = named(s, "完整季度收入", "百万美元", 2, "Fiscal Quarter", 2,
                              [("Estimated Revenue", "Yipit 回测"), ("Reported Total Revenue", "原表披露口径")],
                              grain="季度", quarter_ends=find_row(s, 2, "Quarter Ending Date or Latest Date"))
    for key, title, labels in [
        ("arr", "月末与最新测量日的 run-rate", [("Estimated ARR ($bn)", "Yipit 估算"),
         ("Reported/Interpolated ARR ($bn)*", "披露、媒体信息及插值")]),
        ("adds", "ARR 月新增", [("Estimated M/M ARR Added ($bn) (Green = Extrapolated)", "估算（未完月外推）"),
         ("M/M ARR Added", "披露及插值序列的月差")])]:
        if ARR in book:
            charts[key] = named(book[ARR], title, "十亿美元", 2, "Month Ending Date", 2, labels, grain="测量日")

    for prefix, sheet, weekly in [("cloud", "Cloud API", False), ("router", "OpenRouter", False),
                                  ("combined", "Combined Cloud and OR", False), ("cloud-w", "Cloud API - Weekly", True),
                                  ("router-w", "OpenRouter - Weekly", True)]:
        cloud, combined = prefix.startswith("cloud"), prefix == "combined"
        grain = ("周起始日" if cloud else "周结束日") if weekly else "月度"
        for key, section, metric, title, unit in [
            ("spend", "Data by Model" if cloud else "Spend by Model" if combined else "Implied Spend ($M)",
             "Spend" if cloud else "Spend by Model ($M)" if prefix == "router" else "Spend ($M)",
             "模型支出", "美元" if cloud else "百万美元"),
            ("tokens", "Data by Model" if cloud else "Tokens by Model" if combined else "Anthropic Tokens (API)",
             "Tokens (bn)" if cloud or combined else "Anthropic Tokens" if weekly else "Tokens by Model",
             "模型用量", "十亿 token"),
            ("price", "Data by Model" if cloud else "Price per Million", "Price" if cloud else "Price per Million",
             "每百万 token 价格", "美元／百万 token")]:
            charts[f"{prefix}-{key}"] = block(book[sheet], section, metric, title, unit, grain)
        section, metric = (("Combined Price per Token (ex-Claude Enterprise/Platform)", "Combined Price per Million")
                           if cloud else ("Price per Million", "Price per Million" if combined else "Combined Price"))
        if combined:
            s = book[sheet]
            start = find_row(s, 3, "Total Combined")
            charts[prefix + "-blended"] = group(s, "综合单价", "美元／百万 token", start - 3, [(start, "综合单价")])
        else:
            charts[prefix + "-blended"] = block(book[sheet], section, metric, "综合单价", "美元／百万 token", grain)

    for key, sheet, section, metric, title, unit in [
        ("b2b", "B2B Spend Panel", "Product Detail", "Spend by Product ($M)", "企业订阅与 API 支出", "百万美元"),
        ("median", "B2B Spend Panel", "Spend Per Panelist", "Median Month Spend", "企业月支出中位数", "美元"),
        ("large", "B2B Spend Panel", "Panelists", "Panelists >$100k ARR", "年化支出超过 10 万美元的企业", "家"),
        ("retain", COHORT, "Anthropic Cohorts", "Retention", "各批客户的付费留存率", "%"),
        ("expand", COHORT, "Anthropic Cohorts", "Spend Expansion by Cohort", "各批客户的支出扩张倍数", "倍"),
        ("adopt", SECTOR, "Adoption", "Adoption", "各行业采用率", "%"),
        ("sector", SECTOR, "Data by Sector", "Spend ($M)", "各行业 Anthropic 支出", "百万美元"),
        ("new", "Claude Subscriptions", "Subscriber Behavior", "Claude", "新增、流失与套餐变更", "笔"),
        ("active", "Claude Subscriptions", "Monthly Subscriptions", "Active Subscriptions", "活跃订阅", "份"),
        ("net", "Claude Subscriptions", "Monthly Subscriptions", "Net Adds", "净新增订阅", "份"),
        ("consumer", "Consumer Transaction Panel", "Observed Panel Spend", "Observed Spend", "消费者交易支出", "美元"),
        ("users", "Consumer Transaction Panel", "Active Subscribers", "Active Subscribers", "交易面板活跃用户", "人"),
        ("arpu", "Consumer Transaction Panel", "ARPU", "ARPU", "每名用户月收入", "美元"),
        ("tiers", TIERS, "Spend by Tier", "Spend ($M)", "各层客户支出", "百万美元"),
        ("tier-median", TIERS, "Median Spend by Tier", "Median Spend", "各层客户支出中位数", "美元"),
        ("tier-avg", TIERS, "Avg. Spend by Tier", "Average Spend", "各层客户平均支出", "美元"),
        ("tier-count", TIERS, "Panelists by Tier", "Panelists", "各层客户数", "家")]:
        charts[key] = block(book[sheet], section, metric, title, unit, "观察月序" if sheet == COHORT else "月度")

    s = book[HISTORY]
    for key, quarterly, error in [("rev-test", True, False), ("rev-error", True, True),
                                  ("arr-test", False, False), ("arr-error", False, True)]:
        if not quarterly and "Timeframe (Assumed as Month-End)" not in {c.value for c in s["C"]}:
            continue
        labels = ([("Delta (Est. vs. Reported)", "回测误差")] if error else
                  [("Company Reported", "原表披露口径"), ("YipitData Current Backtest Estimate", "本期回测")]) if quarterly else (
                  [("Delta (Est. vs. Reported/Interpolated*)", "回测误差"),
                   ("Delta (Est. vs. Reported/Interpolated)", "当期发布误差")] if error else
                  [("Company Reported (Interpolated*)", "披露及插值"), ("YipitData Current Backtest Estimate", "本期回测"),
                   ("YipitData Published Estimate", "当期发布")])
        charts[key] = named(s, ("季度收入" if quarterly else "月度 ARR") + ("估算误差" if error else "历史核对"),
                            "%" if error else "十亿美元", 3, "Quarter" if quarterly else "Timeframe (Assumed as Month-End)",
                            4, labels, grain="季度" if quarterly else "月度")

    dates = {r[2].value: r[3].value.date().isoformat() for r in book["Max Dates"].iter_rows()
             if isinstance(r[3].value, date)}
    cover = book["Cover"]
    definition = find_row(cover, 2, "ARR or Annualized Revenue or Run-Rate Revenue")
    result = dict(date=edition, charts=charts, through=dates, definitions={
        "sheet": cover.title,
        "yipit": cover.cell(definition, 3).value,
        "company": cover.cell(definition, 4).value,
    })
    if edition == "2026-11-18":
        # The report extrapolates October using its prior-year share of Q4.
        s = book[MONTHLY]
        data = charts["month"]
        idx = data["periods"].index
        month, prior = data["series"]
        oct26 = data["columns"][idx("2026-10")] + str(month["row"])
        oct25 = data["columns"][idx("2025-10")] + str(prior["row"])
        q4 = next(c.column_letter for c in s[find_row(s, 2, "Month")] if c.value == "4Q '25") + str(prior["row"])
        result["extrapolation"] = dict(period="2026 Q4", unit="百万美元", sheet=MONTHLY,
                                       formula=f"{oct26} × {q4} ÷ {oct25}",
                                       value=s[oct26].value * s[q4].value / s[oct25].value)
    book.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbooks", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    incoming = {}
    for path in args.workbooks:
        matches = re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", path.name)
        if len(matches) != 1:
            parser.error(f"文件名须含一个报告日期：{path.name}")
        edition = date.fromisoformat(matches[0]).isoformat()
        if edition in incoming:
            parser.error(f"同一期次传入了两个文件：{edition}")
        incoming[edition] = extract(path, edition)
    directory = os.open(args.output.parent, os.O_RDONLY)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        old = json.loads(args.output.read_text())["editions"] if args.output.exists() else []
        editions = {e["date"]: e for e in old}
        editions.update(incoming)
        payload = {"editions": [editions[d] for d in sorted(editions)]}
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent, delete=False) as f:
            f.write(serialized + "\n")
        os.replace(f.name, args.output)
    finally:
        os.close(directory)
    print(f"{args.output} · {', '.join(sorted(editions))}")


if __name__ == "__main__":
    main()
