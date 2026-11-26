"""Archive completed UTC hours from the public Nebius billing API."""
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


def rates():
    html = fetch(PRICES).decode()
    script = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    state = json.loads(script[1])["props"]["pageProps"]["__APOLLO_STATE__"]
    for page in state.values():
        if not isinstance(page, dict) or not isinstance(page.get("content"), str):
            continue
        for block in json.loads(page["content"]).get("blocks", []):
            if block.get("title") != "NVIDIA GPU Instances":
                continue
            header, *rows = block["table"]["content"]
            col = header.index("On-demand, GPU-hour")
            result = {}
            for row in rows:
                match = re.fullmatch(r"\$(\d+(?:\.\d+)?)", row[col].strip())
                if match:
                    result[row[0].removeprefix("NVIDIA ").removeprefix("HGX ")] = float(match[1])
            if not result or any(not math.isfinite(v) or v <= 0 for v in result.values()):
                raise ValueError("Empty GPU rate card")
            return result
    raise ValueError("GPU price table unavailable")


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


def build():
    now = datetime.now(timezone.utc)
    end = now.replace(minute=0, second=0, microsecond=0)
    earliest = end - timedelta(days=30) + HOUR
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
    # New SKUs need the full window. Existing ones resume at their last interval end.
    starts = [instant(series[i]["points"][-1][0]) if i in series else earliest for i in ids]
    start = max(earliest, min(min(starts), end - HOUR))
    raw = []
    added = 0
    if start < end:
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
                        or not math.isfinite(value) or value < 0 or previous and t != previous + HOUR):
                    raise ValueError(f"Invalid hourly point for {sku} at {t}")
                if previous is None and sku in ids and points and t > max(start, instant(s["points"][-1][0])) + HOUR:
                    raise ValueError(f"Missing initial hours for {sku}")
                previous = t
                if iso(t) not in points:
                    points[iso(t)] = value
                    added += 1
            if sku in ids and previous != end:
                raise ValueError(f"Incomplete tail for {sku}")
            s["points"] = sorted(points.items())
    checked = now.date().isoformat()
    price = old.get("onDemand", {})
    if price.get("checked") != checked:
        price = {"source": PRICES, "checked": checked, "usd": rates()}
    result = {"unit": "USD/GPU-hour", "timestamp": "interval end UTC", "onDemand": price,
              "series": sorted(series.values(), key=lambda s: (s["model"], s["region"], s["skuId"]))}
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["sku_id", "model", "region", "platform", "hour_end_utc", "spot_usd_per_gpu_hour"])
    for s in result["series"]:
        for t, value in s["points"]:
            writer.writerow([s["skuId"], s["model"], s["region"], s["platform"], t, value])
    payload = encode(result)
    if added:
        # Capture date and requested bounds keep every original response retrievable.
        archive = encode({"current": raw_current, "history": raw})
        digest = hashlib.sha256(archive).hexdigest()[:16]
        filename = f"{end:%Y%m%dT%H}Z-{digest}.json.gz"
        publish(ARCHIVE / checked / filename, gzip.compress(archive, mtime=0))
    publish(PUBLIC / "nebius.csv", output.getvalue().encode())
    publish(path, payload)
    print(f"{added} new hours, {sum(len(s['points']) for s in series.values())} total SKU-hours")


if __name__ == "__main__":
    lock = hashlib.sha256(str(ROOT).encode()).hexdigest()
    with open(Path(tempfile.gettempdir()) / f"nebius-{lock}.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        build()
