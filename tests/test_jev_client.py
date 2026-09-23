# -*- coding: utf-8 -*-
"""jev 素材可信度判断点：mock HTTP 全降级路径 + 默认关闭零调用。

不联网：所有 HTTP 走注入的 fake transport；research 集成走 fake client。
"""

import argparse
import copy
import json
import os
import unittest
from unittest.mock import patch

from scripts import jev_client
from scripts.research_trip import apply_jev_material_scoring, collect
from scripts.guide_utils import load_json

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def jev_body(score):
    return json.dumps({"answers": {"credibility": {"score": score}}})


class FakeJevClient:
    """模拟 JevClient 的 duck-typed 替身（不发起任何 HTTP）。"""

    def __init__(self, scores=None, limit=20, threshold=7.0, api_key="test-key"):
        self.scores = list(scores or [])
        self.limit = limit
        self.threshold = threshold
        self.api_key = api_key
        self.calls_used = 0
        self.degraded = False

    def score(self, state, instructions=None, criteria=None):
        if self.calls_used >= self.limit:  # 与 JevClient 一致：超限不消耗调用
            self.degraded = True
            return {
                "ok": False,
                "score": None,
                "degraded": True,
                "reason": jev_client.LIMIT_REACHED,
            }
        self.calls_used += 1
        score = self.scores.pop(0) if self.scores else 9.0
        return {"ok": True, "score": score, "degraded": False, "reason": None}


def make_destination():
    return {
        "destination": "苏州",
        "references": {
            "xiaohongshu": {
                "status": "ok",
                "notes": [
                    {
                        "rank": 1,
                        "title": "苏州2天1夜保姆级攻略",
                        "author": "someone",
                        "url": "https://xhslink.com/a",
                        "detail": {
                            "desc": "拙政园早上8点人少，平江路评弹馆下午2点有场次",
                            "images": [
                                {"alt_description": "拙政园 garden with pavilions"},
                                {"alt_description": " unrelated street food"},
                            ],
                        },
                    }
                ],
            }
        },
    }


