# 攻略 Schema v2 — 新增字段定义（additive）

> T1（#3）prefactor：全部新字段为**增量**定义。v1 既有字段不动，旧 schema 无新字段时渲染/校验照常工作
> （回归基线：`tests/fixtures/guide_minimal.json`）。
> 渲染端契约与品牌驱动规范见 spec #16；术语见 CONTEXT.md。

## 新增字段总表

| 字段 | 类型 | 说明 |
|------|------|------|
| `meta.destination_identity` | `object?` | 目的地文化属性（品牌驱动主题）。`{brand_id, theme, reason}`。宿主 AI 从 awesome-design-md 73 个品牌设计系统中**严格选择**，库外不发明（spec #16） |
| `spots[]` | `object[]?` | 地图行程点（按天坐标序列）。每项：`name, day (1-based), ll [lng, lat]（GCJ-02 真实经纬度，禁止 SVG 伪映射）, tw "HH:MM-HH:MM"（时间窗）, x/y（SVG 降级示意图坐标，可选）, image?` |
| `spots[].image` | `object?` | 素材图片：`{url, source, credibility, alt_description, jev_score (0–10)}`。仅 jev 阈值达标图片写入；无图降级文化占位色块 |
| `routes[]` | `object[]?` | 按天动线：`{day, pts: [[lng,lat], ...]}`，pts 为当天 spots 按顺序的 `ll` 序列（GCJ-02） |
| `intercity` | `object?` | 跨城交通段：`{from: {name, time, ll}, to: {name, time, ll}, line, duration, price, train_no}` |
| `weather[]` | `object[]`（扩展） | v1 已有；v2 允许 `icon/temp/advice` 展示字段 |
| `alerts[]` | `object[]?` | 校验与筛选告警：`{source: "JEV"\|"RULE", type, title, detail}`。与 blocking/advisory 分级配套（v1 校验报告对应 `blocking_count` 等） |
| `blocking_count` / `hard_rules_passed` / `advisory_count` | `int?` | 校验汇总计数，渲染页脚状态行 |
| `pipeline` | `object?` | 票线元数据（页脚披露）：`{collect: {ok, notes, images, tool}, jev: {ok, calls, limit, threshold, adopted, degraded}}` |
| `budget.profiles.*.categories` | `object`（扩展） | v1 为字符串描述；v2 允许 `{amount: number, note}` 分项数值 |
| `confidence` | `object?` | 字段级数据来源标记：`realtime`（实时）/ `verified`（校验通过）/ `estimated`（估算）。键为字段路径（如 `"transport"` → `"realtime"`） |

## 渲染端主题契约（T13，#15）

- 渲染端内置主题注册表：`ferrari` / `airbnb` / `theverge` 三套 CSS token 集（主色/画布/墨色/圆角/字体栈/字距），token 取值严格来自 awesome-design-md 对应品牌 DESIGN.md（`ferrari.md` / `airbnb.md` / `theverge.md`），库外不发明。
- `meta.destination_identity.brand_id` 命中注册表 → 页面 `<html>` 静态挂 `data-brand-id` 并注入该品牌 token；未命中（库外 brand_id）→ 回落默认主题，且页面顶部显式标注「库外设计已回落」（spec #16 不合格判定）；无 `destination_identity` 字段 → 静默回落默认主题。
- 三套品牌主题 + 默认主题均提供暗色（默认）与亮色（`?theme=light` / 切换钮）两态。

## 渲染消费契约（T5，#7）

- `confidence`：渲染为「数据可信度」条，三类徽章 `conf-realtime`（实时）/ `conf-verified`（校验通过）/ `conf-estimated`（估算）三色三形可辨。
- `alerts[]`：渲染为「告警与筛选披露」分区（Tab：告警）。
- `watermark`（validate_guide 报告，`blocking_count > 0`）：页面显示固定「未校验通过」水印。
- `spots[].image`：直出到行程点详情面板，带 untrusted 来源标记 + `window.__imgFail` onerror 降级；无 `image` 字段降级文化占位色块。图片筛选（jev 阈值）在管线侧完成，渲染端只消费已达标图片。

## 兼容规则

- 渲染产物将**完整攻略 schema** 以 JSON 内嵌为 `window.__GUIDE_SCHEMA__`（地图等能力只经该单缝读写，不设第二数据通道）。
- 渲染/校验代码遇到**缺失**新字段必须降级跳过，不得报错（最小降级夹具回归保护）。
- `spots[].ll` 必须是真实 GCJ-02 经纬度；SVG 降级示意图坐标放 `x/y`，禁止伪映射经纬度（上一原型教训）。
- 图片直出前必须过 jev 匹配评分（阈值默认 ≥7），低分降级占位色块并转 JEV 告警。
- `tw` 时间窗与 v1 `items[].start/end` 对齐；渲染端以 schema 为唯一数据缝，不设第二通道。
