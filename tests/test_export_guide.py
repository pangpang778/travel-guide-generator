"""T8（#10）.ics 日历导出 + T9（#11）小红书图文导出验收测试。

- 幸福路径夹具 guide_happy.json：v2 字段全在场（含 days/intercity），事件精确时刻、车次入文案
- 降级夹具 guide_minimal.json：无 12306 车次/坐标，仍产出建议时段事件与「数据不全」简版
"""

import copy
import tempfile
import unittest
from pathlib import Path

from scripts.export_guide import export_all, ics_text, xhs_tags, xhs_text
from scripts.guide_utils import load_json

SAMPLE = load_json(Path("examples") / "sample-guide.json")
HAPPY = load_json(Path("tests") / "fixtures" / "guide_happy.json")
MINIMAL = load_json(Path("tests") / "fixtures" / "guide_minimal.json")

HAPPY_SPOTS = [spot["name"] for spot in HAPPY["spots"]]
HAPPY_TW = [spot["tw"] for spot in HAPPY["spots"]]


class IcsTests(unittest.TestCase):
    def test_happy_events_split_by_day_with_timezone(self):
        ics = ics_text(HAPPY)
        # 9 个行程点 + 1 个跨城高铁段
        self.assertEqual(ics.count("BEGIN:VEVENT"), 10)
        for date_value in ("20261016", "20261017", "20261018"):
            self.assertIn(
                "DTSTART;TZID=Asia/Shanghai:{}T".format(date_value), ics
            )
        for line in ics.splitlines():
            if line.startswith("DTSTART") and not line.startswith("DTSTART:19700101T000000"):
                self.assertIn("TZID=", line)
        # TZID + VTIMEZONE 同时在场，主流日历 app 可直接导入
        self.assertIn("TZID:Asia/Shanghai", ics)
        self.assertIn("BEGIN:VTIMEZONE", ics)
        self.assertIn("TZOFFSETTO:+0800", ics)

    def test_happy_intercity_event_carries_train_and_times(self):
        ics = ics_text(HAPPY)
        self.assertIn("SUMMARY:🚄 G1836 郑州东→西安北", ics)
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20261016T113300", ics)
        self.assertIn("DTEND;TZID=Asia/Shanghai:20261016T140500", ics)
        self.assertIn("DESCRIPTION:郑西高铁 · 2小时32分 · 票价 240", ics)

    def test_sample_uses_precise_item_times_and_hotel_checkin(self):
        ics = ics_text(SAMPLE)
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20260920T080000", ics)
        self.assertIn("DTEND;TZID=Asia/Shanghai:20260920T083000", ics)
        self.assertIn("SUMMARY:🏨 入住 红门附近", ics)
        self.assertIn("SUMMARY:🚄 高铁到泰安站", ics)
        self.assertEqual(ics.count("BEGIN:VEVENT"), 5)

    def test_minimal_still_produces_usable_calendar(self):
        ics = ics_text(MINIMAL)
        self.assertEqual(ics.count("BEGIN:VEVENT"), 4)
        for name in ("红门游客中心", "中天门", "南天门与天街"):
            self.assertIn(name, ics)
        # 非 recommended 的 12306 空结果不应变成事件
        self.assertNotIn("暂无直达样本", ics)

    def test_missing_times_get_suggested_slots(self):
        guide = copy.deepcopy(MINIMAL)
        for item in guide["days"][0]["items"]:
            item.pop("start", None)
            item.pop("end", None)
        ics = ics_text(guide)
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20260920T090000", ics)
        self.assertIn("DTEND;TZID=Asia/Shanghai:20260920T110000", ics)

    def test_lines_are_rfc5545_folded(self):
        for ics in (ics_text(HAPPY), ics_text(SAMPLE)):
            for line in ics.splitlines():
                self.assertLessEqual(len(line.encode("utf-8")), 75)
            self.assertTrue(ics.endswith("\r\n"))


class XhsTests(unittest.TestCase):
    def test_happy_full_structure(self):
        text = xhs_text(HAPPY)
        title = text.splitlines()[0]
        self.assertLessEqual(len(title), 20)
        self.assertIn("西安", title)
        self.assertTrue(any(emoji in title for emoji in ("🚄", "⛰", "✨", "📸")))
        # 分日文案：行程点名与时间窗逐一出现，关键事实未走样
        for name in HAPPY_SPOTS:
            self.assertIn(name, text)
        for window in HAPPY_TW:
            self.assertIn(window, text)
        self.assertIn("G1836", text)
        self.assertIn("11:33", text)
        self.assertIn("14:05", text)
        self.assertNotIn("数据不全", text)
        tags = [word for word in text.split() if word.startswith("#")]
        self.assertTrue(5 <= len(tags) <= 8)
        self.assertIn("#西安旅游", tags)
        # 无编造：行程 bullet 只引用 schema 里的行程点名
        known = set(HAPPY_SPOTS)
        bullets = [line for line in text.splitlines() if line.startswith("· ")]
        self.assertEqual(len(bullets), 9)
        for bullet in bullets:
            self.assertTrue(any(name in bullet for name in known), bullet)

    def test_sample_facts_survive(self):
        text = xhs_text(SAMPLE)
        for name in ("红门游客中心", "中天门", "南天门与天街"):
            self.assertIn(name, text)
        self.assertIn("08:00-08:30", text)
        self.assertNotIn("数据不全", text)
        tags = [word for word in text.split() if word.startswith("#")]
        self.assertTrue(5 <= len(tags) <= 8)

    def test_minimal_marked_incomplete(self):
        text = xhs_text(MINIMAL)
        self.assertIn("数据不全", text)
        for name in ("红门游客中心", "中天门", "南天门与天街"):
            self.assertIn(name, text)
        tags = [word for word in text.split() if word.startswith("#")]
        self.assertGreaterEqual(len(tags), 5)

    def test_tags_five_to_eight_for_all_fixtures(self):
        for guide in (HAPPY, SAMPLE, MINIMAL):
            count = len(xhs_tags(guide).split())
            self.assertTrue(5 <= count <= 8)


class ExportFilesTests(unittest.TestCase):
    def test_export_all_writes_xhs_file_without_touching_html(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "guide"
            files = export_all(HAPPY, base)
            self.assertEqual(len(files), 4)
            xhs_path = base.with_suffix(".xhs.md")
            self.assertTrue(xhs_path.exists())
            self.assertEqual(xhs_path.read_text(encoding="utf-8"), xhs_text(HAPPY))
            self.assertTrue(base.with_suffix(".ics").exists())
            self.assertTrue(base.with_suffix(".md").exists())
            # xhs 是独立产物，不污染其他导出文件
            self.assertNotIn("小红书", base.with_suffix(".md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