class JevClientDegradationPaths(unittest.TestCase):
    """全部降级路径：无 key / 超时 / 5xx / 422 / 坏回包 / 超限。"""

    def test_no_key_degrades_without_any_http_call(self):
        def transport(*_args):
            raise AssertionError("no-key client must not touch the network")

        client = jev_client.JevClient(api_key="", transport=transport)
        result = client.score("任意素材")
        self.assertFalse(result["ok"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["reason"], jev_client.NO_KEY)
        self.assertTrue(client.degraded)
        self.assertEqual(client.calls_used, 0)

    def test_timeout_retries_once_then_degrades(self):
        attempts = []

        def transport(*_args):
            attempts.append(1)
            raise TimeoutError("timed out")

        client = jev_client.JevClient(api_key="k", transport=transport, limit=10)
        result = client.score("素材")
        self.assertEqual(result["reason"], jev_client.TIMEOUT)
        self.assertTrue(result["degraded"])
        self.assertEqual(len(attempts), 2)  # 首次 + 1 次重试
        self.assertEqual(client.calls_used, 2)

    def test_server_error_retries_then_degrades(self):
        attempts = []

        def transport(*_args):
            attempts.append(1)
            return 500, "boom"

        client = jev_client.JevClient(api_key="k", transport=transport, limit=10)
        result = client.score("素材")
        self.assertEqual(result["reason"], jev_client.HTTP_5XX)
        self.assertEqual(len(attempts), 2)

    def test_422_not_retried(self):
        attempts = []

        def transport(*_args):
            attempts.append(1)
            return 422, json.dumps({"detail": "model field required"})

        client = jev_client.JevClient(api_key="k", transport=transport, limit=10)
        result = client.score("素材")
        self.assertEqual(result["reason"], jev_client.HTTP_4XX)
        self.assertEqual(len(attempts), 1)  # 客户端错误不重试

    def test_invalid_response_degrades(self):
        def transport(*_args):
            return 200, "not-json"

        client = jev_client.JevClient(api_key="k", transport=transport, limit=10)
        result = client.score("素材")
        self.assertEqual(result["reason"], jev_client.INVALID_RESPONSE)
        self.assertEqual(client.calls_used, 2)  # 坏回包也重试一次

    def test_call_limit_triggers_degradation_on_next_call(self):
        def transport(*_args):
            return 200, jev_body(8.0)

        client = jev_client.JevClient(api_key="k", transport=transport, limit=2)
        self.assertTrue(client.score("素材1")["ok"])
        self.assertTrue(client.score("素材2")["ok"])
        third = client.score("素材3")  # 第 N+1 次调用触发降级
        self.assertFalse(third["ok"])
        self.assertEqual(third["reason"], jev_client.LIMIT_REACHED)
        self.assertTrue(client.degraded)
        self.assertEqual(client.calls_used, 2)  # 没有发出额外 HTTP 调用

    def test_retries_consume_call_budget(self):
        def transport(*_args):
            raise TimeoutError("timed out")

        client = jev_client.JevClient(api_key="k", transport=transport, limit=3)
        self.assertEqual(client.score("素材1")["reason"], jev_client.TIMEOUT)  # 2 attempts
        self.assertEqual(client.score("素材2")["reason"], jev_client.TIMEOUT)  # 3rd attempt, 预算尽
        self.assertEqual(client.score("素材3")["reason"], jev_client.LIMIT_REACHED)
        self.assertEqual(client.calls_used, 3)

    def test_success_parses_score_and_request_shape(self):
        seen = {}

        def transport(method, url, payload, headers, timeout):
            seen["method"] = method
            seen["url"] = url
            seen["body"] = json.loads(payload.decode("utf-8"))
            seen["auth"] = headers.get("Authorization")
            seen["timeout"] = timeout
            return 200, jev_body(8.5)

        client = jev_client.JevClient(api_key="k", transport=transport, timeout=5.0)
        result = client.score("素材", instructions="打分", criteria=["0-3 差"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["score"], 8.5)
        self.assertFalse(result["degraded"])
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["url"], jev_client.API_URL)
        self.assertEqual(seen["auth"], "Bearer k")
        self.assertEqual(seen["timeout"], 5.0)
        self.assertEqual(seen["body"]["model"], "jev-latest")  # 缺 model 会被 422
        question = seen["body"]["questions"]["credibility"]
        self.assertEqual(question["type"], "score")
        self.assertEqual(question["criteria"], ["0-3 差"])

    def test_numeric_answer_and_clamping(self):
        client = jev_client.JevClient(
            api_key="k",
            transport=lambda *_: (200, json.dumps({"answers": {"credibility": 99}})),
            limit=10,
        )
        self.assertEqual(client.score("素材")["score"], 10.0)

    def test_env_overrides(self):
        with patch.dict(os.environ, {"JEV_CALL_LIMIT": "5", "JEV_SCORE_THRESHOLD": "6.5"}):
            client = jev_client.JevClient(api_key="k", transport=lambda *_: (200, "{}"))
            self.assertEqual(client.limit, 5)
            self.assertEqual(client.threshold, 6.5)


class MaterialPointDefaultOff(unittest.TestCase):
    """默认关闭 = 零 jev 调用、产物与判断点不存在时完全一致。"""

    def setUp(self):
        jev_client.reset_shared_client()

    def test_collect_without_flag_makes_zero_jev_calls(self):
        def forbidden_transport(*_args):
            raise AssertionError("默认关闭时禁止任何 jev 网络调用")

        def fake_run_json(command, timeout=120):
            if "stations" in command:
                return {"ok": True, "data": [{"name": command[3], "code": "X"}]}
            if "search" in command:
                return {
                    "ok": True,
                    "data": {"notes": [{"title": "苏州攻略", "author": "a", "url": "u"}]},
                }
            if "note" in command:
                return {"ok": True, "data": {"desc": "细节"}}
            return {"ok": True, "data": []}

        args = argparse.Namespace(
            origin="宁波",
            destinations="苏州",
            start_date=None,
            return_date=None,
            days=3,
            nights=2,
            travelers=1,
            reference_platforms=["xiaohongshu"],
            reference_limit=8,
            reference_details=1,
            rail_limit=10,
            rail_details=False,
            depart_after=None,
            return_before=None,
        )
        env = {k: v for k, v in os.environ.items() if k != "JEV_POINT_MATERIAL"}
        env["JEV_POINT_MATERIAL"] = ""  # 显式关闭
        with patch.dict(os.environ, env, clear=True):
            with patch("scripts.research_trip.doctor", return_value={"status": "unavailable"}), \
                 patch("scripts.research_trip.run_json", side_effect=fake_run_json), \
                 patch(
                     "scripts.research_trip.fetch_daily_weather",
                     return_value={"status": "unavailable"},
                 ), \
                 patch.object(jev_client, "_urllib_transport", forbidden_transport), \
                 patch.object(
                     jev_client,
                     "JevClient",
                     side_effect=AssertionError("默认关闭时不得实例化 jev 客户端"),
                 ):
                result = collect(args)

        destination = result["destinations"][0]
        self.assertNotIn("jev", destination)
        self.assertNotIn("jev_alerts", destination)
        for note in destination["references"]["xiaohongshu"]["notes"]:
            self.assertNotIn("jev", note)
            self.assertNotIn("credibility", note)


class MaterialPointEnabled(unittest.TestCase):
    """开启后：低分素材降权 + JEV advisory 告警 + 图片门槛 + 降级记录。"""

    def setUp(self):
        jev_client.reset_shared_client()

    def test_low_score_material_flagged_untrusted_with_advisory_alert(self):
        # 评分顺序：先两张图片（达标），再素材本体（低分）
        client = FakeJevClient(scores=[8.0, 8.0, 3.0])
        destination = make_destination()
        summary, alerts = apply_jev_material_scoring(destination, client)

        note = destination["references"]["xiaohongshu"]["notes"][0]
        self.assertFalse(note["jev"]["adopted"])
        self.assertEqual(note["jev"]["trust"], "untrusted")
        self.assertIn("不可信", note["credibility"])
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert["source"], "JEV")
        self.assertEqual(alert["level"], "advisory")
        self.assertEqual(alert["type"], "素材可信度")
        self.assertFalse(summary["degraded"])
        self.assertEqual(summary["adopted"], 2)  # 两张图片达标计入，素材本体不计

    def test_high_score_material_adopted_without_alert(self):
        # 评分顺序：图1、图2、素材本体，全部达标 → adopted 合计 3
        client = FakeJevClient(scores=[9.0, 9.0, 9.0])
        destination = make_destination()
        summary, alerts = apply_jev_material_scoring(destination, client)

        note = destination["references"]["xiaohongshu"]["notes"][0]
        self.assertTrue(note["jev"]["adopted"])
        self.assertEqual(alerts, [])
        self.assertEqual(summary["adopted"], 3)

    def test_low_score_image_excluded_from_spot_image(self):
        client = FakeJevClient(scores=[2.0, 9.0, 9.0])  # 图1低分、图2高分、素材高分
        destination = make_destination()
        summary, alerts = apply_jev_material_scoring(destination, client)

        images = destination["references"]["xiaohongshu"]["notes"][0]["detail"]["images"]
        self.assertEqual(images[0]["jev_score"], 2.0)
        self.assertFalse(images[0]["adopted"])  # 低分图片不写入 spot.image
        self.assertEqual(images[1]["jev_score"], 9.0)
        self.assertTrue(images[1]["adopted"])
        image_alerts = [a for a in alerts if a["type"] == "图片筛选"]
        self.assertEqual(len(image_alerts), 1)
        self.assertEqual(image_alerts[0]["source"], "JEV")
        self.assertIn("spot.image", image_alerts[0]["title"])
        self.assertEqual(summary["adopted"], 2)  # 高分图 + 素材本体

    def test_no_key_records_explicit_degradation(self):
        client = FakeJevClient(api_key="")
        destination = make_destination()
        summary, alerts = apply_jev_material_scoring(destination, client)

        self.assertFalse(summary["ok"])
        self.assertTrue(summary["degraded"])
        self.assertEqual(summary["reason"], jev_client.NO_KEY)
        self.assertEqual(alerts, [])
        # 启发式孪生：产物不因 jev 缺失而失败，素材保持原样
        note = destination["references"]["xiaohongshu"]["notes"][0]
        self.assertNotIn("jev", note)

    def test_call_limit_degradation_recorded_and_heuristic_twin_continues(self):
        # 每条素材 3 次评分（2 图 + 1 本体）；limit=3 只够第 1 条
        client = FakeJevClient(limit=3)
        destination = make_destination()
        notes = destination["references"]["xiaohongshu"]["notes"]
        destination["references"]["xiaohongshu"]["notes"] = [
            copy.deepcopy(notes[0]) for _ in range(3)
        ]  # 3 条独立素材（每条 2 图 + 1 本体 = 3 次评分）
        summary, _alerts = apply_jev_material_scoring(destination, client)

        self.assertFalse(summary["ok"])
        self.assertTrue(summary["degraded"])
        self.assertEqual(summary["reason"], jev_client.LIMIT_REACHED)
        self.assertEqual(summary["calls"], 3)
        # 已评分的素材保留 jev 标记，其余退回启发式孪生（不抛异常、不破坏产物）
        notes = destination["references"]["xiaohongshu"]["notes"]
        self.assertTrue(notes[0]["jev"]["adopted"])
        self.assertNotIn("jev", notes[1])

    def test_collect_wires_jev_when_enabled(self):
        def fake_run_json(command, timeout=120):
            if "stations" in command:
                return {"ok": True, "data": [{"name": command[3], "code": "X"}]}
            if "search" in command:
                return {
                    "ok": True,
                    "data": {"notes": [{"title": "苏州攻略", "author": "a", "url": "u"}]},
                }
            if "note" in command:
                return {"ok": True, "data": {"desc": "细节"}}
            return {"ok": True, "data": []}

        args = argparse.Namespace(
            origin="宁波",
            destinations="苏州",
            start_date=None,
            return_date=None,
            days=3,
            nights=2,
            travelers=1,
            reference_platforms=["xiaohongshu"],
            reference_limit=8,
            reference_details=1,
            rail_limit=10,
            rail_details=False,
            depart_after=None,
            return_before=None,
        )
        with patch.dict(os.environ, {"JEV_POINT_MATERIAL": "1"}, clear=False):
            with patch("scripts.research_trip.doctor", return_value={"status": "unavailable"}), \
                 patch("scripts.research_trip.run_json", side_effect=fake_run_json), \
                 patch(
                     "scripts.research_trip.fetch_daily_weather",
                     return_value={"status": "unavailable"},
                 ), \
                 patch.object(jev_client, "JevClient", FakeJevClient):
                result = collect(args)

        destination = result["destinations"][0]
        self.assertIn("jev", destination)
        self.assertTrue(destination["jev"]["ok"])
        self.assertFalse(destination["jev"]["degraded"])
        note = destination["references"]["xiaohongshu"]["notes"][0]
        self.assertTrue(note["jev"]["adopted"])


class JevFullFixture(unittest.TestCase):
    """「jev 全开」夹具：happy 夹具 + jev 评分字段版。"""

    @classmethod
    def setUpClass(cls):
        cls.fixture = load_json(os.path.join(FIXTURES, "guide_jev_full.json"))

    def test_pipeline_jev_block_shape(self):
        jev = self.fixture["pipeline"]["jev"]
        self.assertTrue(jev["ok"])
        self.assertEqual(jev["limit"], 20)
        self.assertEqual(jev["threshold"], 7)
        self.assertFalse(jev["degraded"])

    def test_all_spot_images_carry_jev_score(self):
        images = [spot["image"] for spot in self.fixture["spots"] if "image" in spot]
        self.assertTrue(images)
        for image in images:
            for key in ("url", "source", "credibility", "alt_description", "jev_score"):
                self.assertIn(key, image)
            self.assertTrue(0 <= image["jev_score"] <= 10)

    def test_jev_alerts_use_jev_source(self):
        jev_alerts = [a for a in self.fixture["alerts"] if a["source"] == "JEV"]
        self.assertTrue(jev_alerts)
        for alert in jev_alerts:
            self.assertIn("type", alert)
            self.assertIn("title", alert)


if __name__ == "__main__":
    unittest.main()
