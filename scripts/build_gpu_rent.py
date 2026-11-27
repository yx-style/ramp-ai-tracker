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


def read_observations(feed):
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
        return dates
    except (FileNotFoundError, ValueError, KeyError, TypeError) as exc:
        # The checked-in CSV identifies the exact revision retained after each same-day retry.
        if not path.exists() and not any((path.parent / "raw").glob("*.gz")):
            return {}
        restored = {}
        with (PUBLIC / "gpu-rent.csv").open() as f:
            for row in csv.DictReader(f):
                raw = Path(row["raw_path"])
                if raw.parent != path.parent.relative_to(ROOT) / "raw" or not raw.name.startswith(row["date"] + "_"):
                    continue
                if not (ROOT / raw).is_file():
                    raise ValueError(f"还原 {feed} 时找不到快照 {raw}") from exc
                quote = {k: row[k] for k in FIELDS[1:]}
                quote.update(price=float(row["usd_per_gpu_hour"]), note=row["basis"], raw=str(raw))
                restored.setdefault(row["date"], []).append(quote)
        if not restored:
            raise ValueError(f"无法从已发布数据还原 {feed}") from exc
        for rows in restored.values():
            verify(rows)
        publish(path, encode(restored))
        warn(f"{feed}：已根据上次发布的 CSV 和快照路径还原观察文件")
        return restored


def refresh(feed, day):
    source, url, parse = FEEDS[feed]
    body = download(url)
    rows = list(parse(body))
    verify(rows)
    path = DATA / feed / "observations.json"
    dates = read_observations(feed)
    raw = archive(feed, body, day)
    prior = {tuple(r[k] for k in FIELDS[1:]) for rows_on_day in dates.values() for r in rows_on_day}
    observed = {tuple(r[k] for k in FIELDS[1:]) for r in rows}
    missing = prior - observed
    previous = dates.get(day)
    if previous:
        # Missing rows in a same-day retry must not delete already verified prices.
        keyed = {tuple(r[k] for k in FIELDS[1:]): r for r in previous}
        for row in rows:
            keyed[tuple(row[k] for k in FIELDS[1:])] = dict(row, raw=raw)
        dates[day] = list(keyed.values())
    else:
        dates[day] = [dict(row, raw=raw) for row in rows]
    publish(path, encode(dates))
    print(f"{source} ({feed}): {len(rows)}")
    return len(missing)


def export():
    series = {}
    def add(row):
        key = tuple(row[k] for k in FIELDS)
        entry = series.setdefault(key, {**dict(zip(FIELDS, key)), "points": {}})
        entry["points"][row["date"]] = [row["price"], row["note"], row["url"], row.get("raw", "")]
    historical = json.loads((DATA / "historical.json").read_bytes())
    if not isinstance(historical, list) or not historical:
        raise ValueError("历史价格文件为空或格式有误")
    for row in historical:
        add(row)
    for feed, (source, url, _) in FEEDS.items():
        for day, rows in read_observations(feed).items():
            for row in rows:
                add(dict(row, source=source, date=day, url=url))
    with (PUBLIC / "nebius-daily.csv").open() as f:
        for row in csv.DictReader(f):
            official = row["source"] == "nebius_api"
            add(dict(source="Nebius 现货" if official else "06.ai（Nebius）", model=row["model"],
                     kind="现货日均" if row["regime"] == "dynamic_spot" else "固定可中断挂牌",
                     term="可中断", region=row["region"], product=row["platform"] + " · " + row["sku_id"],
                     price=float(row["spot_usd_per_gpu_hour"]), date=row["date_utc"],
                     note="完整 UTC 日的官方小时报价均值。" if official else "06.ai 日内 15 分钟区间末价均值。",
                     url="https://docs.nebius.com/signup-billing/spot-prices" if official else "https://06.ai/trackers/gpu-spot-prices/"))
    previous = PUBLIC / "gpu-rent.csv"
    if previous.exists():
        with previous.open() as f:
            for row in csv.DictReader(f):
                key = tuple(row[k] for k in FIELDS)
                if key not in series or row["date"] not in series[key]["points"]:
                    raise ValueError(f"构建遗漏已发布观察：{key} {row['date']}")
    records = []
    csvfile = io.StringIO(newline="")
    writer = csv.writer(csvfile, lineterminator="\n")
    writer.writerow([*FIELDS, "date", "usd_per_gpu_hour", "basis", "url", "raw_path"])
    for key, entry in sorted(series.items()):
        points = [[d, *values] for d, values in sorted(entry["points"].items())]
        for point in points:
            writer.writerow([*key, *point])
        records.append(dict(entry, points=points))
    publish(PUBLIC / "gpu-rent.csv", csvfile.getvalue().encode())
    publish(PUBLIC / "gpu-rent.json", encode({"series": records, "status": read_json(DATA / "status.json", {})}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-only", action="store_true")
    args = parser.parse_args()
    failed = 0
    if not args.export_only:
        today = datetime.now(timezone.utc).date().isoformat()
        path = DATA / "status.json"
        status = read_json(path, {})
        for feed in FEEDS:
            try:
                missing = refresh(feed, today)
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
