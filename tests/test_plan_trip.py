import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.plan_trip import build_candidate_guides, guide_from_research


class PlanTripTests(unittest.TestCase):
    def test_builds_a_guide_for_every_candidate(self):
        checked_at = date.today().isoformat()
        research = {
            "collected_at": checked_at,
            "request": {
                "origin": "宁波",
                "destinations": ["苏州", "福州"],
                "start_date": "2026-09-26",
                "return_date": "2026-09-28",
                "days": 3,
                "nights": 2,
                "travelers": 1,
            },
            "sources": [
                {
                    "id": "12306-official",
                    "title": "12306 train and station data",
                    "type": "official",
                    "checked_at": checked_at,
                },
                {
                    "id": "xiaohongshu-suzhou-1",
                    "title": "苏州三日游参考",
                    "type": "search",
                    "checked_at": checked_at,
                    "url": "https://example.com/suzhou",
                },
                {
                    "id": "xiaohongshu-fuzhou-1",
                    "title": "福州三日游参考",
                    "type": "search",
                    "checked_at": checked_at,
                    "url": "https://example.com/fuzhou",
                },
                {
                    "id": "xiaohongshu-suzhou-1",
                    "title": "重复来源记录",
                    "type": "search",
                    "checked_at": checked_at,
                    "url": "https://example.com/suzhou",
                },
            ],
            "destinations": [
                {
                    "destination": "苏州",
                    "references": {
                        "xiaohongshu": {
                            "notes": [
                                {
                                    "title": "苏州三日游参考",
                                    "url": "https://example.com/suzhou",
                                    "source_id": "xiaohongshu-suzhou-1",
                                }
                            ]
                        }
                    },
                    "transport": {
                        "status": "ok",
                        "outbound": {
                            "date": "2026-09-26",
                            "from": {"name": "宁波"},
                            "to": {"name": "苏州"},
                            "recommended": {
                                "code": "G1866",
                                "start_time": "06:14",
                                "arrive_time": "09:02",
                                "duration": "02:48",
                                "available": True,
                                "second_seat": "198",
                            },
                            "trains": [],
                        },
                        "return": {
                            "date": "2026-09-28",
                            "from": {"name": "苏州"},
                            "to": {"name": "宁波"},
                            "recommended": {
                                "code": "G3059",
                                "start_time": "15:06",
                                "arrive_time": "17:35",
                                "duration": "02:29",
                                "available": True,
                                "second_seat": "198",
                            },
                            "trains": [],
                        },
                    },
                    "warnings": [],
                },
                {
                    "destination": "福州",
                    "references": {
                        "xiaohongshu": {
                            "notes": [
                                {
                                    "title": "福州三日游参考",
                                    "url": "https://example.com/fuzhou",
                                    "source_id": "xiaohongshu-fuzhou-1",
                                }
                            ]
                        }
                    },
                    "transport": {
                        "status": "ok",
                        "outbound": {"trains": []},
                        "return": {"trains": []},
                    },
                    "warnings": ["当前日期没有完整直达样本"],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            guides = build_candidate_guides(research, output_dir)

            self.assertEqual([item["destination"] for item in guides], ["苏州", "福州"])
            self.assertEqual(len(guides[0]["files"]), 6)
            self.assertEqual(len(guides[1]["files"]), 6)
            for item in guides:
                for relative_path in item["files"]:
                    self.assertTrue((output_dir / relative_path).exists(), relative_path)
            self.assertEqual(research["destinations"][0]["guide_html"], "guides/01-trip.html")
            self.assertIn("苏州", (output_dir / "guides/01-trip.html").read_text(encoding="utf-8"))
            self.assertIn("福州", (output_dir / "guides/02-trip.html").read_text(encoding="utf-8"))


BUDGET_KEYS = {"车票", "门票", "餐饮", "住宿"}


def _research(days=3, nights=2, travelers=2):
    return {
        "collected_at": date.today().isoformat(),
        "request": {
            "origin": "宁波",
            "destinations": ["苏州"],
            "start_date": "2026-09-26",
            "return_date": "2026-09-28",
            "days": days,
            "nights": nights,
            "travelers": travelers,
        },
        "sources": [],
        "destinations": [],
    }


def _realtime_candidate():
    return {
        "destination": "苏州",
        "references": {},
        "warnings": [],
        "transport": {
            "status": "ok",
            "outbound": {
                "date": "2026-09-26",
                "from": {"name": "宁波"},
                "to": {"name": "苏州"},
                "recommended": {
                    "code": "G1866",
                    "start_time": "06:14",
                    "arrive_time": "09:02",
                    "duration": "02:48",
                    "available": True,
                    "second_seat": "198",
                },
                "trains": [],
            },
            "return": {
                "date": "2026-09-28",
                "from": {"name": "苏州"},
                "to": {"name": "宁波"},
                "recommended": {
                    "code": "G3059",
                    "start_time": "15:06",
                    "arrive_time": "17:35",
                    "duration": "02:29",
                    "available": True,
                    "second_seat": "198",
                },
                "trains": [],
            },
        },
    }


class BudgetSummaryTests(unittest.TestCase):
    def test_budget_categories_are_numeric_with_total(self):
        guide = guide_from_research(_research(), _realtime_candidate())
        profile = guide["budget"]["profiles"]["comfortable"]
        categories = profile["categories"]
        self.assertEqual(set(categories), BUDGET_KEYS)
        for value in categories.values():
            self.assertIsInstance(value, (int, float))
        # 198 元二等座 x 去返两程 x 2 人
        self.assertEqual(categories["车票"], 792)
        self.assertEqual(profile["total"], sum(categories.values()))
        self.assertEqual(guide["confidence"]["budget"], "estimated")

    def test_budget_survives_missing_transport(self):
        guide = guide_from_research(_research(), {"destination": "苏州", "references": {}, "warnings": []})
        categories = guide["budget"]["profiles"]["comfortable"]["categories"]
        self.assertEqual(set(categories), BUDGET_KEYS)
        for value in categories.values():
            self.assertIsInstance(value, (int, float))
        self.assertEqual(categories["车票"], 0)


class HotelTiersTests(unittest.TestCase):
    def test_hotels_cover_first_value_and_avoid_tiers(self):
        guide = guide_from_research(_research(), _realtime_candidate())
        tiers = [hotel["tier"] for hotel in guide["hotels"]]
        self.assertEqual(tiers, ["首选", "性价比", "避免"])
        first = guide["hotels"][0]
        self.assertIn("地铁", first["reason"])
        self.assertIn("近核心景点", first["area"])
        self.assertTrue(all(hotel.get("source_ids") for hotel in guide["hotels"]))

    def test_first_tier_references_return_station_when_known(self):
        guide = guide_from_research(_research(), _realtime_candidate())
        first = guide["hotels"][0]
        self.assertIn("返程车站：苏州", first["connection"])


class TransportComparisonTests(unittest.TestCase):
    def test_compares_at_least_four_modes(self):
        guide = guide_from_research(_research(), _realtime_candidate())
        modes = {record["mode"] for record in guide["transport"]}
        self.assertGreaterEqual(len(guide["transport"]), 4)
        self.assertTrue({"train", "plane", "drive", "bus"} <= modes)
        advisory = [record for record in guide["transport"] if record["mode"] != "train"]
        self.assertTrue(advisory)
        for record in advisory:
            self.assertFalse(record["recommended"])
            self.assertIn("非实时", record["title"])
            self.assertEqual(record["source_ids"], ["generated-itinerary"])

    def test_train_segment_is_realtime_when_live_sample_exists(self):
        guide = guide_from_research(_research(), _realtime_candidate())
        self.assertEqual(guide["confidence"]["transport"], "realtime")
        train_records = [record for record in guide["transport"] if record["mode"] == "train"]
        self.assertTrue(train_records)
        self.assertTrue(all("非实时" not in record["title"] for record in train_records))

    def test_train_segment_marked_non_realtime_without_sample(self):
        guide = guide_from_research(_research(), {"destination": "苏州", "references": {}, "warnings": []})
        self.assertEqual(guide["confidence"]["transport"], "estimated")
        train_records = [record for record in guide["transport"] if record["mode"] == "train"]
        self.assertTrue(train_records)
        self.assertTrue(all("非实时" in record["title"] for record in train_records))


if __name__ == "__main__":
    unittest.main()
