import copy
import unittest
from pathlib import Path

from scripts.route_estimator import estimate_route
from scripts.validate_guide import (
    GAP_BUFFER_MIN,
    RETURN_DAY_BUFFER_MIN,
    validate_guide,
)
from scripts.guide_utils import load_json


FIXTURES = Path("tests") / "fixtures"
HAPPY = load_json(FIXTURES / "guide_happy.json")
MINIMAL = load_json(FIXTURES / "guide_minimal.json")


def valid_guide():
    return {
        "schema_version": "1.0",
        "meta": {
            "title": "测试攻略",
            "destination": "测试地",
            "language": "zh-CN",
            "start_date": "2026-09-20",
            "days": 1,
        },
        "preferences": {"pace": "balanced"},
        "sources": [
            {
                "id": "official",
                "title": "官方",
                "type": "official",
                "checked_at": "2026-09-01",
            }
        ],
        "days": [
            {
                "day": 1,
                "date": "2026-09-20",
                "title": "测试",
                "items": [
                    {
                        "name": "地点 A",
                        "start": "09:00",
                        "end": "10:00",
                        "source_ids": ["official"],
                    },
                    {
                        "name": "地点 B",
                        "start": "10:30",
                        "end": "11:30",
                        "route_from_previous": {
                            "duration_min": 20,
                            "estimated": True,
                            "method": "test",
                        },
                    },
                ],
            }
        ],
    }


