#!/usr/bin/env python3
"""Render structured travel-guide JSON with the bundled HTML template."""

import argparse
import json
from pathlib import Path

try:
    from .guide_utils import json_for_script, load_json, source_badges, source_index, text
    from .validate_guide import validate_guide
except ImportError:
    from guide_utils import json_for_script, load_json, source_badges, source_index, text
    from validate_guide import validate_guide


MODE_LABELS = {
    "walk": "步行",
    "bike": "骑行",
    "transit": "公共交通",
    "drive": "驾车",
}

# ===== 品牌主题注册表（T13，#15）=====
# token 取值严格来自 awesome-design-md 对应品牌 DESIGN.md（经 prototype/ 三版原型验收），
# 库外不发明；每条 token 注明出处。三套主题均含暗色（默认态）与亮色扩展。
BRAND_THEMES = {
    "ferrari": """\
        /* ferrari.md colors 表：Rosso Corsa 主色 + 近黑画布（品牌禁纯黑） */
        html[data-brand-id="ferrari"]{
          --gd-primary:#da291c;        /* {colors.primary} Rosso Corsa */
          --gd-primary-active:#b01e0a; /* {colors.primary-active} 按压态 */
          --gd-bg:#181818;             /* {colors.canvas} 近黑页面底色 */
          --gd-card:#303030;           /* {colors.canvas-elevated} 暗画布卡片 */
          --gd-nav:rgba(24,24,24,.96); /* canvas 加深推导的导航底，不引入新色 */
          --gd-ink:#ffffff;            /* {colors.ink} 展示型文字 */
          --gd-text:#969696;           /* {colors.body} 暗画布行文 */
          --gd-muted:#666666;          /* {colors.muted} 次级文本 */
          --gd-line:#303030;           /* {colors.hairline} 暗画布 1px 分隔线 */
          --gd-soft:#242424;           /* hairline/canvas 之间的芯片底，非新色相 */
          --gd-hero:#181818;           /* {colors.canvas} */
          --gd-shadow:none;            /* {Elevation} 唯一 soft drop 仅 hover */
        }
        /* ferrari.md：白色编辑带亮色扩展（canvas-light / hairline-on-light） */
        html[data-brand-id="ferrari"][data-theme="light"]{
          --gd-bg:#ffffff; --gd-card:#f7f7f7; --gd-nav:rgba(255,255,255,.96);
          --gd-ink:#181818; /* {colors.on-light} */
          --gd-text:#666666; /* {colors.muted} */
          --gd-muted:#8f8f8f; /* {colors.muted-soft} */
          --gd-line:#d2d2d2; /* {colors.hairline-on-light} */
          --gd-soft:#ebebeb; /* {colors.surface-strong-light} */
        }
        /* ferrari.md rounded 表：CTA/卡片一律 {rounded.none}=0px 尖角 */
        html[data-brand-id="ferrari"]{ --gd-radius-card:0px; --gd-radius-item:0px; }
        /* ferrari.md typography.fontFamily：授权字体，Inter 替代；CTA 大写 + 1.4px 字距 */
        html[data-brand-id="ferrari"]{
          --gd-font:"FerrariSans","Inter",-apple-system,system-ui,"PingFang SC","Microsoft YaHei",sans-serif;
          --gd-font-display:var(--gd-font);
          --gd-caps:1.4px;
        }
    """,
    "airbnb": """\
        /* airbnb.md colors 表：Rausch 主色，亮色画布为品牌默认 */
        html[data-brand-id="airbnb"][data-theme="light"]{
          --gd-primary:#ff385c;        /* {colors.primary} Rausch */
          --gd-primary-active:#e00b41; /* {colors.primary-active} */
          --gd-bg:#ffffff;             /* {colors.canvas} */
          --gd-card:#ffffff;           /* {colors.canvas} */
          --gd-nav:rgba(255,255,255,.96); /* canvas 半透推导 */
          --gd-ink:#222222;            /* {colors.ink} 主文本，非纯黑 */
          --gd-text:#3f3f3f;           /* {colors.body} */
          --gd-muted:#6a6a6a;          /* {colors.muted} */
          --gd-line:#dddddd;           /* {colors.hairline} */
          --gd-soft:#f7f7f7;           /* {colors.surface-soft} */
          --gd-hero:#222222;           /* {colors.ink} hero 反白带 */
          --gd-shadow:rgba(0,0,0,.02) 0 0 0 1px, rgba(0,0,0,.04) 0 2px 6px 0, rgba(0,0,0,.1) 0 4px 8px 0; /* {Elevation} 全系统唯一阴影层 */
        }
        /* airbnb.md dark canvas/surface 表（原型验收暗色扩展） */
        html[data-brand-id="airbnb"][data-theme="dark"]{
          --gd-primary:#ff385c; --gd-primary-active:#e00b41;
          --gd-bg:#141414;             /* {colors.canvas} dark */
          --gd-card:#1f1f1f;           /* {colors.surface-soft} dark */
          --gd-nav:rgba(20,20,20,.96);
          --gd-ink:#f7f7f7; --gd-text:#d8d8d8; --gd-muted:#a8a8a8;
          --gd-line:#333333;           /* {colors.hairline} dark */
          --gd-soft:#282828;           /* {colors.surface-strong} dark */
          --gd-hero:#141414; --gd-shadow:none;
        }
        /* airbnb.md rounded 表：卡片 {rounded.md}=14px、条目 {rounded.sm}=8px */
        html[data-brand-id="airbnb"]{ --gd-radius-card:14px; --gd-radius-item:8px; }
        /* airbnb.md typography.fontFamily：Cereal（Circular 替代） */
        html[data-brand-id="airbnb"]{
          --gd-font:"Airbnb Cereal VF","Circular",-apple-system,system-ui,Roboto,"Helvetica Neue","PingFang SC","Microsoft YaHei",sans-serif;
          --gd-font-display:var(--gd-font);
          --gd-caps:1.1px;
        }
    """,
    "theverge": """\
        /* theverge.md §2 Color Palette：Jelly Mint CTA + Ultraviolet 撞色，暗底为默认 */
        html[data-brand-id="theverge"][data-theme="dark"]{
          --gd-primary:#3cffd0;        /* {Primary: Jelly Mint} CTA 填充/高注意力色 */
          --gd-primary-active:#309875; /* {Console Mint Border} 深变体按压态 */
          --gd-bg:#131313;             /* {Canvas Black} 非纯黑 */
          --gd-card:#2d2d2d;           /* {Surface Slate} */
          --gd-nav:rgba(19,19,19,.96);
          --gd-ink:#ffffff;            /* {Hazard White} 大字文本 */
          --gd-text:#e9e9e9;           /* {Muted Text} 暗底行文 */
          --gd-muted:#949494;          /* {Secondary Text} */
          --gd-line:#313131;           /* {Image Frame} 1px 包边 */
          --gd-soft:#2d2d2d;           /* {Surface Slate} */
          --gd-hero:#131313;           /* {Canvas Black} */
          --gd-shadow:none;            /* {§6} 唯一"氛围"为 1px ring，无投影 */
        }
        /* theverge.md 亮色扩展（黑白反转 + surface 浅灰） */
        html[data-brand-id="theverge"][data-theme="light"]{
          --gd-bg:#ffffff; --gd-card:#e9e9e9; --gd-nav:rgba(255,255,255,.96);
          --gd-ink:#131313; /* {Canvas Black} 反转为主文本 */
          --gd-text:#131313; --gd-muted:#8c8c8c; /* {Dim Gray} */
          --gd-line:#131313; /* {Image Frame} 亮色反转 */
          --gd-soft:#e9e9e9; --gd-hero:#131313; --gd-shadow:none;
          --gd-primary:#309875; /* 亮底用 Mint 深变体保对比（品牌自有 token） */
        }
        /* theverge.md §5 Border Radius Scale：标准卡片 20px 药丸 / 嵌套 4px */
        html[data-brand-id="theverge"]{ --gd-radius-card:20px; --gd-radius-item:4px; }
        /* theverge.md §3 Font Family：Manuka 展示 + PolySans UI（系统替代） */
        html[data-brand-id="theverge"]{
          --gd-font:"PolySans","Helvetica Neue",Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif;
          --gd-font-display:"Manuka",Impact,"Arial Narrow Bold","PingFang SC","Microsoft YaHei",sans-serif;
          --gd-caps:1.1px;
        }
    """,
}

