"""Offline regressions for the spot archive and on-demand rate card."""
import contextlib
import csv
import gzip
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_nebius as builder


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        capture = contextlib.redirect_stdout(io.StringIO())
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.public = root / "public"
        self.raw = root / "raw"
        self.end = datetime(2026, 11, 26, 7, tzinfo=timezone.utc)
        self.boundary = self.end - 720 * builder.HOUR
        self.models = {"a": "b300", "b": "h200"}
        self.values = {}
        self.patch("PUBLIC", self.public)
        self.patch("ARCHIVE", self.raw)
        self.patch("pages", self.pages)
        self.patch("accepts_hour", lambda t: t >= self.boundary)

    def patch(self, name, value):
        mock = patch.object(builder, name, value)
        mock.start()
        self.addCleanup(mock.stop)

    def pages(self, endpoint, query):
        result = []
        for sku, model in self.models.items():
            row = {"skuId": sku, "region": "r", "computeInstance": {"platform": "gpu-" + model}}
            if endpoint == builder.API:
                row["price"] = {"unitTitle": "GPU hour", "currency": "usd", "value": "2"}
            else:
                start = builder.instant(query["filters.startTime"])
                self.assertGreaterEqual(start, self.boundary)
                end = builder.instant(query["filters.endTime"])
                points = []
                while start < end:
                    start += builder.HOUR
                    points.append({"timestamp": builder.iso(start), "value": self.values.get((sku, start), "2")})
                row.update(currency="usd", unitTitle="GPU hour", points=points)
            result.append(row)
        return result, [{"url": endpoint, "body": {"items": result}}]

    def data(self):
        return json.loads((self.public / "nebius.json").read_bytes())

    def table(self, rows):
        block = {"title": "NVIDIA GPU Instances", "table": {"content": [["Item", "On-demand, GPU-hour"], *rows]}}
        page = {"content": json.dumps({"blocks": [block]})}
        state = {"props": {"pageProps": {"__APOLLO_STATE__": {"prices": page}}}}
        html = '<script id="__NEXT_DATA__">' + json.dumps(state) + '</script>'
        return patch.object(builder, "fetch", return_value=html.encode())

    def test_rate_failure_keeps_new_hour_and_old_verified_card(self):
        builder.archive_spot(self.end)
        with self.table([["NVIDIA HGX B300", "$9.50"], ["NVIDIA HGX H200", "$5.40"]]):
            builder.update_rates("2026-11-26")
        builder.archive_spot(self.end + builder.HOUR)
        csv_before = (self.public / "nebius.csv").read_bytes()
        archive_before = {p: p.read_bytes() for p in self.raw.rglob("*.gz")}
        with patch.object(builder, "fetch", return_value=b"no pricing page"), contextlib.redirect_stderr(io.StringIO()) as log:
            builder.update_rates("2026-11-27")
        self.assertIn("::warning::", log.getvalue())
        self.assertEqual(csv_before, (self.public / "nebius.csv").read_bytes())
        self.assertEqual(archive_before, {p: p.read_bytes() for p in self.raw.rglob("*.gz")})
        self.assertEqual(len(archive_before), 2)
        data = self.data()
        self.assertTrue(all(len(s["points"]) == 721 for s in data["series"]))
        self.assertEqual(data["onDemand"]["usd"], {"B300": 9.5, "H200": 5.4})
        self.assertTrue(all(m["checked"] == "2026-11-26" and m["status"] == "unverified"
                            for m in data["onDemand"]["byModel"].values()))

    def test_full_boundary_and_interrupted_archive(self):
        builder.archive_spot(self.end)
        initial = self.data()["series"]
        for s in initial:
            self.assertEqual(len(s["points"]), 720)
            self.assertEqual(s["points"][0][0], builder.iso(self.boundary + builder.HOUR))
        resumed = self.end + 744 * builder.HOUR
        self.boundary = resumed - 720 * builder.HOUR
        with contextlib.redirect_stderr(io.StringIO()) as log:
            builder.archive_spot(resumed)
        result = self.data()
        for s, old in zip(result["series"], initial):
            self.assertEqual(s["points"][:720], old["points"])
            self.assertEqual(len(s["points"]), 1440)
        for gap in result["gaps"]:
            self.assertIn(gap["skuId"] + " missing 24 hours", log.getvalue())
            self.assertEqual((gap["start"], gap["end"]), (builder.iso(self.end), builder.iso(self.boundary)))
            self.assertEqual((gap["hours"], gap["outsideRetentionHours"]), (24, 24))
        self.assertEqual(len(result["gaps"]), 2)

    def test_legacy_rates_invalid_row_and_change(self):
        builder.archive_spot(self.end)
        legacy = self.data()
        legacy["onDemand"] = {"source": builder.PRICES, "checked": "2026-11-25",
                              "usd": {"B300": 9.5, "H200": 5.4}}
        for bad in [[], [["NVIDIA HGX B300", "from $9.50"]], [["NVIDIA B300", "$9.50"]],
                    [["NVIDIA HGX B300", "$9.50"]] * 2]:
            builder.publish(self.public / "nebius.json", builder.encode(legacy))
            with self.table([*bad, ["NVIDIA HGX H200", "$6.00"]]), contextlib.redirect_stderr(io.StringIO()):
                builder.update_rates("2026-11-26")
            card = self.data()["onDemand"]
            self.assertEqual(card["usd"], {"B300": 9.5, "H200": 6})
            self.assertEqual(card["byModel"]["B300"]["checked"], "2026-11-25")
            self.assertEqual(card["byModel"]["B300"]["status"], "unverified")
            self.assertEqual(card["byModel"]["H200"]["checked"], "2026-11-26")
            self.assertEqual(card["byModel"]["H200"]["lastChange"]["from"], 5.4)
        with self.table([["NVIDIA HGX B300", "$10.00"], ["NVIDIA HGX H200", "$6.00"]]):
            builder.update_rates("2026-11-26")
        self.assertEqual(self.data()["onDemand"]["byModel"]["B300"]["status"], "verified")

    def test_revision_without_new_hour_and_replay(self):
        builder.archive_spot(self.end)
        self.values["a", self.end] = "3.25"
        builder.archive_spot(self.end)
        result = self.data()
        self.assertEqual(len(result["revisions"]), 1)
        change = result["revisions"][0]
        self.assertEqual((change["old"], change["new"], change["hourEnd"]), (2, 3.25, builder.iso(self.end)))
        self.assertEqual(result["series"][0]["points"][-1][1], 3.25)
        with (self.public / "nebius.csv").open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1440)
        self.assertEqual([r["spot_usd_per_gpu_hour"] for r in rows if r["sku_id"] == "a" and
                          r["hour_end_utc"] == change["hourEnd"]], ["3.25"])
        originals = {p: json.loads(gzip.decompress(p.read_bytes())) for p in self.raw.rglob("*.gz")}
        self.assertEqual({d["history"][0]["body"]["items"][0]["points"][-1]["value"]
                          for d in originals.values()}, {"2", "3.25"})
        self.assertIn(self.raw / change["archive"], originals)
        builder.archive_spot(self.end)
        self.assertEqual(self.data(), result)
        self.assertEqual(set(self.raw.rglob("*.gz")), set(originals))


if __name__ == "__main__":
    unittest.main()
