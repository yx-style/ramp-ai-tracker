"""Weekly source selection. Frozen files are append-only, ordered by effective date."""

import json
from datetime import date, timedelta
from pathlib import Path

ARCHIVE = Path(__file__).resolve().parents[1] / "data" / "calibers"
TABLES = ("makers", "families", "anthropic_models")
METRICS = ("spend", "volume")


def next_day(day):
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def freeze(view, start):
    end = view["weeks"][-1]
    if start > end:
        return
    record = {key: view[key] for key in (*TABLES, "weeks", "week_start", "usd", "maker_source", "source_sha256")}
    record.update(start=start, end=end)
    with (ARCHIVE / f"{start}_{view['maker_source']}.json").open("x") as file:
        json.dump(record, file, ensure_ascii=False, separators=(",", ":"))


def compose(live):
    records = [json.loads(p.read_text()) for p in sorted(ARCHIVE.glob("*.json"))]
    start = next_day(records[-1]["end"]) if records else live["week_start"][0]
    periods = records + [dict(live, start=start, end=None)]
    history = []
    selection = {}
    priorities = {}
    for n, period in enumerate(periods):
        if n and period["start"] != next_day(periods[n-1]["end"]):
            raise ValueError("Gap or overlap in caliber archive")
        history.append({k: period[k] for k in ("start", "end", "maker_source", "usd", "source_sha256")})
        for i, (ws, we) in enumerate(zip(period["week_start"], period["weeks"])):
            # Prefer official maker rows wherever available. For equally detailed
            # sources preserve the vintage that was current during this week.
            effective = ws >= period["start"] and (period["end"] is None or we <= period["end"])
            priority = (period["maker_source"] == "official", effective, -n)
            if we not in priorities or priority > priorities[we]:
                selection[we] = (n, i)
                priorities[we] = priority
    weeks = sorted(selection)
    if set(weeks) != set(live["weeks"]) or any(
        (date.fromisoformat(b) - date.fromisoformat(a)).days != 7 for a, b in zip(weeks, weeks[1:])
    ):
        raise ValueError("Incomplete caliber coverage")
    slots = [selection[w] for w in weeks]
    shared = {"calibers": history}
    stitched = dict(live, **shared, weeks=weeks, caliber_by_week=[n for n, _ in slots],
                    week_start=[periods[n]["week_start"][i] for n, i in slots])
    if len({n for n, _ in slots}) > 1:
        stitched.update(maker_source="by_week", usd=None)
    else:
        sole = periods[slots[0][0]]
        stitched.update(maker_source=sole["maker_source"], usd=sole["usd"])
    for table in TABLES:
        rows = {}
        for period in periods:
            for key, row in period[table].items():
                rows[key] = {k: v for k, v in row.items() if k not in METRICS}
        for key, row in rows.items():
            for metric in METRICS:
                row[metric] = [periods[n][table][key][metric][i] if key in periods[n][table] else 0
                               for n, i in slots]
        stitched[table] = rows
    latest = dict(live, **shared, caliber_by_week=[len(records)] * len(live["weeks"]))
    return stitched, latest