KNOWN_BRANDS = tuple(BRAND_THEMES)


def render_theme_css(meta):
    """Inject the registry CSS for the selected brand (empty for default/unknown)."""
    identity = meta.get("destination_identity") or {}
    brand = identity.get("brand_id")
    return BRAND_THEMES.get(brand, "")


def render_brand_attr(meta):
    identity = meta.get("destination_identity") or {}
    brand = identity.get("brand_id")
    return ' data-brand-id="{}"'.format(text(brand)) if brand else ""


def render_brand_notice(meta):
    """Spec #16：库外 brand_id 属不合格设计，页面显式标注回落，不静默。"""
    identity = meta.get("destination_identity") or {}
    brand = identity.get("brand_id")
    if not brand or brand in BRAND_THEMES:
        return ""
    return (
        '<div class="brand-fallback" role="status">⚠ 库外设计「{}」不在 awesome-design-md '
        "设计库（73 个品牌设计系统），已回落默认主题</div>".format(text(brand))
    )


def render_tags(guide):
    meta = guide.get("meta", {})
    preferences = guide.get("preferences", {})
    tags = [
        "{}天".format(meta.get("days", len(guide.get("days", [])))),
        "{}人".format(meta.get("travelers", 1)),
        preferences.get("group_type", "自由行"),
        preferences.get("pace", "balanced"),
    ]
    return "".join('<span class="tag">{}</span>'.format(text(tag)) for tag in tags if tag)


