"""Archive completed UTC hours from the public Nebius billing API."""
import argparse
import csv
import fcntl
import gzip
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data/nebius/raw"
PUBLIC = ROOT / "docs/data"
API = "https://api.nebius.cloud/billing/v1alpha1/spot-price"
PRICES = "https://nebius.com/prices"
HOUR = timedelta(hours=1)
LABELS = {"b300": "B300", "b200": "B200", "h200": "H200", "h100": "H100",
          "rtx6000": "RTX PRO 6000"}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def instant(timestamp):
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def fetch(url):
    return subprocess.check_output(["curl", "-fsSL", "--retry", "3", "--max-time", "90", url])


def pages(endpoint, query):
    records = []
    responses = []
    seen = set()
    while True:
        url = endpoint + "?" + urlencode(query, doseq=True)
        response = json.loads(fetch(url))
        responses.append({"url": url, "body": response})
        if not response["items"]:
            raise ValueError(f"Empty API response for {endpoint}")
        records.extend(response["items"])
        token = response.get("nextPageToken")
        if not token:
            return records, responses
        if token in seen:
            raise ValueError("Repeated API page token")
        seen.add(token)
        query["pageToken"] = token


def rate_rows():
    html = fetch(PRICES).decode()
    script = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not script:
        raise ValueError("官网未返回牌价数据")
    state = json.loads(script[1])["props"]["pageProps"]["__APOLLO_STATE__"]
    for page in state.values():
        if not isinstance(page, dict) or not isinstance(page.get("content"), str):
            continue
        for block in json.loads(page["content"]).get("blocks", []):
            if block.get("title") == "NVIDIA GPU Instances":
                header, *rows = block["table"]["content"]
                return rows, header.index("On-demand, GPU-hour")
    raise ValueError("官网缺少 GPU 牌价表")


def warn(message):
    # Escape annotation data so source text stays within this warning.
    text = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning::{text}", file=sys.stderr)


