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
from gpu_rent_sources import FEEDS, rental_facts

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/gpu-rent"
PUBLIC = ROOT / "docs/data"
UA = "RampGpuTracker/1.0 (+https://github.com/yx-style/ramp-ai-tracker)"
META = ("effective_date", "announced_price", "configuration", "partNumber")
# SAVRN publishes reuse terms for the data, but no public GPU download endpoint.
ARCHIVE_ONLY = {"impossible-savrn"}
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
        return ""
    elif feed == "impossible-rental":
        body = encode([r for r in rental_facts(body) if r["fetched_at"][:10] == day])
    elif feed == "impossible-savrn":
        fields = ("sku", "gpus", "nodes", "region", "months", "usd_per_gpu_hour", "observed_date", "source")
        body = encode([{k: r[k] for k in fields} for r in json.loads(body) if r["observed_date"] == day])
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
    snapshots = DATA / "backfill" / "observations.json.gz"
    if snapshots.exists():
        rows.extend(json.loads(gzip.decompress(snapshots.read_bytes())))
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
            if row["source"] == "Oracle" and "oracle" in FEEDS and row["url"] == FEEDS["oracle"][1]:
                feed = "oracle"
            elif not feed or not raw.name.startswith(row["date"] + "_") or row["source"] != FEEDS[feed][0]:
                continue
            if (row["source"], observation_key(row), row["date"]) in historical:
                continue
            quote = {k: row[k] for k in FIELDS[1:]}
            quote.update(price=json.loads(row["usd_per_gpu_hour"]), note=row["basis"], url=row["url"])
            if feed != "oracle":
                quote["raw"] = row["raw_path"]
            else:
                # Exports predating the explicit field stored the part number in the product.
                quote["partNumber"] = row["product"].rsplit(" · ", 1)[-1]
            for field in META:
                if row.get(field):
                    quote[field] = float(row[field]) if field == "announced_price" else row[field]
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
            if feed == "oracle" and any(row.get("raw") or not row.get("partNumber") for row in rows):
                raise ValueError("Oracle 归档仅允许含料号的报价事实")
            if any(not isinstance(row.get("note"), str) or
                   (feed != "oracle" and not (isinstance(row.get("raw"), str) and row["raw"])) for row in rows):
                raise ValueError("观察缺少说明或快照路径")
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        if previous is None:
            raise
        if not previous:
            if not path.exists() and not any((path.parent / "raw").glob("*.gz")):
                return {}
            raise
        dates = {}

    @lru_cache(maxsize=None)
    def archived_quotes(raw):
        body = gzip.decompress((ROOT / raw).read_bytes())
        return {observation_key(r): r for r in FEEDS[feed][2](body)}

    restored = 0
    for day, published in (previous or {}).items():
        rows = dates.setdefault(day, [])
        existing = {observation_key(row): row for row in rows}
        for quote in published:
            row = existing.get(observation_key(quote))
            if feed in ("impossible-savrn", "impossible-rental") and (row is None or not row.get("observed_date")):
                facts = archived_quotes(quote["raw"])[observation_key(quote)]
                if facts["observed_date"] != day or facts["price"] != quote["price"]:
                    raise ValueError(f"{feed} 的历史报价或核查日与快照不符")
                quote = dict(quote, observed_date=facts["observed_date"])
                if row is not None:
                    row["observed_date"] = facts["observed_date"]
                    restored += 1
            if row is not None:
                # Old observations did not store the query URL. Preserve it when an endpoint changes.
                if "url" not in row and row.get("raw") == quote.get("raw") and quote["url"] != FEEDS[feed][1]:
                    row["url"] = quote["url"]
                    restored += 1
                continue
            if feed != "oracle" and not (ROOT / quote["raw"]).is_file():
                raise ValueError(f"还原 {feed} 时找不到快照 {quote['raw']}")
            rows.append(quote)
            restored += 1
        verify(rows)
    if restored:
        publish(path, encode(dates))
        warn(f"{feed}：从已发布数据还原 {restored} 条观察或查询链接")
    return dates