def render_language_attributes(meta):
    language = str(meta.get("language", "zh-CN"))
    root_language = language.lower().split("-", 1)[0]
    direction = ' dir="rtl"' if root_language in {"ar", "fa", "he", "ur"} else ""
    return 'lang="{}"{}'.format(text(language), direction)


def render_transport(guide, sources):
    records = guide.get("transport", [])
    if not records:
        return ""
    cards = []
    for record in records:
        cards.append(
            '<div class="t-item{}">{}<div class="t-title">{}</div>'
            '<div class="t-detail">{}</div><div class="t-price">{}</div>{}</div>'.format(
                " recommend" if record.get("recommended") else "",
                '<span class="badge">推荐</span>' if record.get("recommended") else "",
                text(record.get("title", record.get("mode", "交通"))),
                text(record.get("detail", "")),
                text(record.get("price", "")),
                source_badges(record.get("source_ids"), sources),
            )
        )
    return '<section class="transport-card"><h2>🚄 到达与当地交通</h2><div class="transport-grid">{}</div></section>'.format("".join(cards))


def render_hotels(guide, sources):
    records = guide.get("hotels", [])
    if not records:
        return ""
    cards = []
    for record in records:
        cards.append(
            '<div class="hotel-item"><div class="h-name">{}</div>'
            '<div class="h-price">{}</div><div class="h-desc">{}</div>'
            '<div class="h-rec">{}</div>{}</div>'.format(
                text(record.get("area", record.get("name", "住宿区域"))),
                text(record.get("price", "")),
                text(record.get("reason", "")),
                text(record.get("connection", "")),
                source_badges(record.get("source_ids"), sources),
            )
        )
    return '<section class="hotel-section"><h2>🏨 住宿推荐</h2><div class="hotel-grid">{}</div></section>'.format("".join(cards))