class ValidateGuideTests(unittest.TestCase):
    def test_rejects_unknown_schema_version(self):
        guide = valid_guide()
        guide["schema_version"] = "9.9"

        report = validate_guide(guide)

        self.assertFalse(report["valid"])
        self.assertEqual(report["errors"][0]["code"], "SCHEMA_VERSION")

    def test_valid_guide_has_no_errors_or_conflicts(self):
        report = validate_guide(valid_guide())

        self.assertTrue(report["valid"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["conflicts"], [])

    def test_detects_overlap_and_insufficient_transit_time(self):
        guide = copy.deepcopy(valid_guide())
        second = guide["days"][0]["items"][1]
        second["start"] = "09:50"
        second["route_from_previous"]["duration_min"] = 30

        report = validate_guide(guide)
        codes = {item["code"] for item in report["conflicts"]}

        self.assertIn("TIME_OVERLAP", codes)
        self.assertIn("TRANSIT_TOO_SHORT", codes)

    def test_detects_outside_opening_hours(self):
        guide = copy.deepcopy(valid_guide())
        guide["days"][0]["items"][0]["opening_hours"] = {
            "open": "10:00",
            "close": "18:00",
        }

        report = validate_guide(guide)

        self.assertEqual(report["conflicts"][0]["code"], "OUTSIDE_OPENING_HOURS")


class HardRuleTests(unittest.TestCase):
    """四类硬规则各有触发/不触发两个方向的构造夹具测试。"""

    def _conflict_codes(self, report):
        return {item["code"] for item in report["conflicts"]}

    # -- 规则 1：营业时间冲突（OUTSIDE_OPENING_HOURS） --------------------

    def test_opening_hours_conflict_triggers_blocking(self):
        guide = copy.deepcopy(valid_guide())
        guide["days"][0]["items"][0]["opening_hours"] = {
            "open": "10:00",
            "close": "18:00",
        }

        report = validate_guide(guide)

        self.assertIn("OUTSIDE_OPENING_HOURS", self._conflict_codes(report))
        self.assertEqual(report["blocking_count"], 1)
        self.assertTrue(report["watermark"])
        blocking = [a for a in report["alerts"] if a["level"] == "blocking"]
        self.assertEqual(blocking[0]["source"], "RULE")
        self.assertEqual(blocking[0]["type"], "营业时间")

    def test_opening_hours_within_window_does_not_trigger(self):
        guide = copy.deepcopy(valid_guide())
        guide["days"][0]["items"][0]["opening_hours"] = {
            "open": "08:00",
            "close": "18:00",
        }

        report = validate_guide(guide)

        self.assertNotIn("OUTSIDE_OPENING_HOURS", self._conflict_codes(report))
        self.assertFalse(report["watermark"])

    def test_missing_opening_hours_is_skipped(self):
        guide = copy.deepcopy(valid_guide())
        self.assertNotIn("OUTSIDE_OPENING_HOURS", self._conflict_codes(validate_guide(guide)))

    # -- 规则 2：衔接时间不足（估算交通时间 + 缓冲） ----------------------

    def _two_spot_guide(self, gap_minutes, day_count=1):
        """两个带坐标的相邻行程点，间距约 8.5km，drive 估算可复算。"""
        guide = copy.deepcopy(valid_guide())
        origin = [116.0, 40.0]
        destination = [116.1, 40.0]
        estimate = estimate_route(origin, destination, "drive")
        start_hour = 9
        end = "{:02d}:00".format(start_hour + 1)
        second_start_minute = 60 + gap_minutes  # 上一点 09:00-10:00 结束后留 gap
        second_start = "{:02d}:{:02d}".format(
            start_hour + second_start_minute // 60, second_start_minute % 60
        )
        guide["days"][0]["items"] = [
            {
                "name": "起点",
                "start": "09:00",
                "end": "10:00",
                "coords": origin,
            },
            {
                "name": "终点",
                "start": second_start,
                "end": "18:00",
                "coords": destination,
            },
        ]
        guide["meta"]["days"] = day_count
        while len(guide["days"]) < day_count:
            guide["days"].append(
                {
                    "day": len(guide["days"]) + 1,
                    "date": "2026-09-{}".format(20 + len(guide["days"])),
                    "title": "填充日",
                    "items": [
                        {"name": "景点", "start": "09:00", "end": "10:00"}
                    ],
                }
            )
        return guide, estimate

    def test_estimated_gap_insufficient_triggers(self):
        # gap = 估算 + 缓冲 - 10 分钟 → 触发
        estimate = estimate_route([116.0, 40.0], [116.1, 40.0], "drive")
        guide, _ = self._two_spot_guide(
            int(estimate["duration_min"]) + GAP_BUFFER_MIN - 10
        )

        report = validate_guide(guide)

        self.assertIn("TRANSIT_TOO_SHORT", self._conflict_codes(report))
        self.assertTrue(report["watermark"])

    def test_estimated_gap_sufficient_does_not_trigger(self):
        # gap = 估算 + 缓冲 + 10 分钟 → 不触发（day_count=2 避开返程日缓冲）
        estimate = estimate_route([116.0, 40.0], [116.1, 40.0], "drive")
        guide, _ = self._two_spot_guide(
            int(estimate["duration_min"]) + GAP_BUFFER_MIN + 10, day_count=2
        )

        report = validate_guide(guide)

        self.assertNotIn("TRANSIT_TOO_SHORT", self._conflict_codes(report))
        self.assertFalse(report["watermark"])

    def test_return_day_requires_three_hour_buffer(self):
        # 同样的 gap，在末位日（返程日）须满足估算 + 180 分钟缓冲
        gap = int(estimate_route([116.0, 40.0], [116.1, 40.0], "drive")["duration_min"])
        gap += GAP_BUFFER_MIN + 10  # 非返程日足以通过

        tight = self._two_spot_guide(gap, day_count=1)[0]
        loose = self._two_spot_guide(gap, day_count=2)[0]

        self.assertIn("TRANSIT_TOO_SHORT", self._conflict_codes(validate_guide(tight)))
        self.assertNotIn(
            "TRANSIT_TOO_SHORT", self._conflict_codes(validate_guide(loose))
        )
        self.assertEqual(RETURN_DAY_BUFFER_MIN, 180)

    # -- 规则 3：跨城不合理（CROSS_CITY_NO_TRANSIT） ----------------------

    def test_cross_city_without_transit_triggers(self):
        guide = copy.deepcopy(valid_guide())
        guide["days"][0]["items"] = [
            {"name": "北京景点", "start": "09:00", "end": "10:00", "coords": [116.4, 39.9]},
            {"name": "天津景点", "start": "11:00", "end": "12:00", "coords": [117.2, 39.1]},
        ]

        report = validate_guide(guide)

        self.assertIn("CROSS_CITY_NO_TRANSIT", self._conflict_codes(report))
        self.assertTrue(report["watermark"])

    def test_cross_city_with_intercity_segment_does_not_trigger(self):
        guide = copy.deepcopy(valid_guide())
        guide["intercity"] = {
            "from": {"name": "北京南", "time": "10:30", "ll": [116.378, 39.865]},
            "to": {"name": "天津西", "time": "11:01", "ll": [117.16, 39.15]},
            "line": "京津城际",
            "duration": 31,
        }
        # gap 600 分钟同时满足估算衔接检查（约 115km drive ≈ 299 分钟 + 缓冲）
        guide["days"][0]["items"] = [
            {"name": "北京景点", "start": "09:00", "end": "10:00", "coords": [116.4, 39.9]},
            {"name": "天津景点", "start": "20:00", "end": "21:00", "coords": [117.2, 39.1]},
        ]

        report = validate_guide(guide)

        self.assertNotIn("CROSS_CITY_NO_TRANSIT", self._conflict_codes(report))
        self.assertNotIn("TRANSIT_TOO_SHORT", self._conflict_codes(report))

    def test_same_city_short_move_does_not_trigger(self):
        guide = copy.deepcopy(valid_guide())
        guide["days"][0]["items"] = [
            {"name": "景点 A", "start": "09:00", "end": "10:00", "coords": [116.4, 39.9]},
            {"name": "景点 B", "start": "12:00", "end": "13:00", "coords": [116.42, 39.92]},
        ]

        report = validate_guide(guide)

        self.assertNotIn("CROSS_CITY_NO_TRANSIT", self._conflict_codes(report))

    # -- 规则 4：预算超支（BUDGET_OVERRUN） -------------------------------

    def _guide_with_budget(self, limit):
        guide = copy.deepcopy(valid_guide())
        guide["preferences"]["budget_limit"] = limit
        guide["budget"] = {
            "selected": "comfortable",
            "profiles": {
                "comfortable": {
                    "categories": {"交通": 240, "门票": 230, "餐饮": 260, "住宿": 360},
                    "total": 1210,
                }
            },
        }
        return guide

    def test_budget_overrun_triggers(self):
        report = validate_guide(self._guide_with_budget(500))

        self.assertIn("BUDGET_OVERRUN", self._conflict_codes(report))
        self.assertTrue(report["watermark"])
        blocking = [a for a in report["alerts"] if a["level"] == "blocking"]
        self.assertEqual(blocking[0]["type"], "预算超支")

    def test_budget_within_limit_does_not_trigger(self):
        report = validate_guide(self._guide_with_budget(2000))

        self.assertNotIn("BUDGET_OVERRUN", self._conflict_codes(report))

    def test_budget_without_limit_is_skipped(self):
        guide = copy.deepcopy(valid_guide())
        guide["budget"] = {
            "selected": "comfortable",
            "profiles": {"comfortable": {"total": 99999}},
        }

        report = validate_guide(guide)

        self.assertNotIn("BUDGET_OVERRUN", self._conflict_codes(report))

    # -- advisory 分级 -----------------------------------------------------

    def test_long_day_pace_hint_is_advisory_not_blocking(self):
        guide = copy.deepcopy(valid_guide())
        guide["days"][0]["items"] = [
            {"name": "早场", "start": "06:00", "end": "12:00"},
            {"name": "夜场", "start": "14:00", "end": "20:00"},
        ]

        report = validate_guide(guide)

        self.assertEqual(report["conflicts"], [])
        self.assertEqual(
            [w["code"] for w in report["warnings"]], ["PACE_LONG_DAY"]
        )
        self.assertEqual(report["blocking_count"], 0)
        self.assertEqual(report["advisory_count"], 1)
        self.assertFalse(report["watermark"])
        advisory = [a for a in report["alerts"] if a["level"] == "advisory"]
        self.assertEqual(advisory[0]["source"], "RULE")
        self.assertEqual(advisory[0]["type"], "节奏提示")

    # -- 报告契约与夹具回归 -----------------------------------------------

    def test_report_contract_fields_present(self):
        report = validate_guide(valid_guide())

        for key in (
            "valid",
            "errors",
            "warnings",
            "conflicts",
            "blocking_count",
            "advisory_count",
            "alerts",
            "watermark",
        ):
            self.assertIn(key, report)

    def test_happy_fixture_has_zero_blocking(self):
        """幸福路径夹具现有数据自洽：0 blocking、无水印。"""
        report = validate_guide(HAPPY)

        self.assertEqual(report["blocking_count"], 0)
        self.assertFalse(report["watermark"])
        self.assertNotIn("BUDGET_OVERRUN", self._conflict_codes(report))

    def test_minimal_fixture_does_not_crash_and_stays_valid(self):
        """最小降级夹具（纯 v1）不崩，且结构校验与硬规则全部通过。"""
        report = validate_guide(MINIMAL)

        self.assertTrue(report["valid"])
        self.assertEqual(report["blocking_count"], 0)
        self.assertFalse(report["watermark"])


if __name__ == "__main__":
    unittest.main()
