"""Archive each verified response independently and publish the GPU rent comparison."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from protego import Protego

from build_nebius import encode, publish, warn
from gpu_rent_sources import FEEDS, oracle_gpu_items

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/gpu-rent"
PUBLIC = ROOT / "docs/data"
UA = "RampGpuTracker/1.0 (+https://github.com/yx-style/ramp-ai-tracker)"
FIELDS = ["source", "model", "product", "kind", "term", "region"]
# Official price delivery hosts sometimes deny directory listings, including /robots.txt.
PRICE_HOSTS = {"b0.p.awsstatic.com", "apexapps.oracle.com", "prices.azure.com"}


def request(url):
    response = subprocess.check_output(["curl", "--proto", "=https", "--compressed", "-sS",
                                        "--max-time", "45", "--retry", "2", "-A", UA,
                                        "-w", "\n%{http_code} %{redirect_url}", url])
    body, status = response.rsplit(b"\n", 1)
    code, redirect = status.split(b" ", 1)
    return body, int(code), redirect.decode()


@lru_cache(maxsize=None)
def robots(host):
    url = "https://" + host + "/robots.txt"
    for hop in range(6):
        body, status, redirect = request(url)
        if status not in (301, 302, 303, 307, 308):
            break
        if hop == 5 or urlsplit(redirect).scheme != "https":
            raise ValueError("robots.txt 重定向超限或目标非 HTTPS")
        url = redirect
    if status in (404, 410) or (status == 403 and host in PRICE_HOSTS and urlsplit(url).netloc == host):
        return Protego.parse("")
    if status != 200:
        raise ValueError(f"robots.txt HTTP {status}")
    return Protego.parse(body.decode("utf-8", errors="replace"))


def download(url):
    if not robots(urlsplit(url).netloc).can_fetch(url, "RampGpuTracker"):
        raise ValueError("robots.txt 禁止自动抓取")
    body, status, _ = request(url)
    if status != 200:
        raise ValueError(f"价格源 HTTP {status}")
    return gzip.decompress(body) if body.startswith(b"\x1f\x8b") else body


def archive(feed, body, day):
    if feed == "oracle":
        body = encode(oracle_gpu_items(body))
    digest = hashlib.sha256(body).hexdigest()
    path = DATA / feed / "raw" / (day + "_" + digest[:16] + ".gz")
    if not path.exists():
        publish(path, gzip.compress(body, mtime=0))
    return path.relative_to(ROOT).as_posix()


def read_json(path, default):
    return json.loads(path.read_bytes()) if path.exists() else default


def verify(rows):
    seen = set()
    for row in rows:
        key = tuple(row[k] for k in FIELDS[1:])
        if not all(key) or key in seen or not isinstance(row["price"], (int, float)) or not math.isfinite(row["price"]) or row["price"] <= 0:
            raise ValueError(f"报价缺失、无效或重复：{key}")
        seen.add(key)
    if not rows:
        raise ValueError("未找到有效的 GPU 报价")


def history_rows():
    rows = json.loads((DATA / "historical.json").read_bytes())
    if not isinstance(rows, list) or not rows:
        raise ValueError("历史价格文件为空或格式有误")
    return rows


def observation_key(row):
    return tuple(row[k] for k in FIELDS[1:])


def published_daily_rows():
    """Index the previous export only for recovery, excluding curated history."""
    previous = PUBLIC / "gpu-rent.csv"
    grouped = {}
    if not previous.exists():
        return grouped
    historical = {(r["source"], observation_key(r), r["date"])
                  for r in history_rows()}
    directories = {DATA.relative_to(ROOT) / feed / "raw": feed for feed in FEEDS}
    with previous.open() as f:
        for row in csv.DictReader(f):
            raw = Path(row["raw_path"])
            feed = directories.get(raw.parent)
            if not feed or not raw.name.startswith(row["date"] + "_") or row["source"] != FEEDS[feed][0]:
                continue
            if (row["source"], observation_key(row), row["date"]) in historical:
                continue
            quote = {k: row[k] for k in FIELDS[1:]}
            quote.update(price=json.loads(row["usd_per_gpu_hour"]), note=row["basis"], raw=str(raw), url=row["url"])
            grouped.setdefault(feed, {}).setdefault(row["date"], []).append(quote)
    return grouped


def read_observations(feed, previous=None):
    """Without a recovery baseline, read strictly and never modify the checkout."""
    path = DATA / feed / "observations.json"
    try:
        dates = json.loads(path.read_bytes())
        if not isinstance(dates, dict) or not dates:
            raise ValueError("观察归档为空")
        for day, rows in dates.items():
            datetime.strptime(day, "%Y-%m-%d")
            verify(rows)
            if any(not isinstance(row.get("note"), str) or not isinstance(row.get("raw"), str)
                   or not row["raw"] for row in rows):
                raise ValueError("观察缺少说明或快照路径")
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        if previous is None:
            raise
        if not previous:
            if not path.exists() and not any((path.parent / "raw").glob("*.gz")):
                return {}
            raise
        dates = {}

    restored = 0
    for day, published in (previous or {}).items():
        rows = dates.setdefault(day, [])
        existing = {observation_key(row): row for row in rows}
        for quote in published:
            row = existing.get(observation_key(quote))
            if row is not None:
                # Old observations did not store the query URL. Preserve it when an endpoint changes.
                if "url" not in row and row["raw"] == quote["raw"] and quote["url"] != FEEDS[feed][1]:
                    row["url"] = quote["url"]
                    restored += 1
                continue
            if not (ROOT / quote["raw"]).is_file():
                raise ValueError(f"还原 {feed} 时找不到快照 {quote['raw']}")
            rows.append(quote)
            restored += 1
        verify(rows)
    if restored:
        publish(path, encode(dates))
        warn(f"{feed}：从已发布数据还原 {restored} 条观察或查询链接")
    return dates


def refresh(feed, day, previous=None):
    source, url, parse = FEEDS[feed]
    body = download(url)
    rows = list(parse(body))
    verify(rows)
    path = DATA / feed / "observations.json"
    dates = read_observations(feed, previous if previous is not None else published_daily_rows().get(feed, {}))
    raw = archive(feed, body, day)
    prior = {tuple(r[k] for k in FIELDS[1:]) for rows_on_day in dates.values() for r in rows_on_day}
    observed = {tuple(r[k] for k in FIELDS[1:]) for r in rows}
    missing = prior - observed
    previous = dates.get(day)
    if previous:
        # Missing rows in a same-day retry must not delete already verified prices.
        keyed = {tuple(r[k] for k in FIELDS[1:]): r for r in previous}
        for row in rows:
            keyed[tuple(row[k] for k in FIELDS[1:])] = dict(row, raw=raw, url=url)
        dates[day] = list(keyed.values())
    else:
        dates[day] = [dict(row, raw=raw, url=url) for row in rows]
    publish(path, encode(dates))
    print(f"{source} ({feed}): {len(rows)}")
    return len(missing)


def collect_series(previous=None):
    series = {}
    official_days = set()
    def add(row):
        key = tuple(row[k] for k in FIELDS)
        entry = series.setdefault(key, {**dict(zip(FIELDS, key)), "points": {}})
        point = [row["price"], row["note"], row["url"], row.get("raw", "")]
        old = entry["points"].get(row["date"])
        if old is not None and old != point:
            raise ValueError(f"同日归档记录冲突：{key} {row['date']}")
        entry["points"][row["date"]] = point
    for row in history_rows():
        add(row)
    for feed, (source, url, _) in FEEDS.items():
        for day, rows in read_observations(feed, None if previous is None else previous.get(feed, {})).items():
            for row in rows:
                add(dict(row, source=source, date=day, url=row.get("url", url)))
    with (PUBLIC / "nebius-daily.csv").open() as f:
        for row in csv.DictReader(f):
            if row["source"] not in ("06.ai", "nebius_api") or row["regime"] not in ("dynamic_spot", "fixed_preemptible"):
                raise ValueError(f"Nebius 日线来源或口径有误：{row}")
            official = row["source"] == "nebius_api"
            if official:
                official_days.add((row["sku_id"], row["region"], row["date_utc"]))
            add(dict(source="Nebius 现货" if official else "06.ai（Nebius）", model=row["model"],
                     kind="现货日均" if row["regime"] == "dynamic_spot" else "固定可中断挂牌",
                     term="可中断", region=row["region"], product=row["platform"] + " · " + row["sku_id"],
                     price=float(row["spot_usd_per_gpu_hour"]), date=row["date_utc"],
                     note="完整 UTC 日的官方小时报价均值。" if official else "06.ai 日内 15 分钟区间末价均值。",
                     url="https://docs.nebius.com/signup-billing/spot-prices" if official else "https://06.ai/trackers/gpu-spot-prices/"))
    return series, official_days


def render_exports(series):
    records = []
    csvfile = io.StringIO(newline="")
    writer = csv.writer(csvfile, lineterminator="\n")
    writer.writerow([*FIELDS, "date", "usd_per_gpu_hour", "basis", "url", "raw_path"])
    for key, entry in sorted(series.items()):
        points = [[d, *values] for d, values in sorted(entry["points"].items())]
        for point in points:
            writer.writerow([*key, *point])
        records.append(dict(entry, points=points))
    return {"gpu-rent.csv": csvfile.getvalue().encode(),
            "gpu-rent.json": encode({"series": records, "status": json.loads((DATA / "status.json").read_bytes())})}


def validate_exports():
    # Rebuild from inputs only. Neither publication is a reference for the other.
    series, _ = collect_series()
    for name, expected in render_exports(series).items():
        if (PUBLIC / name).read_bytes() != expected:
            raise ValueError(f"{name} 与历史、日更观察或 Nebius 日线不一致")


def export():
    series, official_days = collect_series(published_daily_rows())
    previous = PUBLIC / "gpu-rent.csv"
    if previous.exists():
        with previous.open() as f:
            for row in csv.DictReader(f):
                key = tuple(row[k] for k in FIELDS)
                if key in series and row["date"] in series[key]["points"]:
                    continue
                sku = row["product"].rsplit(" · ", 1)[-1]
                if row["source"] == "06.ai（Nebius）" and (sku, row["region"], row["date"]) in official_days:
                    continue
                raise ValueError(f"构建遗漏已发布观察：{key} {row['date']}")
    for name, body in render_exports(series).items():
        publish(PUBLIC / name, body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-only", action="store_true")
    args = parser.parse_args()
    failed = 0
    if not args.export_only:
        today = datetime.now(timezone.utc).date().isoformat()
        path = DATA / "status.json"
        status = read_json(path, {})
        previous = published_daily_rows()
        for feed in FEEDS:
            try:
                missing = refresh(feed, today, previous.get(feed, {}))
                status[feed] = {"checked": today, "verified": today}
                if missing:
                    message = f"{missing} 条旧报价本次未返回，已保留原观察日期"
                    status[feed]["error"] = message
                    warn(f"{feed}：{message}")
            except Exception as exc:
                failed += 1
                warn(f"{feed}：{exc}")
                status.setdefault(feed, {}).update(checked=today, error="本次抓取失败，沿用最近核实值")
        publish(path, encode(status))
    export()
    if failed == len(FEEDS):
        warn("本轮所有来源抓取失败，已更新检查状态并保留历史价格")
    return 0


if __name__ == "__main__":
    sys.exit(main())