def render_route(route):
    if not route:
        return ""
    mode = MODE_LABELS.get(route.get("mode"), route.get("mode", "交通"))
    estimate = '<span class="estimate-tag">估算</span>' if route.get("estimated") else '<span class="amap-tag">高德实测</span>'
    return "{} {:.1f}km / {}分钟 {}".format(
        text(mode),
        float(route.get("distance_km", 0)),
        int(route.get("duration_min", 0)),
        estimate,
    )


def render_item_media(spot):
    """行程点图片直出（T5）：schema spots[].image 已过 jev 阈值，渲染端带 untrusted 标记；
    onerror 走全局 window.__imgFail 占位色块；无 image 降级文化占位。"""
    image = (spot or {}).get("image") or {}
    if image.get("url"):
        score = image.get("jev_score")
        badge = '<span class="jev-mark">jev {}/10</span>'.format(text(score)) if score is not None else ""
        return (
            '<div class="item-media"><img loading="lazy" src="{src}" alt="{alt}" onerror="window.__imgFail(this)">'
            '{badge}<div class="src-mark" title="{cred}">⚠ {source} · untrusted</div></div>'
        ).format(
            src=text(image.get("url")),
            alt=text(image.get("alt_description") or ""),
            badge=badge,
            cred=text(image.get("credibility", "")),
            source=text(image.get("source", "素材来源未标注")),
        )
    return '<div class="item-media"><div class="img-ph">🏛️<span>无达标图 · 文化占位</span></div></div>'


def render_item_detail(item, spot):
    """行程点可展开详情面板（T5）：介绍/营业时间/门票/理由，原生 details 保证无 JS/打印可用。"""
    spot = spot or {}
    rows = [
        ("介绍", item.get("description")),
        ("营业时间", spot.get("tw") or item.get("hours")),
        ("门票", item.get("price") if item.get("price") not in (None, "", "待核实") else item.get("ticket")),
        ("为什么推荐", item.get("why")),
    ]
    rows = [(label, value) for label, value in rows if value]
    extras = []
    if item.get("romance"):
        extras.append('<div class="spot-romance">💕 {}</div>'.format(text(item["romance"])))
    if item.get("pitfall"):
        extras.append('<div class="pitfall">⚠️ {}</div>'.format(text(item["pitfall"])))
    if not rows and not extras:
        return ""
    dl = "".join("<dt>{}</dt><dd>{}</dd>".format(text(label), text(value)) for label, value in rows)
    return (
        '<details class="item-detail"><summary>详情</summary><div class="detail-body">'
        "{media}<dl>{dl}</dl>{extras}</div></details>"
    ).format(media=render_item_media(spot), dl=dl, extras="".join(extras))


