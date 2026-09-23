"""T5（#7）HTML 增强验收：排版/暗色/移动端/徽章/Tab/详情面板/图片直出/水印。

- 暗色模式默认态 + 切换钮 + URL 参数
- 移动端 375px：viewport meta + 响应式规则在场
- confidence 徽章三类（realtime/verified/estimated）三色三形
- Tab 分区：总览/各天/预算/告警，noscript 与打印全显兜底
- 行程点可展开详情面板（原生 details，介绍/营业时间/门票/理由）
- spots[].image 直出 + untrusted 标记 + __imgFail 降级；无图文化占位
- validate_guide watermark（blocking 未清空）→「未校验通过」水印
- 13 语言渲染不回归（render_language_attributes 未动，RTL 夹具照常）
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.guide_utils import load_json
from scripts.render_guide import render_html

ROOT = Path(__file__).parents[1]
TEMPLATE = (ROOT / "assets" / "template.html").read_text(encoding="utf-8")
HAPPY = load_json(ROOT / "tests" / "fixtures" / "guide_happy.json")
MINIMAL = load_json(ROOT / "tests" / "fixtures" / "guide_minimal.json")


def render(guide):
    return render_html(guide, TEMPLATE)


def copy_with(guide, mutate):
    copy = json.loads(json.dumps(guide))
    mutate(copy)
    return copy


class TypographyAndViewportTests(unittest.TestCase):
    """验收：排版升级（字体配对/字距 token）+ 移动端 375px 无溢出。"""

    def test_viewport_meta_present(self):
        self.assertIn('name="viewport"', TEMPLATE)
        self.assertIn("width=device-width", TEMPLATE)

    def test_no_horizontal_overflow_guards(self):
        self.assertIn("overflow-x: hidden", TEMPLATE)
        self.assertIn("@media (max-width: 375px)", TEMPLATE)

    def test_font_pairing_tokens(self):
        # 展示字体与正文字体分层的配对结构（品牌可覆盖）
        self.assertIn("--gd-font-display:", TEMPLATE)
        self.assertIn("--gd-font:", TEMPLATE)
        self.assertIn("font-family: var(--gd-font-display)", TEMPLATE)
        self.assertIn("letter-spacing: var(--gd-caps)", TEMPLATE)


class DarkModeTests(unittest.TestCase):
    """验收：默认暗色 + 切换钮/URL 参数，两个主题都有设计感（token 化双态）。"""

    @classmethod
    def setUpClass(cls):
        cls.html = render(HAPPY)

    def test_dark_is_default(self):
        self.assertRegex(self.html, r'<html [^>]*data-theme="dark"')

    def test_both_theme_token_blocks_defined(self):
        self.assertIn('html[data-theme="dark"]', TEMPLATE)
        self.assertIn('html[data-theme="light"]', TEMPLATE)

    def test_toggle_button_and_persistence(self):
        self.assertIn('id="theme-toggle"', self.html)
        self.assertIn('localStorage.getItem("guide_theme")', self.html)
        self.assertIn('localStorage.setItem("guide_theme"', self.html)
        self.assertIn('get("theme")', self.html)  # URL ?theme= 参数


class ConfidenceBadgeTests(unittest.TestCase):
    """验收：三类徽章视觉可辨（三色/三形），CSS 类在场。"""

    def test_confidence_section_rendered(self):
        guide = copy_with(HAPPY, lambda g: g.update(
            confidence={"transport": "realtime", "weather": "verified", "budget": "estimated"}))
        html = render(guide)
        for cls in ("conf-realtime", "conf-verified", "conf-estimated"):
            self.assertIn(cls, html, cls)

    def test_three_distinct_shapes_and_colors_in_css(self):
        # realtime 圆点 / verified 药丸描边 / estimated 虚线方块
        self.assertIn(".conf-realtime i { border-radius: 50%;", TEMPLATE)
        self.assertIn('.conf-verified {', TEMPLATE)
        self.assertIn("border: 1px dashed", TEMPLATE)
        self.assertIn("transform: rotate(45deg)", TEMPLATE)

    def test_no_confidence_section_without_field(self):
        self.assertNotIn('<section class="confidence-section"', render(MINIMAL))


class TabSectionTests(unittest.TestCase):
    """验收：总览/各天/预算/告警 Tab 分区；与打印/无 JS 场景兼容。"""

    def test_four_tabs_and_panels(self):
        for tab in ("overview", "days", "budget", "alerts"):
            self.assertIn('data-tab="%s"' % tab, TEMPLATE, tab)
            self.assertIn('id="tab-%s"' % tab, TEMPLATE, tab)

    def test_no_js_fallback_shows_all_panels(self):
        self.assertIn("<noscript>", TEMPLATE)
        self.assertIn(".tab-panel{display:block!important}", TEMPLATE)

    def test_print_fallback_shows_all_panels(self):
        self.assertIn("@media print {", TEMPLATE)
        self.assertIn(".tab-panel { display: block !important; }", TEMPLATE)
        self.assertIn(".tab-nav, .theme-toggle", TEMPLATE)

    def test_alerts_rendered_into_alerts_tab(self):
        html = render(HAPPY)
        self.assertIn('id="tab-alerts"', html)
        self.assertIn("alerts-section", html)
        self.assertIn("alert-jev", html)  # happy 夹具 JEV 告警
        self.assertIn("告警与筛选披露", html)


class ItemDetailPanelTests(unittest.TestCase):
    """验收：行程点可展开详情面板（介绍/营业时间/门票/理由）。"""

    def test_happy_items_have_expandable_details(self):
        html = render(HAPPY)
        self.assertIn('<details class="item-detail">', html)
        self.assertIn("<summary>详情</summary>", html)

    def test_ev_style_fields_flow_into_panel(self):
        def mutate(g):
            g["days"][0]["items"][0].update(
                {"description": "世界第八大奇迹", "hours": "08:30-18:00", "ticket": "¥120", "why": "秦文化地标"})
        html = render(copy_with(HAPPY, mutate))
        for fragment in ("世界第八大奇迹", "08:30-18:00", "¥120", "秦文化地标", "<dt>门票</dt>", "<dt>营业时间</dt>"):
            self.assertIn(fragment, html, fragment)

    def test_minimal_items_still_get_panel(self):
        # minimal 条目含 description → 面板照常（无图 → 文化占位）
        html = render(MINIMAL)
        self.assertIn('<details class="item-detail">', html)
        self.assertIn("文化占位", html)


class SpotImageTests(unittest.TestCase):
    """验收：spots[].image 直出 + untrusted 标记 + __imgFail 降级；无图文化占位。"""

    @classmethod
    def setUpClass(cls):
        cls.html = render(HAPPY)

    def test_image_direct_render_with_source_mark(self):
        self.assertIn("item-media", self.html)
        self.assertIn('onerror="window.__imgFail(this)"', self.html)
        self.assertIn("untrusted", self.html)
        self.assertIn("jev-mark", self.html)  # jev 评分徽章
        self.assertIn("xhs-images/", self.html)

    def test_imgfail_placeholder_mode_kept(self):
        self.assertIn("window.__imgFail = function", self.html)
        self.assertIn('img-ph', self.html)

    def test_no_image_falls_back_to_cultural_placeholder(self):
        html = render(MINIMAL)
        self.assertIn("无达标图 · 文化占位", html)


class WatermarkTests(unittest.TestCase):
    """验收：watermark（blocking 未清空）→「未校验通过」水印；干净产物无水印。"""

    def test_clean_guide_has_no_watermark(self):
        self.assertNotIn('<div class="unverified-watermark"', render(HAPPY))
        self.assertNotIn('<div class="unverified-watermark"', render(MINIMAL))

    def test_blocking_conflict_shows_watermark(self):
        # 复用 test_build_guide 的硬规则触发法：时间窗非法 → blocking → watermark=True
        guide = copy_with(HAPPY, lambda g: g["days"][0]["items"][1].update({"start": "08:31"}))
        html = render(guide)
        self.assertIn("unverified-watermark", html)
        self.assertIn("未校验通过", html)


class LanguageRegressionTests(unittest.TestCase):
    """验收：多语言渲染不回归（含 RTL）。"""

    def test_rtl_language_renders_with_dir(self):
        guide = copy_with(MINIMAL, lambda g: g["meta"].update({"language": "ar-SA"}))
        html = render(guide)
        self.assertIn('lang="ar-SA" dir="rtl" data-theme="dark"', html)

    def test_plain_language_renders(self):
        guide = copy_with(MINIMAL, lambda g: g["meta"].update({"language": "ja-JP"}))
        self.assertIn('lang="ja-JP"', render(guide))


class JsIntegrityTests(unittest.TestCase):
    """工程要求：渲染产物内联 JS 过 node --check；地图模块坑位修法保留。"""

    def test_inline_scripts_pass_node_check(self):
        if not shutil.which("node"):
            self.skipTest("node 不可用")
        html = render(HAPPY)
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        self.assertTrue(scripts)
        for index, script in enumerate(scripts):
            path = Path(ROOT) / ".omc" / "tmp-js-check-{}.js".format(index)
            path.parent.mkdir(exist_ok=True)
            path.write_text("var window={},document={localStorage:{getItem:function(){return null},setItem:function(){},removeItem:function(){}},createElement:function(){return{style:{},addEventListener:function(){}}},head:{appendChild:function(){}},addEventListener:function(){},querySelectorAll:function(){return[]},getElementById:function(){return null}},location={search:''},URLSearchParams=function(){this.get=function(){return ''}};"
                            + script, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            path.unlink()
            self.assertEqual(result.returncode, 0, "script {} 失败:\n{}".format(index, result.stderr))

    def test_no_placeholder_residue(self):
        # 工程要求：渲染产物无 {{ 占位符残留（str.replace 全量替换，
        # 模板注释里也不能写字面占位符，否则会被注入内容撑破注释）
        for guide in (HAPPY, MINIMAL):
            html = render(guide)
            self.assertNotIn("{{", html)

    def test_map_pitfall_fixes_untouched(self):
        html = render(HAPPY)
        for fragment in ("map: amapMap", "AMap.plugin(\"AMap.MoveAnimation\"", "showDir: true", "window.__imgFail"):
            self.assertIn(fragment, html, fragment)


if __name__ == "__main__":
    unittest.main()
