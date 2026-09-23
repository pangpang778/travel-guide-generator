"""T2（#4）地图渲染验收：高德 SDK 内嵌 + 无 key 静态降级。

- 幸福路径夹具渲染出含地图容器与 marker/路线数据的 HTML（不联网、不调真 API）
- 每天路线独立配色（dayColors），marker 点击 InfoWindow
- 最小降级夹具（纯 v1，无坐标）渲染出静态降级图 + 显式降级说明
- 地图数据只经 window.__GUIDE_SCHEMA__（schema 单缝）进出
- AMAP_KEY 引导卡片 + localStorage('amap_key')，正式版无演示 key
- 已实锤高德 JSAPI 坑位在渲染端逐一落位（构造挂图 / setFitView 数组 / MoveAnimation / showDir）
"""

import json
import re
import unittest
from pathlib import Path

from scripts.guide_utils import load_json
from scripts.render_guide import render_html

ROOT = Path(__file__).parents[1]
TEMPLATE = (ROOT / "assets" / "template.html").read_text(encoding="utf-8")
HAPPY = load_json(ROOT / "tests" / "fixtures" / "guide_happy.json")
MINIMAL = load_json(ROOT / "tests" / "fixtures" / "guide_minimal.json")

# 上一原型的演示 key，正式版严禁内置（坑位 6）
DEMO_KEY = "d3d68a8d1fe35a03053ee6bc7e5d448e"


def render(guide):
    return render_html(guide, TEMPLATE)


def extract_schema(html):
    """从渲染产物提取内嵌 schema（验证单缝完整内嵌）。"""
    match = re.search(r"window\.__GUIDE_SCHEMA__ = (.*?);</script>", html, re.S)
    assert match, "window.__GUIDE_SCHEMA__ 未内嵌"
    return json.loads(match.group(1).replace("<\\/", "</"))


class HappyPathMapTests(unittest.TestCase):
    """验收 #1/#2：幸福路径夹具 → 地图容器 + marker/路线数据 + 按天配色。"""

    @classmethod
    def setUpClass(cls):
        cls.html = render(HAPPY)

    def test_map_container_and_skeleton(self):
        for fragment in (
            'id="map-card"',
            'id="map-area"',
            'id="key-guide"',
            'id="amap-key-input"',
            'id="amap-go"',
            'id="map-ctrl-row"',
            'id="replay-btn"',
            'id="label-toggle"',
            'id="map-legend"',
            'id="map-status"',
        ):
            self.assertIn(fragment, self.html, fragment)

    def test_marker_and_route_data_embedded(self):
        schema = extract_schema(self.html)
        self.assertEqual(len(schema["spots"]), 9)
        self.assertEqual(schema["spots"][0]["ll"], [109.2785, 34.3842])
        self.assertEqual(len(schema["routes"]), 3)
        self.assertEqual(schema["routes"][0]["pts"][0], [109.2785, 34.3842])
        self.assertEqual(schema["dayColors"], {"1": "#da291c", "2": "#ffffff", "3": "#969696"})

    def test_day_colors_drive_css_variables(self):
        # dayColors → --gd-day-N CSS 变量 + 图例按 dayColors 渲染
        self.assertIn('"--gd-day-" + d', self.html)
        self.assertIn("--gd-accent", self.html)
        self.assertIn('style="background:' + "' + dayColor(d) + '" + '"', self.html)

    def test_infowindow_uses_schema_fields_only(self):
        # InfoWindow 内容只来自 schema：name/tw/day + days[].items[] 匹配 + image + 导航 deep-link
        self.assertIn("tw-info", self.html)
        self.assertIn("uri.amap.com/marker?position=", self.html)
        self.assertIn("window.__imgFail(this)", self.html)
        self.assertIn("byName[it.name] = it", self.html)

    def test_jsapi_pitfalls_guarded(self):
        self.assertIn('map: amapMap', self.html)  # 坑位1/4：构造挂图
        self.assertIn("amapMap.setFitView(fit, false, [60, 60, 60, 60])", self.html)  # 坑位2：overlay 数组
        self.assertIn('AMap.plugin("AMap.MoveAnimation"', self.html)  # 坑位5
        self.assertIn("showDir: true", self.html)  # intercity 流动箭头
        self.assertIn("ov.show(); } else { ov.hide(); }", self.html)  # 按天 show/hide 分组

    def test_interactions_present(self):
        # 回放 / 标签开关 / zoom 分级 / marker 分级 / 卡片飞行
        self.assertIn("▶ 回放", self.html)
        self.assertIn("标签 " + '" + (twOn ? "开" : "关")', self.html)
        self.assertIn("amapMap.getZoom() < 9", self.html)
        self.assertIn("mins(parts[1]) - mins(parts[0]) >= 120", self.html)
        self.assertIn("setZoomAndCenter(13, sp.ll, false, 400)", self.html)


