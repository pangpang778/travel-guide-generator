# -*- coding: utf-8 -*-
"""jev 行程合理性判断点（T12）：每天整体评估一次，mock 全降级路径。

不联网：全部走 FakeJevClient；与素材点（T11）逐点独立、共享限次预算。
"""

import argparse
import os
import unittest
from unittest.mock import patch

from scripts import jev_client
from scripts.plan_trip import apply_jev_plausibility_scoring, guide_from_research
from scripts.research_trip import apply_jev_material_scoring, collect
from test_jev_client import FakeJevClient, make_destination


def make_guide(days=3):
    """最小 guide：3 天 × 2 个行程点，走 apply_jev_plausibility_scoring。"""
    return {
        "meta": {"destination": "苏州"},
        "days": [
            {
                "day": index + 1,
                "title": "第{}天".format(index + 1),
                "items": [
                    {
                        "name": "拙政园",
                        "type": "activity",
                        "start": "09:00",
                        "end": "11:30",
                    },
                    {
                        "name": "平江路",
                        "type": "activity",
                        "start": "13:00",
                        "end": "15:00",
                    },
                ],
            }
            for index in range(days)
        ],
    }


def make_research(days=3):
    return {
        "collected_at": "2026-09-26",
        "request": {
            "origin": "宁波",
            "start_date": "2026-09-26",
            "days": days,
            "nights": days - 1,
            "travelers": 1,
        },
        "sources": [],
        "destinations": [],
    }


def make_candidate():
    return {"destination": "苏州", "references": {}, "transport": {"status": "error"}, "warnings": []}


class PlausibilityScoringTests(unittest.TestCase):
    """apply_jev_plausibility_scoring：每天一次 + advisory 告警结构 + 降级。"""

    def test_one_call_per_day(self):
        client = FakeJevClient()
        summary, alerts = apply_jev_plausibility_scoring(make_guide(days=3), client)
        self.assertEqual(client.calls_used, 3)  # 3 天行程 = 3 次调用
        self.assertEqual(summary["days_evaluated"], 3)
        self.assertEqual(alerts, [])
        self.assertFalse(summary["degraded"])

    def test_low_score_day_emits_advisory_alert_with_contract_shape(self):
        client = FakeJevClient(scores=[3.0, 9.0, 9.0])
        summary, alerts = apply_jev_plausibility_scoring(make_guide(days=3), client)

        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert["source"], "JEV")
        self.assertEqual(alert["level"], "advisory")
        self.assertEqual(alert["type"], "行程合理性")
        self.assertIn("Day 1", alert["title"])
        self.assertIn("3.0", alert["title"])
        self.assertTrue(alert["detail"])  # 打包了当天 items 序列
        self.assertEqual(summary["days_evaluated"], 3)  # 低分不中断后续天

    def test_high_scores_produce_no_alerts(self):
        client = FakeJevClient(scores=[8.0, 8.5, 9.0])
        summary, alerts = apply_jev_plausibility_scoring(make_guide(days=3), client)
        self.assertEqual(alerts, [])
        self.assertEqual(summary["days_evaluated"], 3)

    def test_no_key_degrades_explicitly_with_zero_calls(self):
        client = FakeJevClient(api_key="")
        summary, alerts = apply_jev_plausibility_scoring(make_guide(days=3), client)
        self.assertFalse(summary["ok"])
        self.assertTrue(summary["degraded"])
        self.assertEqual(summary["reason"], jev_client.NO_KEY)
        self.assertEqual(client.calls_used, 0)
        self.assertEqual(alerts, [])

    def test_timeout_degradation_recorded_and_remaining_days_keep_heuristic_twin(self):
        client = FakeJevClient()
        client.score = None  # 替换成会降级的 duck-typed score

        def score(state, instructions=None, criteria=None):
            if client.calls_used >= client.limit:
                return {"ok": False, "score": None, "degraded": True, "reason": jev_client.LIMIT_REACHED}
            client.calls_used += 1
            return {"ok": False, "score": None, "degraded": True, "reason": jev_client.TIMEOUT}

        client.score = score
        summary, alerts = apply_jev_plausibility_scoring(make_guide(days=3), client)
        self.assertTrue(summary["degraded"])
        self.assertEqual(summary["reason"], jev_client.TIMEOUT)
        self.assertEqual(summary["calls"], 1)  # 第一天失败即停，其余天走启发式孪生
        self.assertEqual(alerts, [])


