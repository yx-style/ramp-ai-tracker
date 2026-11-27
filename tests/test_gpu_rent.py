"""Regression checks for independent price feeds and the published archive."""
import csv
import gzip
import hashlib
import json
from contextlib import contextmanager
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_gpu_rent as builder
from gpu_rent_sources import FEEDS, html, rate, together


def validate_contracts(path):
    records = json.loads(path.read_bytes())
    if not isinstance(records, list) or not records:
        raise ValueError("合约表为空或格式错误")
    for row in records:
        for field in ("date", "party", "model", "region", "term", "price", "unit", "basis"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"合约缺少 {field}")
        if not row["links"] or not all(label and url.startswith("https://") for label, url in row["links"]):
            raise ValueError("合约缺少原文链接")


@contextmanager
def price_archive():
    """Two successful feeds plus independent historical and spot observations."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data, public = root / "data/gpu-rent", root / "docs/data"
        feeds = {k: (k, "https://example.com/" + k, json.loads) for k in ("east", "west")}
        with patch.object(builder, "ROOT", root), patch.object(builder, "DATA", data), patch.object(builder, "PUBLIC", public), \
                patch.dict(FEEDS, feeds, clear=True):
            data.mkdir(parents=True)
            public.mkdir(parents=True)
            historical = [dict(rate("H100", 2), date=day, source="历史", url="https://example.com/archive")
                          for day in ("2025-01-01", "2025-02-01")]
            (data / "historical.json").write_text(json.dumps(historical))
            (public / "nebius-daily.csv").write_text(
                "source,model,regime,region,platform,sku_id,spot_usd_per_gpu_hour,date_utc\n"
                "nebius_api,B200,dynamic_spot,US,HGX,1,3,2025-01-02\n")
            (data / "status.json").write_text(json.dumps({k: {"checked": "2025-11-26", "verified": "2025-11-26"}
                                                          for k in feeds}))
            for feed in feeds:
                with patch.object(builder, "download", return_value=json.dumps([rate("B200", 5)]).encode()):
                    builder.refresh(feed, "2025-11-26")
            builder.export()
            yield data, public


class PriceTests(unittest.TestCase):
    def test_archived_feeds_match_all_daily_rows(self):
        for key, (_, _, parse) in FEEDS.items():
            dates = json.loads((builder.DATA / key / "observations.json").read_bytes())
            rows = dates[max(dates)]
            for raw in set(row["raw"] for row in rows):
                with self.subTest(feed=key, raw=raw):
                    body = gzip.decompress((builder.ROOT / raw).read_bytes())
                    self.assertIn(hashlib.sha256(body).hexdigest()[:16], raw)
                    parsed = list(parse(body))
                    builder.verify(parsed)
                    for row in rows:
                        if row["raw"] == raw:
                            self.assertIn({k: v for k, v in row.items() if k != "raw"}, parsed)

    def test_bad_response_and_partial_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feeds = {"demo": ("Test", "https://example.com/prices", json.loads)}
            with patch.object(builder, "ROOT", root), patch.object(builder, "DATA", root / "data"), patch.dict(FEEDS, feeds):
                rows = [rate("NVIDIA H100", 4), rate("NVIDIA B200", 6)]
                date = "2026-11-27"
                path = root / "data/demo/observations.json"
                with patch.object(builder, "download", return_value=json.dumps(rows).encode()):
                    builder.refresh("demo", date)
                original = path.read_bytes()
                with patch.object(builder, "download", return_value=b"[]"), self.assertRaises(ValueError):
                    builder.refresh("demo", date)
                self.assertEqual(path.read_bytes(), original)
                rows[0]["price"] = 4.2
                with patch.object(builder, "download", return_value=json.dumps(rows[:1]).encode()):
                    builder.refresh("demo", date)
                updated = json.loads(path.read_bytes())[date]
                self.assertEqual([r["price"] for r in updated], [4.2, 6])
                self.assertEqual(len(list((path.parent / "raw").glob("*.gz"))), 2)

    def test_failed_feed_does_not_block_next_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            feeds = {"offline": None, "available": None}
            with patch.object(builder, "DATA", data), patch.dict(FEEDS, feeds, clear=True), \
                    patch.object(builder, "refresh", side_effect=[ValueError("HTTP 503"), 0]) as refresh, \
                    patch.object(builder, "export") as export, patch.object(sys, "argv", ["build_gpu_rent.py"]):
                self.assertEqual(builder.main(), 0)
            self.assertEqual([call.args[0] for call in refresh.call_args_list], list(feeds))
            status = json.loads((data / "status.json").read_bytes())
            self.assertIn("error", status["offline"])
            self.assertIn("verified", status["available"])
            export.assert_called_once_with()

    def test_exports_match(self):
        data = json.loads((builder.PUBLIC / "gpu-rent.json").read_bytes())
        with (builder.PUBLIC / "gpu-rent.csv").open() as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(data["series"])
        self.assertEqual(data["status"], json.loads((builder.DATA / "status.json").read_bytes()))
        self.assertEqual(len(rows), sum(len(s["points"]) for s in data["series"]))
        exported = iter(rows)
        for series in data["series"]:
            dates = [p[0] for p in series["points"]]
            self.assertEqual(dates, sorted(set(dates)))
            for day, price, basis, url, raw in series["points"]:
                row = next(exported)
                self.assertEqual([row[k] for k in builder.FIELDS], [series[k] for k in builder.FIELDS])
                self.assertEqual((row["date"], float(row["usd_per_gpu_hour"]), row["basis"], row["url"], row["raw_path"]),
                                 (day, price, basis, url, raw))
                if raw:
                    self.assertTrue((builder.ROOT / raw).is_file())

    def test_together_missing_price_cell_rejects_whole_response(self):
        raw = next((builder.DATA / "together/raw").glob("2026-11-27_*.gz"))
        body = gzip.decompress(raw.read_bytes())
        h100 = [r for r in together(body) if r["model"] == "H100"]
        self.assertEqual([(r["kind"], r["term"], r["price"]) for r in h100],
                         [("可中断挂牌", "可中断", 2.0), ("按需挂牌", "无固定期", 3.99),
                          ("预留挂牌", "3–30 天", 3.69), ("预留挂牌", "31–90 天", 3.45)])
        soup = html(body)
        table = next(t for t in soup.select("table") if "Preemptible" in t.get_text() and "31-90 days" in t.get_text())
        row = next(r for r in table.select("tr") if r.find("td") and "HGX H100" in r.get_text())
        row.find_all("td", recursive=False)[1].decompose()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(builder, "ROOT", root), patch.object(builder, "DATA", root / "data"):
                with patch.object(builder, "download", return_value=body):
                    builder.refresh("together", "2026-11-27")
                path = root / "data/together/observations.json"
                original = path.read_bytes()
                for day in ["2026-11-27", "2026-11-28"]:
                    with patch.object(builder, "download", return_value=soup.encode()), self.assertRaises(ValueError):
                        builder.refresh("together", day)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(len(list((path.parent / "raw").glob("*.gz"))), 1)

    def test_absent_or_shorter_history_keeps_both_previous_exports(self):
        with price_archive() as (data, public):
            history = data / "historical.json"
            rows = json.loads(history.read_bytes())
            paths = [public / "gpu-rent.csv", public / "gpu-rent.json"]
            original = [p.read_bytes() for p in paths]
            history.unlink()
            with self.assertRaises(FileNotFoundError):
                builder.export()
            self.assertEqual(original, [p.read_bytes() for p in paths])
            history.write_text(json.dumps(rows[:1]))
            with self.assertRaisesRegex(ValueError, "遗漏已发布"):
                builder.export()
            self.assertEqual(original, [p.read_bytes() for p in paths])

    def test_corrupt_feed_recovers_while_another_feed_updates(self):
        with price_archive() as (data, public):
            path = data / "east/observations.json"
            original = json.loads(path.read_bytes())
            path.write_text("{invalid")
            with patch.object(builder, "download", return_value=json.dumps([rate("B200", 6)]).encode()):
                builder.refresh("west", "2025-11-27")
            builder.export()
            self.assertEqual(json.loads(path.read_bytes()), original)
            with (public / "gpu-rent.csv").open() as f:
                values = [(r["source"], r["date"], float(r["usd_per_gpu_hour"])) for r in csv.DictReader(f)]
            self.assertIn(("east", "2025-11-26", 5), values)
            self.assertIn(("west", "2025-11-26", 5), values)
            self.assertIn(("west", "2025-11-27", 6), values)

    def test_missing_product_warns_each_day_until_it_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(builder, "ROOT", root), patch.object(builder, "DATA", root / "data"), \
                    patch.dict(FEEDS, {"demo": ("Test", "https://example.com", json.loads)}, clear=True):
                rows = [rate("H100", 4), rate("B200", 6)]
                for i, (returned, missing) in enumerate([(rows, 0), (rows[:1], 1), (rows[:1], 1), (rows, 0)]):
                    with patch.object(builder, "download", return_value=json.dumps(returned).encode()):
                        self.assertEqual(builder.refresh("demo", f"2026-11-{20+i}"), missing)
                dates = json.loads((root / "data/demo/observations.json").read_bytes())
                self.assertEqual([d for d, rs in dates.items() if any(r["model"] == "B200" for r in rs)],
                                 ["2026-11-20", "2026-11-23"])

    def test_all_network_failures_publish_status_and_preserve_prices(self):
        with price_archive() as (_, public):
            original = (public / "gpu-rent.csv").read_bytes()
            with patch.object(builder, "download", side_effect=ValueError("HTTP 503")), patch.object(sys, "argv", ["build"]):
                self.assertEqual(builder.main(), 0)
            new_status = json.loads((public / "gpu-rent.json").read_bytes())["status"]
            self.assertEqual(set(new_status), set(FEEDS))
            self.assertTrue(all(r["error"] and r["verified"] == "2025-11-26" and r["checked"] != r["verified"]
                                for r in new_status.values()))
            self.assertEqual(original, (public / "gpu-rent.csv").read_bytes())

    def test_published_dates_cover_historical_and_daily_inputs(self):
        with (builder.PUBLIC / "gpu-rent.csv").open() as f:
            keys = {tuple(r[k] for k in builder.FIELDS) + (r["date"],) for r in csv.DictReader(f)}
        historical = json.loads((builder.DATA / "historical.json").read_bytes())
        self.assertTrue(historical)
        for r in historical:
            self.assertIn(tuple(r[k] for k in builder.FIELDS) + (r["date"],), keys)
        for feed, (source, _, _) in FEEDS.items():
            dates = json.loads((builder.DATA / feed / "observations.json").read_bytes())
            for date, rows in dates.items():
                for r in rows:
                    self.assertIn((source,) + tuple(r[k] for k in builder.FIELDS[1:]) + (date,), keys)

    def test_contracts_in_pages_checkout(self):
        validate_contracts(builder.PUBLIC / "gpu-contracts.json")

    def test_malformed_contract_file_and_missing_unit_fail_pages_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gpu-contracts.json"
            path.write_text("{")
            with self.assertRaises(ValueError):
                validate_contracts(path)
            row = json.loads((builder.PUBLIC / path.name).read_bytes())[0]
            del row["unit"]
            path.write_text(json.dumps([row]))
            with self.assertRaisesRegex(ValueError, "unit"):
                validate_contracts(path)
        workflow = (builder.ROOT / ".github/workflows/update.yml").read_text()
        package = workflow.split("\n  package:\n")[1].split("\n  deploy:\n")[0]
        self.assertLess(package.index("actions/checkout"), package.index("test_gpu_rent.py"))
        self.assertLess(package.index("test_gpu_rent.py"), package.index("actions/upload-pages-artifact"))

    def test_redirected_robots_enforces_final_rules_on_original_host(self):
        urls = {"https://source.test/robots.txt": (b"", 301, "https://cdn.test/robots"),
                "https://cdn.test/robots": (b"", 307, "https://cdn.test/robots-v2"),
                "https://cdn.test/robots-v2": (b"User-agent: *\nDisallow: /private\n", 200, ""),
                "https://source.test/prices": (b"public", 200, "")}
        builder.robots.cache_clear()
        self.addCleanup(builder.robots.cache_clear)
        with patch.object(builder, "request", side_effect=urls.__getitem__) as request:
            self.assertEqual(builder.download("https://source.test/prices"), b"public")
            with self.assertRaisesRegex(ValueError, "禁止"):
                builder.download("https://source.test/private/prices")
            self.assertEqual([c.args[0] for c in request.call_args_list], list(urls))

    def test_robots_redirect_loop_and_downgrade_fail_closed(self):
        for target in ("https://loop.test/robots.txt", "http://other.test/robots.txt"):
            builder.robots.cache_clear()
            with patch.object(builder, "request", return_value=(b"", 302, target)) as request, self.assertRaises(ValueError):
                builder.download("https://loop.test/prices")
            self.assertLessEqual(request.call_count, 6)

    def test_oracle_archives_contain_only_target_original_items(self):
        from gpu_rent_sources import oracle_gpu_items
        for path in (builder.DATA / "oracle/raw").glob("*.gz"):
            body = gzip.decompress(path.read_bytes())
            self.assertEqual(json.loads(body), oracle_gpu_items(body))
            self.assertEqual(set(json.loads(body)), {"lastUpdated", "items"})
