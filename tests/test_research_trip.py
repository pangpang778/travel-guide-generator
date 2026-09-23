import unittest
from unittest.mock import patch

from scripts.research_trip import (
    _clean_command_message,
    _json_from_output,
    choose_station,
    collect_transport,
)


class ResearchTripTests(unittest.TestCase):
    def test_clean_command_message_removes_runtime_noise(self):
        raw = """(node:123) [UNDICI-EHPA] warning
(Use `node --trace-warnings ...` to show where the warning was created)
ok: false
error:
  code: EMPTY_RESULT
  message: No trains found from 宁波 to 成都东 returned no data
  exitCode: 66"""
        message = _clean_command_message(raw)
        self.assertEqual(message, "12306 当前日期未返回直达车次，需查中转换乘或调整日期。")
        self.assertNotIn("node:", message)

    def test_json_parser_skips_runtime_warning(self):
        self.assertEqual(_json_from_output("warning\n[{\"code\":\"G1\"}]"), [{"code": "G1"}])

    def test_choose_station_prefers_city_station(self):
        result = {
            "data": [
                {"name": "苏州北", "code": "OHH"},
                {"name": "苏州", "code": "SZH"},
            ]
        }
        self.assertEqual(choose_station("苏州", result)["code"], "SZH")

    def test_collect_transport_enriches_recommended_train(self):
        station_result = {
            "ok": True,
            "data": [{"name": "宁波", "code": "NGH"}],
        }
        destination_result = {
            "ok": True,
            "data": [{"name": "苏州", "code": "SZH"}],
        }
        train_result = {
            "ok": True,
            "data": [
                {
                    "code": "G1",
                    "train_no": "internal-1",
                    "from_station": "宁波",
                    "to_station": "苏州",
                    "start_time": "08:00",
                    "arrive_time": "10:30",
                    "duration": "02:30",
                    "available": True,
                    "second_seat": "有",
                }
            ],
        }
        with patch("scripts.research_trip.list_stations", side_effect=[station_result, destination_result]), patch(
            "scripts.research_trip.search_trains", side_effect=[train_result, train_result]
        ), patch(
            "scripts.research_trip.train_price",
            return_value={"ok": True, "data": [{"seat_name": "二等座", "price": 123}]},
        ), patch(
            "scripts.research_trip.train_route",
            return_value={"ok": True, "data": [{"station_name": "宁波"}]},
        ):
            result = collect_transport(
                "宁波",
                "苏州",
                "2026-09-26",
                "2026-09-28",
                5,
                True,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["outbound"]["recommended"]["prices"][0]["price"], 123)
        self.assertEqual(result["return"]["recommended"]["stops"][0]["station_name"], "宁波")

    def test_collect_transport_reports_missing_date(self):
        result = collect_transport("宁波", "苏州", None, None, 5, False)
        self.assertEqual(result["status"], "needs_date")


if __name__ == "__main__":
    unittest.main()