class SharedBudgetTests(unittest.TestCase):
    """共享限次：素材点用掉 N 次，合理性点最多还能用 limit-N 次。"""

    def test_material_consumes_budget_before_plausibility(self):
        client = FakeJevClient(limit=5)  # make_destination：2 图 + 1 本体 = 3 次
        material_summary, _ = apply_jev_material_scoring(make_destination(), client)
        self.assertEqual(material_summary["calls"], 3)

        plausibility_summary, _ = apply_jev_plausibility_scoring(make_guide(days=3), client)
        self.assertEqual(plausibility_summary["calls"], 5)  # 只够评 2 天
        self.assertEqual(client.calls_used, 5)  # 两判断点合计不超上限
        self.assertTrue(plausibility_summary["degraded"])
        self.assertEqual(plausibility_summary["reason"], jev_client.LIMIT_REACHED)
        self.assertEqual(plausibility_summary["days_evaluated"], 2)

    def test_reverse_order_also_shares_budget(self):
        client = FakeJevClient(limit=4)
        plausibility_summary, _ = apply_jev_plausibility_scoring(make_guide(days=3), client)
        self.assertEqual(plausibility_summary["calls"], 3)

        material_summary, _ = apply_jev_material_scoring(make_destination(), client)
        self.assertTrue(material_summary["degraded"])  # 只剩 1 次预算，图 1 评完即超限
        self.assertEqual(material_summary["reason"], jev_client.LIMIT_REACHED)
        self.assertEqual(client.calls_used, 4)


class PointIndependenceTests(unittest.TestCase):
    """逐点独立性：只开一个判断点时，另一个零调用、零告警。"""

    def setUp(self):
        jev_client.reset_shared_client()

    def _collect_args(self):
        return argparse.Namespace(
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

    @staticmethod
    def _fake_run_json(command, timeout=120):
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

    def test_plausibility_only_material_point_makes_zero_calls(self):
        env = dict(os.environ)
        env["JEV_POINT_PLAUSIBILITY"] = "1"
        env["JEV_POINT_MATERIAL"] = ""  # 显式关闭素材点
        with patch.dict(os.environ, env, clear=True):
            with patch("scripts.research_trip.doctor", return_value={"status": "unavailable"}), \
                 patch("scripts.research_trip.run_json", side_effect=self._fake_run_json), \
                 patch(
                     "scripts.research_trip.fetch_daily_weather",
                     return_value={"status": "unavailable"},
                 ), \
                 patch.object(
                     jev_client,
                     "JevClient",
                     side_effect=AssertionError("素材点关闭时不得实例化 jev 客户端"),
                 ):
                result = collect(self._collect_args())

        destination = result["destinations"][0]
        self.assertNotIn("jev", destination)  # 素材点零调用、零告警
        self.assertNotIn("jev_alerts", destination)

    def test_material_only_plausibility_makes_zero_calls_and_no_alerts(self):
        env = dict(os.environ)
        env["JEV_POINT_MATERIAL"] = "1"
        env["JEV_POINT_PLAUSIBILITY"] = ""  # 显式关闭合理性点
        with patch.dict(os.environ, env, clear=True):
            with patch.object(jev_client, "JevClient", FakeJevClient):
                guide = guide_from_research(make_research(), make_candidate())

        self.assertNotIn("jev_plausibility", guide)  # 合理性点零调用、零告警
        for alert in guide.get("alerts") or []:
            self.assertNotEqual(alert["source"], "JEV")

    def test_wiring_via_guide_from_research(self):
        env = dict(os.environ)
        env["JEV_POINT_PLAUSIBILITY"] = "1"
        env["JEV_POINT_MATERIAL"] = ""
        client = FakeJevClient(scores=[9.0, 2.0, 9.0])
        with patch.dict(os.environ, env, clear=True):
            with patch.object(jev_client, "shared_client", return_value=client):
                guide = guide_from_research(make_research(), make_candidate())

        summary = guide["jev_plausibility"]
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["days_evaluated"], 3)
        self.assertEqual(client.calls_used, 3)  # 3 天 = 3 次调用
        jev_alerts = [a for a in guide.get("alerts") or [] if a["source"] == "JEV"]
        self.assertEqual(len(jev_alerts), 1)
        self.assertEqual(jev_alerts[0]["type"], "行程合理性")


if __name__ == "__main__":
    unittest.main()
