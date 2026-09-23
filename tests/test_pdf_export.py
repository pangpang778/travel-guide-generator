"""T10（#12）PDF 打印导出验收：打印 CSS 与无头 Chromium 产物。

- 打印 CSS：分页合理（卡片 break-inside: avoid + @page 边距）、配色降为打印安全色
- 交互元素在打印输出中隐藏（tab 钮/主题钮/工具条/key 引导/回放控件等）
- 暗色模式打印输出仍为浅色可打印版（@media print 覆盖 data-theme 与品牌 token）
- PDF 为可选步骤：默认 build() 产物计数保持 6，pdf=True 才追加第 7 个产物
- 无头浏览器 PDF 全流程：文件存在且以 %PDF- 魔数开头（playwright/chromium 缺失则跳过）
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.build_guide import build, export_pdf
from scripts.guide_utils import load_json

ROOT = Path(__file__).parents[1]
TEMPLATE = (ROOT / "assets" / "template.html").read_text(encoding="utf-8")
SAMPLE = load_json(ROOT / "examples" / "sample-guide.json")


def _chromium_available():
    """playwright CLI + chromium 二进制可用性（CI 无浏览器环境跳过真实 PDF 用例）。"""
    try:
        probe = subprocess.run(
            [shutil.which("npx") or "npx", "--no-install", "playwright", "--version"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if probe.returncode != 0:
        return False
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    return any(cache.is_dir() and any(cache.glob("chromium*")) for cache in candidates)


class PrintCssTests(unittest.TestCase):
    """验收：打印 CSS 分页合理、浅底深字、隐藏交互件、暗色不影响打印。"""

    def test_pagination_keeps_sections_whole(self):
        self.assertIn("break-inside: avoid", TEMPLATE)
        self.assertIn("@page { margin: 14mm; }", TEMPLATE)
        self.assertIn("h2 { break-after: avoid; }", TEMPLATE)
        # 主要内容卡片全部参与防跨页断裂
        for card in (".transport-card", ".day-card", ".budget-card",
                     ".map-card", ".avoid-section", ".alert-item", ".spot"):
            self.assertIn(card, TEMPLATE)

    def test_interactive_elements_hidden_in_print(self):
        self.assertIn("@media print {", TEMPLATE)
        for selector in (".tab-nav", ".theme-toggle", ".quick-nav", ".tool-card",
                         ".item-toggle", ".favorite-btn", ".edit-btn",
                         ".key-guide", ".map-ctrl-row", ".item-detail summary"):
            self.assertIn(selector, TEMPLATE)

    def test_dark_theme_prints_light(self):
        # token 覆盖块同时命中暗色默认态（data-theme="dark"）与品牌主题（data-brand-id）
        self.assertIn('html[data-theme="dark"], html[data-brand-id] {', TEMPLATE)
        self.assertIn("--gd-hero:#fff", TEMPLATE)
        self.assertIn("beforeprint", TEMPLATE)  # JS 侧兜底：打印前强制亮色

    def test_print_safe_colors(self):
        self.assertIn(".budget-card { color: #1d1d1f; }", TEMPLATE)
        self.assertIn(".hero, .hero h1 { color: #1d1d1f; }", TEMPLATE)
        self.assertIn(".alert-jev { background: #fdeceb;", TEMPLATE)
        self.assertIn("text-shadow: none !important", TEMPLATE)


class PdfExportTests(unittest.TestCase):
    """验收：无头浏览器从 HTML 产出 PDF 全流程可复现。"""

    @classmethod
    def setUpClass(cls):
        if not _chromium_available():
            raise unittest.SkipTest("playwright/chromium 不可用，跳过真实 PDF 用例")

    def test_export_pdf_produces_magic_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            html_path = Path(directory) / "guide.html"
            html_path.write_text(
                "<html><body><h1>行程单</h1><p>print-css-probe</p></body></html>",
                encoding="utf-8",
            )
            pdf_path = export_pdf(html_path, Path(directory) / "guide.pdf")
            data = pdf_path.read_bytes()
            self.assertTrue(data.startswith(b"%PDF-"), "PDF 魔数缺失")
            self.assertGreater(len(data), 1000)

    def test_build_pdf_opt_in_adds_seventh_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            result = build(SAMPLE, Path(directory) / "guide", pdf=True)
            self.assertEqual(result["status"], "ok")
            self.assertNotIn("pdf_error", result)
            self.assertEqual(len(result["files"]), 7)  # 6 常规产物 + PDF
            data = (Path(directory) / "guide.pdf").read_bytes()
            self.assertTrue(data.startswith(b"%PDF-"))
            self.assertGreater(len(data), 10_000)

    def test_build_default_keeps_six_artifacts(self):
        # PDF 保持为可选步骤：不传 pdf=True 不产出，既有 6 产物计数不回归
        with tempfile.TemporaryDirectory() as directory:
            result = build(SAMPLE, Path(directory) / "guide")
            self.assertEqual(len(result["files"]), 6)


if __name__ == "__main__":
    unittest.main()