def update_rates(today):
    path = PUBLIC / "nebius.json"
    data = json.loads(path.read_bytes())
    card = data["onDemand"]
    prices = card["usd"]
    details = card.setdefault("byModel", {})
    models = sorted(set(prices) | {s["model"] for s in data["series"]})
    if all(details.get(m, {}).get("checked") == today and
           details[m].get("status") == "verified" for m in models):
        return
    try:
        rows, col = rate_rows()
        error = None
    except (OSError, ValueError, LookupError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        rows, col, error = [], 0, f"官网牌价读取失败（{exc}）"
    for model in models:
        detail = details.setdefault(model, {"checked": card.get("checked") if model in prices else None})
        name = ("NVIDIA HGX " if model in ("B300", "B200", "H200", "H100") else "NVIDIA ") + model
        matches = [r for r in rows if isinstance(r, list) and r and r[0] == name]
        reason = error
        if not reason and len(matches) != 1:
            reason = "官网缺少该型号" if not matches else "官网有多行同名报价"
        if not reason:
            row = matches[0]
            raw = row[col] if len(row) > col and isinstance(row[col], str) else ""
            match = re.fullmatch(r"\$(\d+(?:\.\d+)?)", raw.strip())
            value = float(match[1]) if match else 0
            if value <= 0 or not math.isfinite(value):
                reason = "官网未列出单一固定价" + (f"（{raw.strip()}）" if raw.strip() else "")
        detail["attempted"] = today
        if reason:
            detail.update(status="unverified", reason=reason)
            warn(f"{model} 牌价未核实，{reason}；最近核实日期 {detail['checked']}")
        else:
            if model in prices and prices[model] != value:
                detail["lastChange"] = {"observed": today, "from": prices[model], "to": value}
            prices[model] = value
            detail.update(status="verified", checked=today)
            detail.pop("reason", None)
    # Keep the legacy summary date conservative for older clients.
    dates = [details[m]["checked"] for m in models]
    card["checked"] = min(dates) if all(dates) else None
    publish(path, encode(data))


def encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()


def publish(path, content):
    if path.exists() and path.read_bytes() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp.write(content)
    os.chmod(temp.name, 0o644)
    os.replace(temp.name, path)


def accepts_hour(start):
    query = {"filters.startTime": iso(start), "filters.endTime": iso(start + HOUR),
             "aggregation.granularity": "GRANULARITY_HOUR_1",
             "aggregation.method": "METHOD_AVG_PRICE", "pageSize": 1}
    url = API + "/list-series?" + urlencode(query)
    body, status = subprocess.check_output([
        "curl", "-sSL", "--retry", "3", "--max-time", "90", "-w", "\n%{http_code}", url
    ]).rsplit(b"\n", 1)
    response = json.loads(body)
    if status == b"200" and (isinstance(response.get("items"), list) or "snapshotTime" in response) and "code" not in response:
        return True
    errors = [v for detail in response.get("details", [])
              for v in detail.get("badRequest", {}).get("violations", [])]
    if status == b"400" and any(v.get("field") in ("filters", "filters.startTime") and
                                "within the last 30 days" in v.get("message", "") for v in errors):
        return False
    raise ValueError(f"Unexpected response probing retention at {start}: {status!r}, {response}")


def earliest_hour(end):
    # The published retention is 30 days. Probe nearby whole hours because the
    # live service can accept an additional partial day.
    step = timedelta(days=1)
    first = end - 30 * step
    if accepts_hour(first):
        while accepts_hour(first - step):
            first -= step
        rejected = first - step
    else:
        rejected = first
        first += step
        if not accepts_hour(first):
            raise ValueError("Could not establish the API retention boundary")
    while first - rejected > HOUR:
        mid = rejected + ((first - rejected) // HOUR // 2) * HOUR
        if accepts_hour(mid):
            first = mid
        else:
            rejected = mid
    return first


def find_gaps(series, retention_start):
    gaps = []
    for s in series:
        times = [instant(t) for t, _ in s["points"]]
        for prev, nxt in zip(times, times[1:]):
            hours = (nxt - prev) // HOUR - 1
            if hours:
                end = nxt - HOUR
                expired = max(0, (min(end, retention_start) - prev) // HOUR)
                gaps.append({"skuId": s["skuId"], "start": iso(prev), "end": iso(end),
                             "hours": hours, "outsideRetentionHours": expired})
    return gaps


def archive_spot(now):
    end = now.replace(minute=0, second=0, microsecond=0)
    today = now.date().isoformat()
    path = PUBLIC / "nebius.json"
    old = json.loads(path.read_bytes()) if path.exists() else {"series": []}
    if not path.exists() and ARCHIVE.exists() and any(ARCHIVE.rglob("*.gz")):
        raise ValueError("Restore merged history from git before updating")
    series = {s["skuId"]: s for s in old["series"]}
    current, raw_current = pages(API, {"pageSize": 100})
    for item in current:
        quote = item["price"]
        if (not item["region"] or not item["computeInstance"]["platform"]
                or quote["currency"].lower() != "usd" or quote["unitTitle"] != "GPU hour"
                or not math.isfinite(float(quote["value"])) or float(quote["value"]) < 0):
            raise ValueError("Invalid current GPU quote")
    ids = [s["skuId"] for s in current]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate SKU in current prices")
    # Reconcile the entire available history daily; between scans overlap 24h
    # with the previous fetch to pick up recent revisions.
    scan = (old.get("lastFullScan") != today or "retentionStart" not in old
            or any(i not in series for i in ids))
    if not scan:
        oldest_tail = min(instant(series[i]["points"][-1][0]) for i in ids)
        scan = oldest_tail < end - timedelta(days=30)
    start = earliest_hour(end) if scan else min(oldest_tail, end - 24 * HOUR)
    changes = []
    added = 0
    # Unfiltered history includes retired platforms that still have observations.
    rows, raw = pages(API + "/list-series", {
        "filters.startTime": iso(start), "filters.endTime": iso(end),
        "aggregation.granularity": "GRANULARITY_HOUR_1",
        "aggregation.method": "METHOD_AVG_PRICE", "pageSize": 100})
    got = {s["skuId"] for s in rows}
    if set(ids) - got or len(got) != len(rows):
        raise ValueError("Missing or duplicate series")
    for row in rows:
        sku = row["skuId"]
        platform = row["computeInstance"]["platform"]
        if row["currency"].lower() != "usd" or row["unitTitle"] != "GPU hour" or not row["points"]:
            raise ValueError(f"Invalid units or empty points for {sku}")
        model = next((v for k, v in LABELS.items() if k in platform.split("-")), platform)
        s = series.setdefault(sku, {"skuId": sku, "region": row["region"], "platform": platform,
                                    "model": model, "points": []})
        if (s["region"], s["platform"]) != (row["region"], platform):
            raise ValueError(f"SKU metadata changed for {sku}")
        points = dict(s["points"])
        previous = None
        for point in row["points"]:
            t = instant(point["timestamp"])
            value = float(point["value"])
            if (not start < t <= end or t.minute or t.second or t.microsecond
                    or not math.isfinite(value) or value < 0 or previous and t <= previous):
                raise ValueError(f"Invalid hourly point for {sku} at {t}")
            previous = t
            key = iso(t)
            if key not in points:
                added += 1
            elif points[key] != value:
                changes.append({"skuId": sku, "hourEnd": key, "old": points[key], "new": value})
            points[key] = value
        if sku in ids and previous != end:
            raise ValueError(f"Incomplete tail for {sku}")
        s["points"] = sorted(points.items())
    result = {"unit": "USD/GPU-hour", "timestamp": "interval end UTC",
              "onDemand": old.get("onDemand", {"source": PRICES, "usd": {}, "checked": None}),
              "retentionStart": iso(start) if scan else old["retentionStart"],
              "lastFullScan": today if scan else old["lastFullScan"],
              "revisions": old.get("revisions", []),
              "series": sorted(series.values(), key=lambda s: (s["model"], s["region"], s["skuId"]))}
    result["gaps"] = find_gaps(result["series"], instant(result["retentionStart"]))
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["sku_id", "model", "region", "platform", "hour_end_utc", "spot_usd_per_gpu_hour"])
    for s in result["series"]:
        for t, value in s["points"]:
            writer.writerow([s["skuId"], s["model"], s["region"], s["platform"], t, value])
    if added or changes:
        # Capture date and requested bounds keep every original response retrievable.
        archive = encode({"current": raw_current, "history": raw})
        digest = hashlib.sha256(archive).hexdigest()[:16]
        filename = f"{end:%Y%m%dT%H}Z-{digest}.json.gz"
        raw_path = Path(today) / filename
        publish(ARCHIVE / raw_path, gzip.compress(archive, mtime=0))
        for change in changes:
            change.update(observedAt=iso(now), archive=str(raw_path))
        result["revisions"].extend(changes)
    publish(PUBLIC / "nebius.csv", output.getvalue().encode())
    publish(path, encode(result))
    for gap in result["gaps"]:
        warn(f"{gap['skuId']} missing {gap['hours']} hours [{gap['start']}, {gap['end']}); {gap['outsideRetentionHours']} outside retention")
    print(f"{len(changes)} revised hours; scanned from {iso(start)}")
    print(f"{added} new hours, {sum(len(s['points']) for s in series.values())} total SKU-hours")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("spot", "rates", "all"), default="all", nargs="?")
    stage = parser.parse_args().stage
    lock = hashlib.sha256(str(ROOT).encode()).hexdigest()
    with open(Path(tempfile.gettempdir()) / f"nebius-{lock}.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        now = datetime.now(timezone.utc)
        if stage in ("spot", "all"):
            archive_spot(now)
        if stage in ("rates", "all"):
            update_rates(now.date().isoformat())
