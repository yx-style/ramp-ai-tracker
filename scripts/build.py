"""Fetch Ramp AI Index token data and build the static tracker's data files.

Outputs (only rewritten when the upstream data changes):
  docs/data/tracker.json          everything the page needs
  docs/data/ramp-makers-weekly.csv
  docs/data/ramp-anthropic-models-weekly.csv
  data/snapshots/<date>.json.gz   raw upstream payload, one per change
  data/dashboard_history.json     Tokenomics table scraped from the dashboard, by week
"""

import csv
import gzip
import hashlib
import html
import json
import math
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DATA = ROOT / "docs" / "data"
SNAPSHOTS = ROOT / "data" / "snapshots"
DASH_HISTORY = ROOT / "data" / "dashboard_history.json"
CALIBRATION = ROOT / "data" / "calibration.json"

TOKEN_URL = "https://ramp.com/data/ai-index/chart-data/tokenVolumes"
DASH_URL = "https://ramp.com/data/ai-index"
UA = {"User-Agent": "Mozilla/5.0 (ramp-ai-tracker; github actions)"}

MAKER_LABELS = {
    "anthropic": "Anthropic", "openai": "OpenAI", "google": "Google", "xai": "xAI",
    "deepseek": "DeepSeek", "moonshot": "Moonshot", "zhipu": "Zhipu", "cursor": "Cursor",
}
# Chart groups: the four largest makers keep their own line, the rest fold into 其他.
GROUPS = ["anthropic", "openai", "google", "xai", "other"]
FAMILIES = [("opus", "Opus"), ("fable", "Fable"), ("sonnet", "Sonnet"),
            ("haiku", "Haiku"), ("mythos", "Mythos"), ("other", "其他")]


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def family_of(key):
    for fam, _ in FAMILIES[:-1]:
        if fam in key:
            return fam
    return "other"


def scrape_dashboard():
    """Pull the Tokenomics table (shares and $/M token prices) off the dashboard page."""
    try:
        raw = fetch(DASH_URL).decode("utf-8", "replace")
    except Exception as e:  # dashboard is a nice-to-have; never fail the build on it
        print("dashboard fetch failed:", e, file=sys.stderr)
        return None
    i = raw.find("Tokenomics")
    if i < 0:
        return None
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw[i:i + 80000])))
    weeks = re.findall(r"Week ending ([A-Z][a-z]{2} \d{1,2}, \d{4})", text)
    if len(weeks) < 2:
        return None

    def iso(s):
        return datetime.strptime(s, "%b %d, %Y").date().isoformat()

    rows = []
    pat = re.compile(
        r"(Share of token spend|Share of token volume|Blended price per million tokens): "
        r"([A-Za-z& ]+?) (\$?[\d.]+%?) (\$?[\d.]+%?) ([+\-]?[\d.]+ ?(?:pts|%))")
    for m in pat.finditer(text):
        metric, who, cur, prev, chg = m.groups()
        num = lambda s: float(s.replace("$", "").replace("%", ""))
        rows.append({"metric": metric, "who": who.strip(), "cur": num(cur), "prev": num(prev),
                     "change": chg.replace(" ", "")})
    if not rows:
        return None
    return {"week_end": iso(weeks[0]), "prev_week_end": iso(weeks[1]), "rows": rows}


def prepare_weekly(rows, required_weeks=()):
    """Select one maker scale for the whole history. Keep the snapshot itself raw."""
    metrics = {"token_spend", "token_volume"}
    weekly = [r for r in rows if r["period_grain"] == "weekly" and r["metric_key"] in metrics]
    weeks = sorted({r["period_end"] for r in weekly})
    if not weeks or not set(required_weeks).issubset(weeks):
        return None
    if any(date.fromisoformat(b) - date.fromisoformat(a) != timedelta(days=7)
           for a, b in zip(weeks, weeks[1:])):
        return None

    models = [r for r in weekly if r["dimension_type"] == "model"]
    totals = defaultdict(float)
    starts = {}
    # Both long-running providers are present throughout Ramp's published series.
    providers = defaultdict(set)
    for r in models:
        week, metric, maker = r["period_end"], r["metric_key"], r.get("model_maker")
        value = r["metric_value"]
        if not maker or value is None or not math.isfinite(value) or value < 0:
            return None
        starts[week] = r["period_start"]
        totals[week, metric, maker] += value
        providers[week, metric].add(maker)
    if any(not {"anthropic", "openai"} <= providers[w, m] for w in weeks for m in metrics):
        return None

    official = [r for r in weekly if r["dimension_type"] == "maker"]
    keys = {(r["period_end"], r["metric_key"], r["series_key"]) for r in official
            if r["metric_value"] is not None and math.isfinite(r["metric_value"])}
    if len(keys) == len(official) and totals.keys() <= keys:
        return models + official, "official"

    makers = [{"period_grain": "weekly", "period_start": starts[w], "period_end": w,
               "metric_key": metric, "dimension_type": "maker", "series_key": maker,
               "series_label": MAKER_LABELS.get(maker, maker.title()), "model_maker": maker,
               "metric_value": value} for (w, metric, maker), value in totals.items()]
    return models + makers, "model_sum"