def render_day_item(item, day_index, item_index, sources, spot=None):
    key = "day-{}-item-{}".format(day_index + 1, item_index + 1)
    route = item.get("route_from_previous")
    source_html = source_badges(item.get("source_ids"), sources)
    coords = item.get("coords") or []
    coords_attr = ",".join(str(value) for value in coords)
    return (
        '<article class="spot itinerary-item" data-item-key="{key}" data-coords="{coords}">'
        '<div class="spot-head"><label class="item-toggle" title="标记完成">'
        '<input type="checkbox" class="item-check" data-key="{key}"></label>'
        '<div class="spot-icon">{icon}</div><div class="spot-main">'
        '<div class="spot-name"><span class="spot-title">{name}<span class="spot-price">{price}</span></span>'
        '<span class="spot-actions"><button type="button" class="favorite-btn" data-key="{key}" title="收藏">♡</button>'
        '<button type="button" class="edit-btn" data-key="{key}" aria-expanded="false">编辑</button></span></div>'
        '<div class="editable-schedule"><span class="schedule-display">{start}–{end}</span>'
        '<span class="item-edit-controls" hidden><input type="time" class="time-input start-time" '
        'data-key="{key}" value="{start}" aria-label="开始时间">–'
        '<input type="time" class="time-input end-time" data-key="{key}" value="{end}" aria-label="结束时间"></span>'
        '<span class="spot-transit">{route}</span></div>'
        '<div class="spot-desc">{description}</div>{sources}'
        '<label class="cost-editor item-edit-controls" hidden>预算 <input type="number" min="0" step="1" class="cost-input" '
        'data-key="{key}" value="{cost}"> {currency}</label>'
        '</div></div>{romance}{pitfall}{detail}</article>'
    ).format(
        key=key,
        coords=text(coords_attr),
        icon=text(item.get("icon", "📍")),
        name=text(item.get("name", "未命名行程")),
        price=text(item.get("price", "待核实")),
        start=text(item.get("start", "09:00")),
        end=text(item.get("end", "10:00")),
        route=render_route(route),
        description=text(item.get("description", "")),
        sources=source_html,
        cost=text(item.get("cost", 0)),
        currency=text(item.get("currency", "元")),
        romance='<div class="spot-romance">💕 {}</div>'.format(text(item["romance"])) if item.get("romance") else "",
        pitfall='<div class="pitfall">⚠️ {}</div>'.format(text(item["pitfall"])) if item.get("pitfall") else "",
        detail=render_item_detail(item, spot),
    )


def render_days(guide, sources):
    spots = {spot.get("name"): spot for spot in guide.get("spots", []) if spot.get("name")}
    cards = []
    for day_index, day in enumerate(guide.get("days", [])):
        items = day.get("items", [])
        route_names = " → ".join(item.get("name", "") for item in items)
        item_html = "".join(
            render_day_item(item, day_index, item_index, sources, spots.get(item.get("name")))
            for item_index, item in enumerate(items)
        )
        cards.append(
            '<section class="day-card" data-day="{day}"><div class="day-header d{color}">'
            '<div class="day-num">Day {day} · {date}</div><div class="day-title">{title}</div>'
            '<div class="day-route">{route}</div></div><div class="route-bar">'
            '<span class="dot"></span>{route}</div>{items}</section>'.format(
                day=text(day.get("day", day_index + 1)),
                date=text(day.get("date", "")),
                color=(day_index % 5) + 1,
                title=text(day.get("title", "")),
                route=text(route_names),
                items=item_html,
            )
        )
    return "".join(cards)


def render_foods(guide, sources):
    records = guide.get("foods", [])
    if not records:
        return ""
    cards = []
    for record in records:
        cards.append(
            '<div class="food-item"><div class="f-name">{}</div><div class="f-shop">{}</div>'
            '<div class="f-price">{}</div><div class="f-note">{}</div>{}</div>'.format(
                text(record.get("name", "")),
                text(record.get("shop", "")),
                text(record.get("price", "")),
                text(record.get("reason", "")),
                source_badges(record.get("source_ids"), sources),
            )
        )
    return '<section class="food-section"><h2>🍜 美食推荐</h2><div class="food-grid">{}</div></section>'.format("".join(cards))


def render_avoid(guide, sources):
    records = guide.get("avoid", [])
    if not records:
        return ""
    items = []
    for index, record in enumerate(records):
        items.append(
            '<div class="avoid-item"><div class="a-num">{}</div><div>'
            '<span class="a-wrong">{}</span> → <span class="a-right">{}</span>{}</div></div>'.format(
                index + 1,
                text(record.get("wrong", "")),
                text(record.get("right", "")),
                source_badges(record.get("source_ids"), sources),
            )
        )
    return '<section class="avoid-section"><h2>⚠️ 避坑清单</h2><div class="avoid-list">{}</div></section>'.format("".join(items))


