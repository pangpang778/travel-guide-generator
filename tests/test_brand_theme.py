"""T13（#15）destination_identity 主题驱动验收。

- 渲染端按 destination_identity.brand_id 切主题：内置注册表 ferrari/airbnb/theverge
  （token 取值注明 awesome-design-md 出处，库外不发明）
- happy 夹具（ferrari）→ CSS 变量值来自字段映射（data-brand-id="ferrari" → --gd-primary:#da291c）
- minimal 夹具无字段 → 静默回落默认主题，产物不失败
- 同一 schema 换 brand_id → 主色变量值不同
- 库外 brand_id → 回落默认 + 页面显式标注「库外设计已回落」（spec #16 不合格判定）
"""

import json
import unittest
from pathlib import Path

from scripts.guide_utils import load_json
from scripts.render_guide import BRAND_THEMES, KNOWN_BRANDS, render_html

ROOT = Path(__file__).parents[1]
TEMPLATE = (ROOT / "assets" / "template.html").read_text(encoding="utf-8")
HAPPY = load_json(ROOT / "tests" / "fixtures" / "guide_happy.json")
MINIMAL = load_json(ROOT / "tests" / "fixtures" / "guide_minimal.json")


def render(guide):
    return render_html(guide, TEMPLATE)


def with_brand(guide, brand_id):
    copy = json.loads(json.dumps(guide))
    copy["meta"]["destination_identity"]["brand_id"] = brand_id
    return copy


class RegistryTests(unittest.TestCase):
    """验收：内置主题注册表至少三套，token 集齐主色/画布/墨色/圆角/字体栈/字距并注明出处。"""

    def test_registry_has_three_brands(self):
        self.assertEqual(KNOWN_BRANDS, ("ferrari", "airbnb", "theverge"))

    def test_each_brand_covers_full_token_set(self):
        for brand, css in BRAND_THEMES.items():
            for token in (
                "--gd-primary:",
                "--gd-bg:",
                "--gd-ink:",
                "--gd-radius-card:",
                "--gd-font:",
                "--gd-caps:",
            ):
                self.assertIn(token, css, (brand, token))
            # 暗色（默认）与亮色两态都在场
            self.assertIn('html[data-brand-id="%s"]' % brand, css)
            self.assertIn('html[data-brand-id="%s"][data-theme="light"]' % brand, css)
            # token 出处注释（awesome-design-md 品牌 DESIGN.md），库外不发明
            self.assertIn(".md", css, brand)

    def test_brand_tokens_match_design_library(self):
        # 与 prototype/ 三版原型同源（均取自 awesome-design-md 品牌 token 表）
        self.assertIn("--gd-primary:#da291c", BRAND_THEMES["ferrari"])   # ferrari.md Rosso Corsa
        self.assertIn("--gd-bg:#181818", BRAND_THEMES["ferrari"])
        self.assertIn("--gd-primary:#ff385c", BRAND_THEMES["airbnb"])   # airbnb.md Rausch
        self.assertIn("--gd-radius-card:14px", BRAND_THEMES["airbnb"])
        self.assertIn("--gd-primary:#3cffd0", BRAND_THEMES["theverge"])  # theverge.md Jelly Mint
        self.assertIn("--gd-radius-card:20px", BRAND_THEMES["theverge"])


class HappyBrandThemeTests(unittest.TestCase):
    """验收：ferrari 夹具渲染 → data-brand-id 静态挂载 + CSS 变量值来自字段映射。"""

    @classmethod
    def setUpClass(cls):
        cls.html = render(HAPPY)

    def test_brand_id_on_html_tag(self):
        self.assertIn('<html lang="zh-CN" data-theme="dark" data-brand-id="ferrari">', self.html)

    def test_css_variable_mapped_from_brand_id(self):
        self.assertIn('html[data-brand-id="ferrari"]', self.html)
        self.assertIn("--gd-primary:#da291c", self.html)
        self.assertIn("--gd-radius-card:0px", self.html)  # ferrari 尖角
        self.assertIn("FerrariSans", self.html)

    def test_no_fallback_notice_for_known_brand(self):
        self.assertNotIn('<div class="brand-fallback"', self.html)


class MinimalFallbackTests(unittest.TestCase):
    """验收：无 destination_identity → 静默回落默认主题，产物不失败。"""

    @classmethod
    def setUpClass(cls):
        cls.html = render(MINIMAL)

    def test_renders_without_error_and_without_brand_attr(self):
        self.assertIn('<html lang="zh-CN" data-theme="dark">', self.html)
        self.assertNotIn('data-brand-id="', self.html)

    def test_no_brand_css_injected(self):
        self.assertNotIn('html[data-brand-id="', self.html)
        self.assertIn('html[data-theme="dark"]', self.html)  # 默认主题 token 在场

    def test_no_fallback_notice(self):
        self.assertNotIn('<div class="brand-fallback"', self.html)


class BrandSwapTests(unittest.TestCase):
    """验收：同一 schema 换 brand_id → 主色变量值不同（明显不同视觉）。"""

    def test_primary_variable_differs_by_brand(self):
        ferrari = render(with_brand(HAPPY, "ferrari"))
        airbnb = render(with_brand(HAPPY, "airbnb"))
        theverge = render(with_brand(HAPPY, "theverge"))
        self.assertIn("--gd-primary:#da291c", ferrari)
        self.assertIn("--gd-primary:#ff385c", airbnb)
        self.assertIn("--gd-primary:#3cffd0", theverge)
        self.assertIn("--gd-radius-card:14px", airbnb)   # airbnb 圆角 vs
        self.assertIn("--gd-radius-card:0px", ferrari)   # ferrari 尖角
        self.assertIn('data-brand-id="airbnb"', airbnb)


class UnknownBrandTests(unittest.TestCase):
    """验收：库外 brand_id → 回落默认主题 + 页面显式标注（spec #16 不合格判定）。"""

    @classmethod
    def setUpClass(cls):
        cls.html = render(with_brand(HAPPY, "unknown-brand"))

    def test_falls_back_to_default_theme(self):
        # 库外 id 只挂标注属性，无任何品牌 CSS 块命中 → 默认 token 生效
        self.assertIn('data-brand-id="unknown-brand"', self.html)
        self.assertNotIn('html[data-brand-id="unknown-brand"]', self.html)

    def test_explicit_fallback_notice(self):
        self.assertIn("brand-fallback", self.html)
        self.assertIn("库外设计「unknown-brand」", self.html)
        self.assertIn("已回落默认主题", self.html)


if __name__ == "__main__":
    unittest.main()
