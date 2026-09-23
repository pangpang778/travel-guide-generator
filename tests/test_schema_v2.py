"""T1（#3）回归基线：schema v2 增量字段与双夹具。

- guide_happy.json：幸福路径（v2 新字段全部在场）
- guide_minimal.json：最小降级（纯 v1 形状，无任何 v2 字段，旧管线照常 build/渲染）
- docs/SCHEMA.md 的字段表与本测试互为契约：新增字段必须先改文档再进夹具。
"""

import tempfile
import unittest
from pathlib import Path

from scripts.build_guide import build
from scripts.guide_utils import load_json

FIXTURES = Path("tests") / "fixtures"
HAPPY = load_json(FIXTURES / "guide_happy.json")
MINIMAL = load_json(FIXTURES / "guide_minimal.json")

V2_ONLY_KEYS = (
    "spots",
    "routes",
    "dayColors",
    "alerts",
    "pipeline",
    "blocking_count",
    "hard_rules_passed",
    "advisory_count",
)


def _is_ll(pair):
    return (
        isinstance(pair, (list, tuple))
        and len(pair) == 2
        and all(isinstance(v, (int, float)) for v in pair)
        and 73 <= pair[0] <= 136  # GCJ-02 中国范围 lng
        and 3 <= pair[1] <= 54    # GCJ-02 中国范围 lat
    )


class HappyFixtureTests(unittest.TestCase):
    def test_destination_identity_strict_library(self):
        di = HAPPY["meta"]["destination_identity"]
        for key in ("brand_id", "theme", "reason"):
            self.assertTrue(str(di.get(key, "")).strip(), key)
        self.assertEqual(di["brand_id"], "ferrari")

    def test_spots_have_real_coords_and_time_windows(self):
        self.assertEqual(len(HAPPY["spots"]), 9)
        for sp in HAPPY["spots"]:
            self.assertIn(sp["day"], (1, 2, 3))
            self.assertTrue(_is_ll(sp["ll"]), sp["name"])
            self.assertRegex(sp["tw"], r"^\d{2}:\d{2}-\d{2}:\d{2}$", sp["name"])
            img = sp.get("image")
            for key in ("url", "source", "credibility", "alt_description", "jev_score"):
                self.assertIn(key, img, sp["name"])
            self.assertTrue(0 <= img["jev_score"] <= 10)
            self.assertIn("untrusted", img["credibility"])

    def test_routes_are_real_coord_sequences(self):
        self.assertEqual(len(HAPPY["routes"]), 3)
        for route in HAPPY["routes"]:
            self.assertTrue(_is_ll(route["pts"][0]))
            day_spots = [sp for sp in HAPPY["spots"] if sp["day"] == route["day"]]
            self.assertEqual(route["pts"][0], day_spots[0]["ll"], route["day"])

    def test_alerts_and_pipeline_metadata(self):
        sources = {a["source"] for a in HAPPY["alerts"]}
        self.assertTrue(sources.issubset({"JEV", "RULE"}))
        self.assertEqual(HAPPY["blocking_count"], 0)
        self.assertEqual(HAPPY["hard_rules_passed"], 3)
        self.assertEqual(HAPPY["advisory_count"], 3)
        self.assertTrue(HAPPY["pipeline"]["collect"]["ok"])
        self.assertEqual(HAPPY["pipeline"]["jev"]["limit"], 20)
        self.assertEqual(HAPPY["pipeline"]["jev"]["threshold"], 7)

    def test_weather_has_v2_display_fields(self):
        for w in HAPPY["weather"]:
            for key in ("day", "icon", "temp", "advice"):
                self.assertIn(key, w)


class MinimalFixtureTests(unittest.TestCase):
    def test_v2_only_fields_absent(self):
        for key in V2_ONLY_KEYS:
            self.assertNotIn(key, MINIMAL, key)
        self.assertNotIn("destination_identity", MINIMAL["meta"])

    def test_minimal_builds_with_zero_behavior_change(self):
        """旧管线对纯 v1 夹具照常产出全部导出格式（行为零变化验收）。"""
        with tempfile.TemporaryDirectory() as directory:
            result = build(MINIMAL, Path(directory) / "guide")
            self.assertEqual(result["status"], "ok")
            for output in result["files"]:
                self.assertTrue(Path(output).exists(), output)


if __name__ == "__main__":
    unittest.main()
