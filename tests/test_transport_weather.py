import json
import unittest
from datetime import date
from unittest.mock import patch

from scripts.plan_trip import guide_from_research, _is_rainy, _ticket_status, _weather_icon
from scripts.research_trip import fetch_daily_weather


def _research():
    return {
        "collected_at": date.today().isoformat(),
        "request": {
            "origin": "宁波",
            "destinations": ["苏州"],
            "start_date": "2026-09-26",
            "return_date": "2026-09-28",
            "days": 3,
            "nights": 2,
            "travelers": 1,
        },
        "sources": [],
        "destinations": [],
    }


def _candidate(transport=None, weather=None):
    candidate = {
        "destination": "苏州",
        "references": {},
        "warnings": [],
    }
    if transport is not None:
        candidate["transport"] = transport
    if weather is not None:
        candidate["weather"] = weather
    return candidate


def _realtime_transport():
    return {
        "status": "ok",
        "outbound": {
            "date": "2026-09-26",
            "from": {"name": "宁波"},
            "to": {"name": "苏州"},
            "recommended": {
                "code": "G1866",
                "train_no": "24000000G60I",
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
    }


class TicketStatusTests(unittest.TestCase):
    def test_status_from_available_flag(self):
        self.assertEqual(_ticket_status({"available": True}), "ok")
        self.assertEqual(_ticket_status({"available": False}), "soldout")
        self.assertEqual(_ticket_status({}), "unknown")
        self.assertEqual(_ticket_status(None), "none")

    def test_tight_from_waitlist_or_low_count(self):
        self.assertEqual(_ticket_status({"second_seat": "候补"}), "tight")
        self.assertEqual(_ticket_status({"second_seat": "12"}), "tight")
        self.assertEqual(_ticket_status({"second_seat": "300"}), "unknown")


class IntercityTests(unittest.TestCase):
    def test_intercity_written_from_realtime_train(self):
        guide = guide_from_research(_research(), _candidate(transport=_realtime_transport()))
        intercity = guide["intercity"]
        self.assertEqual(intercity["from"]["name"], "宁波")
        self.assertEqual(intercity["from"]["time"], "06:14")
        self.assertEqual(intercity["to"]["name"], "苏州")
        self.assertEqual(intercity["to"]["time"], "09:02")
        self.assertEqual(intercity["line"], "G1866")
        self.assertEqual(intercity["duration"], "02:48")
        self.assertEqual(intercity["price"], "¥198")
        self.assertEqual(intercity["train_no"], "24000000G60I")
        self.assertEqual(guide["confidence"]["transport"], "realtime")
        self.assertNotIn("非实时", guide["transport"][0]["detail"])

    def test_transport_degrades_to_estimated_without_trains(self):
        transport = {"status": "ok", "outbound": {"trains": []}, "return": {"trains": []}}
        guide = guide_from_research(_research(), _candidate(transport=transport))
        self.assertNotIn("intercity", guide)
        self.assertEqual(guide["confidence"]["transport"], "estimated")
        self.assertIn("非实时", guide["transport"][0]["title"])
        self.assertIn("非实时", guide["transport"][0]["detail"])

    def test_transport_degrades_on_12306_error(self):
        transport = {"status": "error", "message": "station lookup failed"}
        guide = guide_from_research(_research(), _candidate(transport=transport))
        self.assertEqual(guide["confidence"]["transport"], "estimated")
        self.assertIn("非实时", guide["transport"][0]["title"])


class TicketWarningTests(unittest.TestCase):
    def _leg(self, recommended, trains):
        return {
            "from": {"name": "宁波"},
            "to": {"name": "苏州"},
            "recommended": recommended,
            "trains": trains,
        }

    def test_soldout_gets_warning_and_alternate(self):
        leg = self._leg(
            {"code": "G1930", "start_time": "16:40", "available": False},
            [
                {"code": "G1930", "start_time": "16:40", "available": False},
                {"code": "G1909", "start_time": "17:26", "available": True},
            ],
        )
        guide = guide_from_research(_research(), _candidate(transport={"status": "ok", "outbound": leg, "return": {}}))
        detail = guide["transport"][0]["detail"]
        self.assertIn("⚠ 该车次无票", detail)
        self.assertIn("17:26 G1909", detail)
        alerts = [a for a in guide["alerts"] if a["type"] == "余票告警"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["source"], "RULE")
        self.assertIn("G1909", alerts[0]["detail"])

    def test_soldout_without_alternate_still_warns(self):
        leg = self._leg(
            {"code": "G1930", "available": False},
            [{"code": "G1930", "available": False}],
        )
        guide = guide_from_research(_research(), _candidate(transport={"status": "ok", "outbound": leg, "return": {}}))
        self.assertIn("建议改选其他车次或调整日期", guide["transport"][0]["detail"])

    def test_tight_train_warns_with_alternate(self):
        leg = self._leg(
            {"code": "G1930", "start_time": "16:40", "second_seat": "候补"},
            [
                {"code": "G1930", "second_seat": "候补"},
                {"code": "G1909", "start_time": "17:26", "available": True},
            ],
        )
        guide = guide_from_research(_research(), _candidate(transport={"status": "ok", "outbound": leg, "return": {}}))
        self.assertIn("⚠ 余票紧张", guide["transport"][0]["detail"])
        self.assertIn("17:26 G1909", guide["transport"][0]["detail"])

    def test_available_train_has_no_warning(self):
        guide = guide_from_research(_research(), _candidate(transport=_realtime_transport()))
        self.assertEqual(
            [a for a in guide.get("alerts", []) if a["type"] == "余票告警"], []
        )


class WeatherSectionTests(unittest.TestCase):
    def _weather_payload(self, days):
        return {"status": "ok", "source": "open-meteo", "days": days}

    def test_weather_written_per_day(self):
        weather = self._weather_payload(
            [
                {"date": "2026-09-26", "weather_code": 0, "temp_max": 22.4, "temp_min": 12.6, "precip_prob": 5},
                {"date": "2026-09-27", "weather_code": 2, "temp_max": 20.0, "temp_min": 11.0, "precip_prob": 10},
                {"date": "2026-09-28", "weather_code": 61, "temp_max": 17.0, "temp_min": 10.0, "precip_prob": 70},
            ]
        )
        guide = guide_from_research(_research(), _candidate(transport=_realtime_transport(), weather=weather))
        self.assertEqual(len(guide["weather"]), 3)
        first = guide["weather"][0]
        self.assertEqual(first["day"], "Day 1 · 09/26")
        self.assertEqual(first["icon"], "☀️")
        self.assertEqual(first["temp"], "13–22℃")
        self.assertIn("advice", first)
        self.assertEqual(guide["confidence"]["weather"], "realtime")

    def test_rain_triggers_rule_alert_only(self):
        weather = self._weather_payload(
            [
                {"date": "2026-09-26", "weather_code": 0, "temp_max": 22, "temp_min": 12, "precip_prob": 5},
                {"date": "2026-09-27", "weather_code": 2, "temp_max": 20, "temp_min": 11, "precip_prob": 65},
                {"date": "2026-09-28", "weather_code": 0, "temp_max": 21, "temp_min": 12, "precip_prob": 5},
            ]
        )
        guide = guide_from_research(_research(), _candidate(transport=_realtime_transport(), weather=weather))
        rain_alerts = [a for a in guide["alerts"] if a["type"] == "雨天替代"]
        self.assertEqual(len(rain_alerts), 1)
        self.assertEqual(rain_alerts[0]["source"], "RULE")
        self.assertEqual(rain_alerts[0]["title"], "Day 2 预报有雨")
        self.assertIn("宿主 AI", rain_alerts[0]["detail"])

    def test_weather_unavailable_degrades_explicitly(self):
        weather = {"status": "unavailable", "message": "天气接口未返回数据，天气数据不可用"}
        guide = guide_from_research(_research(), _candidate(transport=_realtime_transport(), weather=weather))
        self.assertNotIn("weather", guide)
        self.assertEqual(guide["confidence"]["weather"], "estimated")
        alerts = [a for a in guide["alerts"] if a["type"] == "天气数据不可用"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("天气数据不可用", alerts[0]["title"])

    def test_missing_weather_payload_degrades(self):
        guide = guide_from_research(_research(), _candidate(transport=_realtime_transport()))
        self.assertNotIn("weather", guide)
        self.assertEqual(guide["confidence"]["weather"], "estimated")


class WeatherHelpersTests(unittest.TestCase):
    def test_icon_mapping(self):
        self.assertEqual(_weather_icon(0), "☀️")
        self.assertEqual(_weather_icon(61), "🌧️")
        self.assertEqual(_weather_icon(95), "⛈️")
        self.assertEqual(_weather_icon(75), "🌨️")
        self.assertEqual(_weather_icon(None), "❔")

    def test_rainy_by_code_or_probability(self):
        self.assertTrue(_is_rainy({"weather_code": 61, "precip_prob": 5}))
        self.assertTrue(_is_rainy({"weather_code": 3, "precip_prob": 80}))
        self.assertFalse(_is_rainy({"weather_code": 0, "precip_prob": 10}))
        self.assertFalse(_is_rainy({"weather_code": 71, "precip_prob": 5}))


class _FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._data


class FetchDailyWeatherTests(unittest.TestCase):
    def test_missing_dates_degrades_without_network(self):
        with patch("scripts.research_trip.urllib.request.urlopen") as urlopen:
            result = fetch_daily_weather("苏州", None, None)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("天气数据不可用", result["message"])
        urlopen.assert_not_called()

    def test_geocode_failure_degrades(self):
        with patch("scripts.research_trip.urllib.request.urlopen", return_value=_FakeResponse({})):
            result = fetch_daily_weather("不存在的地方", "2026-09-26", "2026-09-28")
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("城市定位失败", result["message"])

    def test_timeout_degrades(self):
        with patch("scripts.research_trip.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            result = fetch_daily_weather("苏州", "2026-09-26", "2026-09-28")
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("天气数据不可用", result["message"])

    def test_forecast_parsed_into_days(self):
        geocode = _FakeResponse({"results": [{"latitude": 31.3, "longitude": 120.6}]})
        forecast = _FakeResponse(
            {
                "daily": {
                    "time": ["2026-09-26", "2026-09-27"],
                    "weather_code": [0, 61],
                    "temperature_2m_max": [22.4, 17.0],
                    "temperature_2m_min": [12.6, 10.0],
                    "precipitation_probability_max": [5, 70],
                }
            }
        )
        with patch("scripts.research_trip.urllib.request.urlopen", side_effect=[geocode, forecast]):
            result = fetch_daily_weather("苏州", "2026-09-26", "2026-09-27")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["days"]), 2)
        self.assertEqual(result["days"][1]["weather_code"], 61)
        self.assertEqual(result["days"][1]["precip_prob"], 70)

    def test_forecast_error_response_degrades(self):
        geocode = _FakeResponse({"results": [{"latitude": 31.3, "longitude": 120.6}]})
        with patch("scripts.research_trip.urllib.request.urlopen", side_effect=[geocode, _FakeResponse({"error": True})]):
            result = fetch_daily_weather("苏州", "2026-09-26", "2026-09-28")
        self.assertEqual(result["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