def refresh(feed, day, previous=None, *, body=None):
    source, url, parse = FEEDS[feed]
    if body is None:
        if feed in ARCHIVE_ONLY:
            raise ValueError("此来源仅接受已获许可的数据文件")
        body = download(url)
    candidates = list(parse(body))
    rows = [r for r in candidates if r.get("observed_date", day) == day]
    if candidates and not rows:
        raise ValueError("第三方价格表尚未核实当日读数")
    verify(rows)
    path = DATA / feed / "observations.json"
    dates = read_observations(feed, previous if previous is not None else published_daily_rows().get(feed, {}))
    raw = archive(feed, body, day)
    evidence = {"raw": raw, "url": url} if raw else {"url": url}
    prior = {tuple(r[k] for k in FIELDS[1:]) for rows_on_day in dates.values() for r in rows_on_day}
    observed = {tuple(r[k] for k in FIELDS[1:]) for r in rows}
    missing = prior - observed
    previous = dates.get(day)
    if previous:
        # Missing rows in a same-day retry must not delete already verified prices.
        keyed = {tuple(r[k] for k in FIELDS[1:]): r for r in previous}
        for row in rows:
            keyed[tuple(row[k] for k in FIELDS[1:])] = dict(row, **evidence)
        dates[day] = list(keyed.values())
    else:
        dates[day] = [dict(row, **evidence) for row in rows]
    publish(path, encode(dates))
    print(f"{source} ({feed}): {len(rows)}")
    return len(missing)


def collect_series(previous=None):
    series = {}
    official_days = set()
    evidence_days = {}
    def add(row):
        key = tuple(row[k] for k in FIELDS)
        day = row["date"]
        if row.get("observed_date", day) != day:
            raise ValueError(f"报价日期与独立观察日不符：{key} {day}")
        # Replaying the same evidence cannot create another observation.
        if row.get("raw"):
            evidence = key, row["url"], row["raw"]
            if evidence_days.setdefault(evidence, day) != day:
                raise ValueError(f"同一证据重复计作观察日：{key} {day}")
        entry = series.setdefault(key, {**dict(zip(FIELDS, key)), "points": {}})
        point = [row["price"], row["note"], row["url"], row.get("raw", "")]
        meta = {k: row[k] for k in META if row.get(k) is not None and row[k] != ""}
        point.append(meta)
        old = entry["points"].get(row["date"])
        if old is not None and old[0] == point[0] and row["source"] == "Impossible Cloud" and row["kind"] == "预留挂牌":
            # Multiple feeds may verify one listing on the same day. Prefer the
            # vendor, then the licensed manual observation when prices agree.
            sources = ["console.impossiblecloud.com", "savrn.com", "raw.githubusercontent.com"]
            prior_host, new_host = (urlsplit(p[2]).hostname for p in (old, point))
            if prior_host in sources and new_host in sources and prior_host != new_host:
                if sources.index(prior_host) < sources.index(new_host):
                    return
                old = None
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
    directory, shards = [], {}
    csvfile = io.StringIO(newline="")
    writer = csv.writer(csvfile, lineterminator="\n")
    writer.writerow([*FIELDS, "date", "usd_per_gpu_hour", "basis", "url", "raw_path",
                     "observed_date", *META])
    for key, entry in sorted(series.items()):
        points = [[d, *values] for d, values in sorted(entry["points"].items())]
        for day, price, note, url, raw, meta in points:
            writer.writerow([*key, day, price, note, url, raw, day, *(meta.get(k, "") for k in META)])
        # Immutable content URLs prevent mixed directory/detail revisions after a deployment.
        body = encode(points)
        filename = "gpu-rent-series/" + hashlib.sha256(body).hexdigest()[:24] + ".json"
        shards[filename] = body
        latest = points[-1]
        directory.append({**dict(zip(FIELDS, key)), "count": len(points), "first": points[0][0],
                          "last": latest[0], "price": latest[1], "url": latest[3], "file": filename})
    return {"gpu-rent.csv": csvfile.getvalue().encode(),
            "gpu-rent.json": encode({"series": directory, "status": read_json(DATA / "status.json", {})}),
            **shards}


def validate_exports():
    # Rebuild from inputs only. Neither publication is a reference for the other.
    series, _ = collect_series()
    files = render_exports(series)
    for name, expected in files.items():
        if (PUBLIC / name).read_bytes() != expected:
            raise ValueError(f"{name} 与历史、日更观察或 Nebius 日线不一致")
    if any(path.relative_to(PUBLIC).as_posix() not in files for path in (PUBLIC / "gpu-rent-series").glob("*.json")):
        raise ValueError("公开目录含未引用的走势分片")


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
    files = render_exports(series)
    for name, body in files.items():
        if name != "gpu-rent.json":
            publish(PUBLIC / name, body)
    publish(PUBLIC / "gpu-rent.json", files["gpu-rent.json"])
    for path in (PUBLIC / "gpu-rent-series").glob("*.json"):
        if path.relative_to(PUBLIC).as_posix() not in files:
            path.unlink()


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
            if feed in ARCHIVE_ONLY:
                continue
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
    if failed == len(FEEDS.keys() - ARCHIVE_ONLY):
        warn("本轮所有来源抓取失败，已更新检查状态并保留历史价格")
    return 0


if __name__ == "__main__":
    sys.exit(main())