class MinimalDegradedTests(unittest.TestCase):
    """验收 #3：最小降级夹具（纯 v1，无任何 v2 字段）→ 静态降级图 + 显式说明。"""

    @classmethod
    def setUpClass(cls):
        cls.html = render(MINIMAL)

    def test_still_renders_map_skeleton(self):
        for fragment in ('id="map-card"', 'id="map-area"', 'id="key-guide"', "localStorage"):
            self.assertIn(fragment, self.html, fragment)

    def test_explicit_degradation_notes(self):
        # 有坐标但无 key → 静态路线图 + 引导；无坐标（v1）→ 显式说明，不伪造
        self.assertIn("静态路线图已就绪", TEMPLATE)
        self.assertIn("本攻略未包含坐标与路线数据", TEMPLATE)
        self.assertIn(" spots 缺少 SVG 示意图坐标", TEMPLATE)

    def test_no_v2_data_leaks_into_minimal(self):
        self.assertNotIn("兵马俑", self.html)
        self.assertNotIn("dayColors", MINIMAL)
        schema = extract_schema(self.html)
        self.assertNotIn("spots", schema)
        self.assertNotIn("routes", schema)

    def test_full_build_pipeline_unchanged(self):
        # build 全管线（render_file）对最小降级夹具照常出 HTML，且含地图骨架
        import tempfile

        from scripts.render_guide import render_file

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "guide.html"
            render_file(MINIMAL, out, allow_invalid=True)
            html = out.read_text(encoding="utf-8")
        self.assertIn('id="map-card"', html)
        self.assertIn("window.__GUIDE_SCHEMA__", html)


class SchemaSeamTests(unittest.TestCase):
    """验收 #4/#6：数据只经 schema 字段进出；schema JSON 完整内嵌。"""

    def test_embedded_schema_round_trips(self):
        schema = extract_schema(render(HAPPY))
        self.assertEqual(schema, HAPPY)

    def test_schema_field_change_flows_through(self):
        guide = json.loads(json.dumps(HAPPY))
        guide["spots"][0]["name"] = "单缝验证点"
        html = render(guide)
        self.assertIn("单缝验证点", html)

    def test_intercity_consumed_from_schema_when_present(self):
        guide = json.loads(json.dumps(HAPPY))
        guide["intercity"] = {
            "from": {"name": "郑州东", "time": "09:12", "ll": [113.6254, 34.7466]},
            "to": {"name": "西安北", "time": "11:33", "ll": [108.9377, 34.3764]},
            "line": "徐兰高铁",
            "duration": "2h21m",
            "price": "¥239",
            "train_no": "G1901",
        }
        html = render(guide)
        self.assertIn("徐兰高铁", html)  # 完整内嵌，渲染端 buildHsr() 消费 S.intercity
        self.assertIn("buildHsr()", html)
        self.assertIn("S.intercity", html)

    def test_key_guidance_local_storage_only(self):
        html = render(HAPPY)
        self.assertIn("amap_key", html)
        self.assertIn('localStorage.getItem("amap_key")', html)
        self.assertIn('localStorage.setItem("amap_key"', html)
        self.assertNotIn(DEMO_KEY, html)  # 正式版不内置演示 key

    def test_destination_identity_embedded_and_brand_attributed(self):
        html = render(HAPPY)
        self.assertIn('"brand_id"', html)
        self.assertIn('data-brand-id', html)
        self.assertIn("ferrari", html)
        # CSS 变量可被字段驱动（dayColors → --gd-day-N 运行时注入；主题切换留给 #15）
        self.assertIn('"--gd-day-" + d', html)


class TemplateConsistencyTests(unittest.TestCase):
    def test_no_unfilled_placeholders_after_render(self):
        for guide in (HAPPY, MINIMAL):
            self.assertNotIn("{{", render(guide))

    def test_fallback_svg_is_first_class(self):
        # 一等公民降级：街区块 + 网格 + 河流装饰 + 图例
        for fragment in ("svg-block", "svg-grid", "svg-river", "legend-dot", "行程静态路线图"):
            self.assertIn(fragment, TEMPLATE, fragment)


if __name__ == "__main__":
    unittest.main()
