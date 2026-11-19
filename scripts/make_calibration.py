"""One-off: turn an archived dollar-denominated tokenVolumes payload into monthly calibration totals.

Ramp's endpoint returned dollars and raw token counts until early November 2026 and switched to
indices afterwards. Usage: python3 scripts/make_calibration.py <archived.json[.gz]> <fetched-on YYYY-MM-DD>
"""

import gzip
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

WINDOW = (date(2026, 1, 1), date(2026, 9, 30))


def daily(rows, metric):
    out = defaultdict(float)
    for r in rows:
        if r["metric_key"] != metric:
            continue
        s, e = date.fromisoformat(r["period_start"]), date.fromisoformat(r["period_end"])
        n = (e - s).days + 1
        for i in range(n):
            out[s + timedelta(i)] += float(r["metric_value"] or 0) / n
    return out


def main(path, fetched_on):
    raw = gzip.open(path).read() if path.endswith(".gz") else open(path, "rb").read()
    rows = json.loads(raw)
    cal = {"fetched_on": fetched_on, "window": [WINDOW[0].isoformat(), WINDOW[1].isoformat()], "months": {}}
    for metric, key in (("token_spend", "spend_usd"), ("token_volume", "tokens")):
        d = daily(rows, metric)
        for day, val in d.items():
            if WINDOW[0] <= day <= WINDOW[1]:
                m = cal["months"].setdefault(day.strftime("%Y-%m"), {"spend_usd": 0.0, "tokens": 0.0})
                m[key] += val
    out = Path(__file__).resolve().parent.parent / "data" / "calibration.json"
    out.write_text(json.dumps(cal, indent=1, sort_keys=True))
    print(out, {k: round(v["spend_usd"] / 1e6, 1) for k, v in sorted(cal["months"].items())})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