def main():
    raw = fetch(TOKEN_URL)
    prev_meta = DOCS_DATA / "tracker.json"
    old = json.loads(prev_meta.read_text()) if prev_meta.exists() else {}
    prepared = prepare_weekly(json.loads(raw), old.get("weeks", ()))
    snapshots = sorted(SNAPSHOTS.glob("*.json.gz"))
    archived = None
    if prepared is None:
        for path in reversed(snapshots):
            raw = gzip.decompress(path.read_bytes())
            prepared = prepare_weekly(json.loads(raw), old.get("weeks", ()))
            if prepared is not None:
                archived = path
                print(f"Ramp weekly feed incomplete; using {path.name}")
                break
        else:
            raise ValueError("No complete weekly Ramp data available")
    data, maker_source = prepared
    digest = hashlib.sha256(raw).hexdigest()

    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    DOCS_DATA.mkdir(parents=True, exist_ok=True)

    # Dashboard table history is kept regardless of whether the JSON changed.
    history = json.loads(DASH_HISTORY.read_text()) if DASH_HISTORY.exists() else {}
    dash = scrape_dashboard()
    if dash:
        history[dash["week_end"]] = dash
        DASH_HISTORY.write_text(json.dumps(history, indent=1, sort_keys=True, ensure_ascii=False))

    force = "--force" in sys.argv
    if old.get("source_sha256") == digest and old.get("maker_source") == maker_source and not force and old.get("dashboard") == [history[k] for k in sorted(history)]:
        print("upstream unchanged; nothing to do")
        return

    # Keep one raw snapshot per upstream change so revisions can be audited later.
    if archived:
        earlier = snapshots[:snapshots.index(archived)]
    elif snapshots and raw == gzip.decompress(snapshots[-1].read_bytes()):
        earlier = snapshots[:-1]
    else:
        earlier = snapshots
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S%fZ")
        with gzip.open(SNAPSHOTS / f"{stamp}.json.gz", "wb") as f:
            f.write(raw)

    prev_snapshot = None
    for path in reversed(earlier):
        previous = prepare_weekly(json.loads(gzip.decompress(path.read_bytes())))
        if previous is not None:
            prev_snapshot = (path.name, *previous)
            break

    weeks, week_start = build_weeks(data)
    series = aggregate(data)
    makers = sorted({k for (_, dim, k) in series if dim == "maker"})

    def vec(metric, dim, key):
        s = series.get((metric, dim, key), {})
        return [round(s.get(w, 0.0), 6) for w in weeks]

    maker_out = {k: {"label": MAKER_LABELS.get(k, k.title()),
                     "spend": vec("token_spend", "maker", k),
                     "volume": vec("token_volume", "maker", k)} for k in makers}

    # Anthropic models: family aggregates and per-model detail.
    labels = {}
    for r in data:
        if r["dimension_type"] == "model" and r["model_maker"] == "anthropic":
            labels[r["series_key"]] = r["series_label"]
    fam = {f: {"spend": [0.0] * len(weeks), "volume": [0.0] * len(weeks)} for f, _ in FAMILIES}
    models = {}
    for key, label in labels.items():
        sp, vo = vec("token_spend", "model", key), vec("token_volume", "model", key)
        models[key] = {"label": label, "family": family_of(key), "spend": sp, "volume": vo}
        f = family_of(key)
        for i in range(len(weeks)):
            fam[f]["spend"][i] += sp[i]
            fam[f]["volume"][i] += vo[i]
    for f in fam.values():
        f["spend"] = [round(x, 6) for x in f["spend"]]
        f["volume"] = [round(x, 6) for x in f["volume"]]

    # Only ship per-model series that matter in the past year, to keep the page light.
    recent = slice(max(0, len(weeks) - 52), len(weeks))
    keep = {k: m for k, m in models.items() if sum(m["spend"][recent]) > 0 or sum(m["volume"][recent]) > 0}

    out = {
        "source_sha256": digest,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "source": {"tokens": TOKEN_URL, "dashboard": DASH_URL},
        "weeks": weeks,
        "week_start": week_start,
        "makers": maker_out,
        "maker_source": maker_source,
        "groups": GROUPS,
        "families": {f: {"label": lab, **fam[f]} for f, lab in FAMILIES},
        "family_order": [f for f, _ in FAMILIES],
        "anthropic_models": keep,
        "dashboard": [history[k] for k in sorted(history)],
        "dashboard_weeks": sorted(history),
        "revisions": revisions(prev_snapshot, data, maker_source),
        "usd": calibrate(data),
    }
    (DOCS_DATA / "tracker.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    write_csvs(weeks, week_start, maker_out, keep, out["usd"])
    print(f"built {len(weeks)} weeks through {weeks[-1]}; makers={makers}")


def calibrate(data):
    """Dollars and tokens per maker-level index point, fitted on the archived dollar payload.

    Weeks are spread evenly over their days so the archived (Sat-Fri) and current (Mon-Sun) bins
    can be compared over identical calendar months. Refitted on every build because Ramp rescales
    the index whenever a new weekly high appears.
    """
    if not CALIBRATION.exists():
        return None
    cal = json.loads(CALIBRATION.read_text())
    idx = {"token_spend": defaultdict(float), "token_volume": defaultdict(float)}
    for r in data:
        if r["dimension_type"] != "maker" or r["metric_key"] not in idx:
            continue
        s, e = date.fromisoformat(r["period_start"]), date.fromisoformat(r["period_end"])
        n = (e - s).days + 1
        for i in range(n):
            idx[r["metric_key"]][(s + timedelta(i)).strftime("%Y-%m")] += float(r["metric_value"] or 0) / n
    out = {"fetched_on": cal["fetched_on"], "window": cal["window"]}
    for metric, key, name in (("token_spend", "spend_usd", "usd_per_spend_point"),
                              ("token_volume", "tokens", "tokens_per_volume_point")):
        months = [m for m in cal["months"] if idx[metric].get(m)]
        if not months:
            return None
        f = sum(cal["months"][m][key] for m in months) / sum(idx[metric][m] for m in months)
        per = [cal["months"][m][key] / idx[metric][m] / f - 1 for m in months]
        errors = [idx[metric][m] * f / cal["months"][m][key] - 1 for m in months]
        out[name + "_monthly_error"] = [min(errors), max(errors)]
        out[name] = f
        out[name.replace("per", "spread")] = [round(min(per), 4), round(max(per), 4)]
    return out


def build_weeks(data):
    ends = {}
    for r in data:
        if r["period_grain"] == "weekly":
            ends[r["period_end"]] = r["period_start"]
    weeks = sorted(ends)
    return weeks, [ends[w] for w in weeks]


def aggregate(data):
    series = defaultdict(lambda: defaultdict(float))
    for r in data:
        if r["period_grain"] != "weekly":
            continue
        series[(r["metric_key"], r["dimension_type"], r["series_key"])][r["period_end"]] += float(r["metric_value"] or 0)
    return series


def shares(data, metric):
    """Anthropic's share of all makers, by week. Shares survive Ramp's max=100 rescaling."""
    tot, ant = defaultdict(float), defaultdict(float)
    for r in data:
        if r["dimension_type"] == "maker" and r["metric_key"] == metric:
            tot[r["period_end"]] += float(r["metric_value"] or 0)
            if r["series_key"] == "anthropic":
                ant[r["period_end"]] += float(r["metric_value"] or 0)
    return {w: ant[w] / tot[w] for w in tot if tot[w]}


def revisions(prev_snapshot, data, maker_source):
    """Weeks where Anthropic's share moved between the previous snapshot and this one."""
    if not prev_snapshot:
        return None
    name, old, old_source = prev_snapshot
    out = []
    for metric, label in (("token_spend", "支出份额"), ("token_volume", "用量份额")):
        a, b = shares(old, metric), shares(data, metric)
        for w in sorted(set(a) & set(b)):
            d = (b[w] - a[w]) * 100
            if abs(d) >= 0.1:
                out.append({"week": w, "metric": label, "old": round(a[w] * 100, 2),
                            "new": round(b[w] * 100, 2), "delta_pts": round(d, 2)})
    return {"compared_with": name.replace(".json.gz", ""), "maker_source_changed": old_source != maker_source, "changes": out}


def write_csvs(weeks, week_start, makers, models, usd):
    with open(DOCS_DATA / "ramp-makers-weekly.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["week_start", "week_end", "maker", "token_spend_index", "token_volume_index",
                    "token_spend_usd_est", "token_volume_est"])
        fs = usd["usd_per_spend_point"] if usd else None
        fv = usd["tokens_per_volume_point"] if usd else None
        for k, m in makers.items():
            for i, wk in enumerate(weeks):
                w.writerow([week_start[i], wk, k, m["spend"][i], m["volume"][i],
                            round(m["spend"][i] * fs) if fs else "", round(m["volume"][i] * fv) if fv else ""])
    with open(DOCS_DATA / "ramp-anthropic-models-weekly.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["week_start", "week_end", "model", "family", "token_spend_index", "token_volume_index"])
        for k, m in models.items():
            for i, wk in enumerate(weeks):
                if m["spend"][i] or m["volume"][i]:
                    w.writerow([week_start[i], wk, k, m["family"], m["spend"][i], m["volume"][i]])


if __name__ == "__main__":
    main()
