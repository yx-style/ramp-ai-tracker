"""Regression checks for independent price feeds and the published archive."""
import csv
import os
import shutil
import subprocess
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
                            self.assertIn({k: v for k, v in row.items() if k not in ("raw", "url")}, parsed)

    def test_bad_response_and_partial_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feeds = {"demo": ("Test", "https://example.com/prices", json.loads)}
            with patch.object(builder, "ROOT", root), patch.object(builder, "DATA", root / "data"), patch.object(builder, "PUBLIC", root / "public"), patch.dict(FEEDS, feeds):
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
            with patch.object(builder, "DATA", data), patch.object(builder, "PUBLIC", data), patch.dict(FEEDS, feeds, clear=True), \
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
            with patch.object(builder, "ROOT", root), patch.object(builder, "DATA", root / "data"), patch.object(builder, "PUBLIC", root / "public"):
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
            with patch.object(builder, "ROOT", root), patch.object(builder, "DATA", root / "data"), patch.object(builder, "PUBLIC", root / "public"), \
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

    def test_published_files_match_all_inputs(self):
        builder.validate_exports()

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

    def test_matching_exports_still_reject_removed_daily_series_and_tenfold_price(self):
        # Change only files offered for download. The same checkout's source archives stay intact.
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp)
            for name in ("gpu-rent.csv", "gpu-rent.json", "nebius-daily.csv"):
                shutil.copy2(builder.PUBLIC / name, public / name)
            with patch.object(builder, "PUBLIC", public):
                csvpath, jsonpath = public / "gpu-rent.csv", public / "gpu-rent.json"
                with csvpath.open() as f:
                    rows = list(csv.DictReader(f))
                data = json.loads(jsonpath.read_bytes())
                original_csv, original_json = csvpath.read_bytes(), jsonpath.read_bytes()
                spot = {"06.ai（Nebius）", "Nebius 现货"}
                for fault in ("drop spot", "change H100"):
                    with self.subTest(fault=fault):
                        changed_rows = [r.copy() for r in rows]
                        changed_data = json.loads(original_json)
                        if fault == "drop spot":
                            changed_rows = [r for r in changed_rows if r["source"] not in spot]
                            changed_data["series"] = [s for s in data["series"] if s["source"] not in spot]
                            self.assertLess(len(changed_rows), len(rows))
                        else:
                            def target(r):
                                return (r["source"], r["product"], r["region"]) == (
                                    "AWS EC2", "p5.48xlarge", "US West (N. California)")
                            row, = [r for r in changed_rows if target(r) and r["date"] == "2026-11-27"]
                            point, = [p for s in changed_data["series"] if target(s)
                                      for p in s["points"] if p[0] == row["date"]]
                            self.assertEqual(float(row["usd_per_gpu_hour"]), 8.6)
                            row["usd_per_gpu_hour"], point[1] = "86.0", 86.0
                        with csvpath.open("w", newline="") as f:
                            writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
                            writer.writeheader()
                            writer.writerows(changed_rows)
                        jsonpath.write_bytes(builder.encode(changed_data))
                        with self.assertRaisesRegex(ValueError, "gpu-rent.csv"):
                            builder.validate_exports()
                        csvpath.write_bytes(original_csv)
                        with self.assertRaisesRegex(ValueError, "gpu-rent.json"):
                            builder.validate_exports()
                        jsonpath.write_bytes(original_json)
                        builder.validate_exports()

    def test_official_day_replaces_only_the_matching_backfill_day(self):
        with price_archive() as (_, public):
            daily = public / "nebius-daily.csv"
            original = daily.read_text()
            daily.write_text(original + "06.ai,B200,dynamic_spot,US,HGX,2,4,2025-01-03\n")
            builder.export()
            with patch.object(builder, "download", return_value=json.dumps([rate("B200", 7)]).encode()):
                builder.refresh("west", "2025-11-28")
            for wrong in ("nebius_api,B200,dynamic_spot,EU,HGX,2,3.7,2025-01-03",
                          "nebius_api,B200,dynamic_spot,US,HGX,1,3.7,2025-01-03",
                          "nebius_api,B200,dynamic_spot,US,HGX,2,3.7,2025-01-04"):
                daily.write_text(original + wrong + "\n")
                with self.assertRaisesRegex(ValueError, "遗漏已发布"):
                    builder.export()
            daily.write_text(original + "nebius_api,B200,dynamic_spot,US,HGX,2,3.7,2025-01-03\n")
            builder.export()
            builder.validate_exports()
            with (public / "gpu-rent.csv").open() as f:
                rows = list(csv.DictReader(f))
            new, = [r for r in rows if r["date"] == "2025-01-03"]
            self.assertEqual((new["source"], float(new["usd_per_gpu_hour"])), ("Nebius 现货", 3.7))
            self.assertTrue(any(r["source"] == "west" and r["date"] == "2025-11-28" for r in rows))

    def test_parseable_archive_recovers_day_and_row_without_overwriting_new_quote(self):
        with price_archive() as (data, public):
            with patch.object(builder, "download", return_value=json.dumps([rate("B200", 6), rate("H100", 4)]).encode()):
                builder.refresh("east", "2025-11-27")
            builder.export()
            path = data / "east/observations.json"
            dates = json.loads(path.read_bytes())
            del dates["2025-11-26"]
            dates["2025-11-27"].pop()
            path.write_text(json.dumps(dates))
            with patch.object(builder, "download", return_value=json.dumps([rate("B200", 7)]).encode()):
                self.assertEqual(builder.refresh("east", "2025-11-27"), 1)
                builder.refresh("west", "2025-11-28")
            builder.export()
            builder.validate_exports()
            with (public / "gpu-rent.csv").open() as f:
                quotes = {(r["source"], r["date"], r["model"]): float(r["usd_per_gpu_hour"]) for r in csv.DictReader(f)}
            self.assertEqual(quotes["east", "2025-11-26", "B200"], 5)
            self.assertEqual(quotes["east", "2025-11-27", "H100"], 4)
            self.assertEqual(quotes["east", "2025-11-27", "B200"], 7)
            self.assertEqual(quotes["west", "2025-11-28", "B200"], 7)

    def test_recovery_preserves_original_query_and_skips_curated_history(self):
        with price_archive() as (data, public):
            path = data / "east/observations.json"
            previous = json.loads(path.read_bytes())
            day = "2025-11-26"
            url = "https://example.com/retail?region=all"
            previous[day][0]["url"] = url
            path.write_text(json.dumps(previous))
            hist = data / "historical.json"
            rows = json.loads(hist.read_bytes())
            snapshot = builder.archive("east", b"old query", "2025-11-25")
            rows.append(dict(rate("H100", 3), source="east", date="2025-11-25", url=url, raw=snapshot))
            hist.write_text(json.dumps(rows))
            builder.export()
            original = (public / "gpu-rent.csv").read_bytes()
            path.write_text("{invalid")
            builder.export()
            self.assertEqual(json.loads(path.read_bytes()), previous)
            self.assertEqual((public / "gpu-rent.csv").read_bytes(), original)
            builder.validate_exports()

    def test_only_changed_gpu_files_require_the_packaging_gate(self):
        script = builder.ROOT / "scripts/gpu_pages_scope.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs/data").mkdir(parents=True)
            artifact = root / "docs/gpu-rent-revision.txt"
            output = root / "output"
            # Simulate Pages serving exactly the last successfully uploaded revision.
            curl = root / "curl"
            curl.write_text('#!/bin/sh\nwhile [ "$1" != "-o" ]; do shift; done\n'
                            'if test -f deployed; then cat deployed > "$2"; printf 200; else printf 404; fi\n')
            curl.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"], GITHUB_OUTPUT=str(output),
                       PAGES_URL="https://test.invalid", GITHUB_RUN_ID="1", GITHUB_RUN_ATTEMPT="1")
            def git(*args):
                subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
                               cwd=root, check=True, capture_output=True)
            def change(path):
                (root / path).write_text("changed " + path)
                git("add", path)
                git("commit", "-qm", path)
            def scope():
                output.write_text("")
                subprocess.run(["bash", str(script)], env=env, cwd=root, check=True)
                return output.read_text().strip()
            git("init", "-q")
            change("docs/gpu-rent.html")
            self.assertEqual(scope(), "changed=true")
            shutil.copyfile(artifact, root / "deployed")
            for path in ("docs/data/tracker.json", "docs/data/nebius-daily.csv"):
                change(path)
                self.assertEqual(scope(), "changed=false")
            change("docs/data/gpu-contracts.json")
            self.assertEqual(scope(), "changed=true")
            # A failed GPU publication must not slip through a subsequent Ramp run.
            change("docs/index.html")
            self.assertEqual(scope(), "changed=true")
            shutil.copyfile(artifact, root / "deployed")
            self.assertEqual(scope(), "changed=false")
        workflow = (builder.ROOT / ".github/workflows/update.yml").read_text().split("\n  package:\n")[1].split("\n  deploy:\n")[0]
        steps = workflow.split("      - ")
        for command in ("actions/setup-python", "pip install", "test_gpu_rent.py"):
            step, = [s for s in steps if command in s]
            self.assertIn("if: steps.gpu_scope.outputs.changed == 'true'", step)
        upload, = [s for s in steps if "actions/upload-pages-artifact" in s]
        self.assertNotIn("gpu_scope", upload)