def render_budget(guide):
    budget = guide.get("budget", {})
    profiles = budget.get("profiles", {})
    selected = budget.get("selected") or guide.get("preferences", {}).get("budget_level", "comfortable")
    profile = profiles.get(selected) or next(iter(profiles.values()), {})
    categories = profile.get("categories", {})
    if not categories:
        return ""
    rows = "".join(
        '<div class="budget-row"><span>{}</span><span>{}</span></div>'.format(text(name), text(value))
        for name, value in categories.items()
    )
    total = profile.get("total", sum(value for value in categories.values() if isinstance(value, (int, float))))
    return (
        '<section class="budget-card"><h2>💰 预算估算 · {}</h2><div>{}'
        '<div class="budget-total">计划预算：<span id="baseBudget">{}</span> '
        '<span class="currency-label">{}</span><br><small>本地编辑合计：'
        '<span id="liveBudget">0</span> <span class="currency-label">{}</span></small></div></div></section>'
    ).format(text(selected), rows, text(total), text(guide.get("meta", {}).get("currency", "CNY")), text(guide.get("meta", {}).get("currency", "CNY")))


def render_tips(guide):
    tips = guide.get("tips", []) + guide.get("season_tips", [])
    if not tips:
        return ""
    return '<section class="tips-card"><h2>🧳 出行 Tips</h2><div>{}</div></section>'.format(
        "".join('<div class="tip-item"><span>✓</span><span>{}</span></div>'.format(text(tip)) for tip in tips)
    )


def render_sources(sources):
    source_rows = "".join(
        '<li><span class="source-type">{}</span>{}</li>'.format(
            text(source.get("type", "search")),
            source_badges([source_id], sources),
        )
        for source_id, source in sources.items()
    )
    return (
        '<section class="sources-section"><h2>🔎 信息来源与核实日期</h2><ul>{}</ul></section>'.format(source_rows)
        if source_rows
        else ""
    )


def render_confidence(guide):
    """字段级数据可信度徽章（T5）：realtime/verified/estimated 三色三形。"""
    confidence = guide.get("confidence") or {}
    if not confidence:
        return ""
    chips = "".join(
        '<span class="conf-chip conf-{level}"><i></i>{field} · {level}</span>'.format(
            level=text(level), field=text(field)
        )
        for field, level in confidence.items()
    )
    return '<section class="confidence-section"><h2>🔬 数据可信度</h2><div class="conf-row">{}</div></section>'.format(chips)


def render_alerts(guide):
    """schema alerts[]（JEV/RULE 告警披露）→「告警」Tab 内容。"""
    alerts = guide.get("alerts", [])
    if not alerts:
        return ""
    rows = "".join(
        '<div class="alert-item"><span class="alert-src alert-{src}">{src}</span>'
        "<div><b>{type} · {title}</b><span>{detail}</span></div></div>".format(
            src=text(alert.get("source", "RULE")).lower(),
            type=text(alert.get("type", "")),
            title=text(alert.get("title", "")),
            detail=text(alert.get("detail", "")),
        )
        for alert in alerts
    )
    return '<section class="alerts-section"><h2>🚨 告警与筛选披露</h2><div class="alert-list">{}</div></section>'.format(rows)


def render_quality(report):
    issues = report.get("errors", []) + report.get("conflicts", []) + report.get("warnings", [])
    if not issues:
        return ""
    issue_rows = "".join(
        '<li class="issue-{}"><b>{}</b> {} <code>{}</code></li>'.format(
            text(item.get("level", "warning")), text(item.get("code", "CHECK")), text(item.get("message", "")), text(item.get("path", ""))
        )
        for item in issues
    )
    return (
        '<section class="quality-section"><h2>🩺 行程质量检查</h2><div class="quality-summary">错误 {} · 冲突 {} · 提醒 {}</div>'
        '<ul class="issue-list">{}</ul></section>'
    ).format(
        len(report.get("errors", [])),
        len(report.get("conflicts", [])),
        len(report.get("warnings", [])),
        issue_rows,
    )


def render_watermark(report):
    """消费 validate_guide 契约（#6）：watermark=True（blocking 未清空）→ 页面显式水印。"""
    if not report.get("watermark"):
        return ""
    return (
        '<div class="unverified-watermark" role="status">⚠ 未校验通过 · 存在未清空的 blocking 告警，'
        "本页数据未经硬规则校验，请谨慎参考</div>"
    )


def render_meta_sections(guide, report, sources):
    preferences = guide.get("preferences", {})
    preference_parts = []
    labels = {
        "budget_level": "预算",
        "pace": "节奏",
        "group_type": "人群",
        "interests": "兴趣",
        "dietary": "饮食",
        "accessibility": "无障碍",
        "transport": "交通偏好",
    }
    for key, label in labels.items():
        value = preferences.get(key)
        if value:
            display = "、".join(str(item) for item in value) if isinstance(value, list) else value
            preference_parts.append('<span class="meta-chip"><b>{}</b> {}</span>'.format(text(label), text(display)))
    weather_rows = []
    for weather in guide.get("weather", []):
        weather_rows.append(
            '<div class="weather-item"><b>{}</b><span>{}</span><span>{}–{}℃</span>{}{}</div>'.format(
                text(weather.get("date", "")),
                text(weather.get("condition", "待核实")),
                text(weather.get("temp_low", "?")),
                text(weather.get("temp_high", "?")),
                source_badges(weather.get("source_ids"), sources),
                '<div class="weather-backup">室内备选：{}</div>'.format(text(weather["backup_plan"])) if weather.get("backup_plan") else "",
            )
        )
    issues = report.get("errors", []) + report.get("conflicts", []) + report.get("warnings", [])
    sections = [
        '<section class="tool-card"><div class="toolbar"><button type="button" id="printGuide">🖨️ 打印/PDF</button>'
        '<button type="button" id="exportState">💾 导出修改</button><button type="button" id="resetState">↺ 清除本地修改</button>'
        '<span id="saveStatus">修改仅保存在本浏览器</span></div></section>',
        '<section class="preferences-section"><h2>🎛️ 本次旅行偏好</h2><div class="meta-chips">{}</div></section>'.format("".join(preference_parts)),
    ]
    if weather_rows:
        sections.append('<section class="weather-section"><h2>🌦️ 天气与季节</h2><div>{}</div></section>'.format("".join(weather_rows)))
    confidence = render_confidence(guide)
    if confidence:
        sections.append(confidence)
    return "".join(sections)


def render_map_section(guide):
    """Render the map card skeleton.

    Map data flows only through the embedded guide schema
    (``window.__GUIDE_SCHEMA__``); this skeleton is pure structure.
    The renderer script fills it with an interactive AMap or a
    first-class static SVG fallback depending on AMAP_KEY presence.
    """
    return (
        '<section class="map-card" id="map-card" aria-label="行程地图">'
        '<h2>🗺️ 行程地图</h2>'
        '<div class="map-area" id="map-area" role="img" aria-label="行程路线图"></div>'
        '<div class="map-ok" id="map-ok" hidden>✅ 交互地图已启用</div>'
        '<div class="key-guide" id="key-guide">'
        '<div class="key-inner">'
        '<h3>启用交互地图</h3>'
        '<p>当前未配置高德地图 Key。填入你的 AMAP_KEY（'
        '<a href="https://lbs.amap.com" target="_blank" rel="noopener noreferrer">lbs.amap.com</a> 申请），'
        "Key 只保存在本地 <code>localStorage('amap_key')</code>，不会上传，"
        "填写后刷新即切换为交互地图。也可先关闭此卡片查看静态路线图。</p>"
        '<div class="key-row">'
        '<input type="text" id="amap-key-input" placeholder="粘贴 AMAP_WEB Key…" autocomplete="off">'
        '<button type="button" class="key-go" id="amap-go">启用</button>'
        "</div>"
        '<button type="button" class="key-close" id="key-guide-close">先看看静态路线图 →</button>'
        "</div></div>"
        '<div class="map-ctrl-row" id="map-ctrl-row" hidden>'
        '<span class="ctrl-label">按天</span>'
        '<span id="day-btns"></span>'
        '<button type="button" class="day-btn" id="replay-btn" title="当天首站 marker 沿动线回放">▶ 回放</button>'
        '<button type="button" class="day-btn" id="label-toggle" title="时间窗标签 开/关">标签 开</button>'
        "</div>"
        '<div class="map-legend" id="map-legend"></div>'
        '<div class="map-status" id="map-status">地图加载中…</div>'
        "</section>"
    )


def render_html(guide, template_text, report=None):
    report = report or validate_guide(guide)
    sources = source_index(guide)
    meta = guide.get("meta", {})
    replacements = {
        "{{LANG_ATTR}}": render_language_attributes(meta),
        "{{BRAND_ATTR}}": render_brand_attr(meta),
        "{{THEME_CSS}}": render_theme_css(meta),
        "{{BRAND_NOTICE}}": render_brand_notice(meta),
        "{{TITLE}}": text(meta.get("title", "旅游攻略")),
        "{{EMOJIS}}": text(meta.get("emojis", "🌍🧭✨")),
        "{{SUBTITLE}}": text(meta.get("subtitle", "为这一次出发认真规划")),
        "{{TAGS}}": render_tags(guide),
        "{{META_SECTIONS}}": render_meta_sections(guide, report, sources),
        "{{ALERTS_SECTION}}": render_alerts(guide),
        "{{QUALITY_SECTION}}": render_quality(report),
        "{{MAP_SECTION}}": render_map_section(guide),
        "{{TRANSPORT_SECTION}}": render_transport(guide, sources),
        "{{HOTEL_SECTION}}": render_hotels(guide, sources),
        "{{DAILY_ITINERARY}}": render_days(guide, sources),
        "{{FOOD_SECTION}}": render_foods(guide, sources),
        "{{AVOID_SECTION}}": render_avoid(guide, sources),
        "{{BUDGET_SECTION}}": render_budget(guide),
        "{{TIPS_SECTION}}": render_tips(guide),
        "{{SOURCES_SECTION}}": render_sources(sources),
        "{{FOOTER_TEXT}}": text(meta.get("footer", "信息会变化，预订与出发前请再次核实。")),
        "{{WATERMARK}}": render_watermark(report),
        "{{GUIDE_DATA}}": json_for_script(guide),
    }
    output = template_text
    for placeholder, value in replacements.items():
        output = output.replace(placeholder, value)
    if "{{" in output:
        raise ValueError("HTML 模板仍有未填充占位符")
    return output


def render_file(guide, output_path, template_path=None, allow_invalid=False):
    report = validate_guide(guide)
    if not report["valid"] and not allow_invalid:
        raise ValueError("攻略校验失败，使用 --allow-invalid 可强制渲染")
    template = Path(template_path or Path(__file__).parents[1] / "assets" / "template.html")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(guide, template.read_text(encoding="utf-8"), report), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="从结构化 JSON 渲染旅游攻略 HTML")
    parser.add_argument("input", help="攻略 JSON 文件")
    parser.add_argument("--output", required=True, help="输出 HTML 文件")
    parser.add_argument("--template", help="自定义 HTML 模板")
    parser.add_argument("--allow-invalid", action="store_true")
    args = parser.parse_args()
    try:
        report = render_file(load_json(args.input), args.output, args.template, args.allow_invalid)
        print(json.dumps({"status": "ok", "output": args.output, "report": report}, ensure_ascii=False, indent=2))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
