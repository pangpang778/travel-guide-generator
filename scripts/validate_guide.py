#!/usr/bin/env python3
"""Validate structured travel guides and detect itinerary conflicts.

校验报告契约（T4 硬规则层；消费方：render_guide / build_guide / 宿主 AI / T5 渲染水印）
--------------------------------------------------------------------------------
- ``valid``: 结构校验是否通过（errors 为空），与告警分级独立；build/render 以此为硬门。
- ``errors`` / ``warnings`` / ``conflicts``: v1 原有三桶，保留向后兼容。
  ``conflicts`` = 硬规则违反（blocking），``warnings`` = 建议类（advisory）。
- ``blocking_count``: 硬规则 blocking 告警数（= len(conflicts)）。
- ``advisory_count``: advisory 告警数（= len(warnings)）。
- ``alerts``: ``[{source: "RULE", level: "blocking"|"advisory", type, title, detail}]``。
- ``watermark``: ``blocking_count > 0``。渲染端（T5）据此在产物上显示「未校验通过」水印；
  水印只看 blocking，结构错误由 valid 门拦截，不计入水印。

四类硬规则（违反 → blocking）：
1. 营业时间冲突 ``OUTSIDE_OPENING_HOURS`` / ``CLOSED_DAY``（items[].opening_hours 缺失则跳过）。
2. 衔接时间不足 ``TRANSIT_TOO_SHORT``：已声明 route_from_previous.duration_min 沿用 v1 规则
   （无缓冲）；未声明但相邻 items 带 coords 时用 route_estimator 离线估算（不依赖 12306），
   需 gap >= 估算交通 + 缓冲（默认 30 分钟；末位日视为返程日，缓冲 180 分钟）。
3. 跨城不合理 ``CROSS_CITY_NO_TRANSIT``：同日相邻 items 直线距离 > 100km 且 guide.intercity
   无对应跨城交通段。
4. 预算超支 ``BUDGET_OVERRUN``：budget.profiles[selected] 合计 > preferences.budget_limit
   （未配置 budget_limit 则不检查）。

advisory 示例：``PACE_LONG_DAY`` 单日行程跨度 > 12 小时的节奏提示。
"""

import argparse
import json
from datetime import date

try:
    from .guide_utils import checked_age_days, load_json, parse_hhmm, parse_iso_date
    from .route_estimator import estimate_route, haversine_km
except ImportError:
    from guide_utils import checked_age_days, load_json, parse_hhmm, parse_iso_date
    from route_estimator import estimate_route, haversine_km


REQUIRED_META_FIELDS = ("title", "destination", "language", "start_date", "days")
VALID_PACES = {"relaxed", "balanced", "intensive"}
VALID_SOURCE_TYPES = {"official", "search", "user", "estimate", "general"}

# 硬规则常量
INTERCITY_KM = 100.0        # 同日相邻行程点超过该直线距离视为跨城移动
GAP_BUFFER_MIN = 30         # 衔接缓冲：估算交通时间之外的富余
RETURN_DAY_BUFFER_MIN = 180  # 返程日（末位日）缓冲，预留回程余量
LONG_DAY_MIN = 720          # 单日行程跨度超过 12 小时给节奏提示

# route_estimator 不认识的 mode 的近似映射；仍未知的回退 drive
TRANSIT_MODE_ALIASES = {
    "train": "transit",
    "highspeed": "transit",
    "bus": "transit",
    "car": "drive",
    "taxi": "drive",
    "flight": "drive",
}

# 告警 type 分类（alerts[].type）
RULE_ALERT_TYPES = {
    "OUTSIDE_OPENING_HOURS": "营业时间",
    "CLOSED_DAY": "营业时间",
    "TRANSIT_TOO_SHORT": "衔接时间",
    "TIME_OVERLAP": "衔接时间",
    "CROSS_CITY_NO_TRANSIT": "跨城不合理",
    "BUDGET_OVERRUN": "预算超支",
    "PACE_LONG_DAY": "节奏提示",
}


def issue(level, code, message, path):
    """Create a consistent validation issue."""
    return {"level": level, "code": code, "message": message, "path": path}


def item_coords(item):
    """Return [lng, lat] for an item, or None when unavailable."""
    coords = item.get("coords")
    if (
        isinstance(coords, (list, tuple))
        and len(coords) == 2
        and all(isinstance(value, (int, float)) for value in coords)
    ):
        return list(coords)
    return None


def estimate_transit(origin, destination, item, guide):
    """Offline transit estimate between two items; None when not estimable."""
    preferred = guide.get("preferences", {}).get("transport") or []
    mode = item.get("transport_mode") or (preferred[0] if preferred else "drive")
    mode = TRANSIT_MODE_ALIASES.get(mode, mode)
    if mode not in {"walk", "bike", "transit", "drive"}:
        mode = "drive"
    try:
        return estimate_route(origin, destination, mode)
    except (TypeError, ValueError):
        return None


def validate_budget(guide, conflicts):
    """Rule 4: budget total vs preferences.budget_limit (skip when unset)."""
    limit = guide.get("preferences", {}).get("budget_limit")
    if not isinstance(limit, (int, float)):
        return
    budget = guide.get("budget") or {}
    profile = (budget.get("profiles") or {}).get(budget.get("selected")) or {}
    total = profile.get("total")
    if not isinstance(total, (int, float)):
        categories = profile.get("categories")
        if isinstance(categories, dict):
            amounts = [v for v in categories.values() if isinstance(v, (int, float))]
            total = sum(amounts) if amounts else None
    if not isinstance(total, (int, float)) or total <= limit:
        return
    conflicts.append(
        issue(
            "conflict",
            "BUDGET_OVERRUN",
            "预算合计 {} 超出上限 {}".format(total, limit),
            "budget",
        )
    )


def build_alerts(conflicts, warnings):
    """Map v1 buckets into the alerts[] contract (source RULE + level)."""
    alerts = []
    for conflict in conflicts:
        alerts.append(
            {
                "source": "RULE",
                "level": "blocking",
                "type": RULE_ALERT_TYPES.get(conflict["code"], conflict["code"]),
                "title": conflict["message"],
                "detail": "触发位置 {}".format(conflict["path"]),
            }
        )
    for warning in warnings:
        alerts.append(
            {
                "source": "RULE",
                "level": "advisory",
                "type": RULE_ALERT_TYPES.get(warning["code"], warning["code"]),
                "title": warning["message"],
                "detail": "触发位置 {}".format(warning["path"]),
            }
        )
    return alerts


def validate_sources(guide, errors, warnings, today):
    source_ids = set()
    for index, source in enumerate(guide.get("sources", [])):
        path = "sources[{}]".format(index)
        source_id = source.get("id")
        if not source_id:
            errors.append(issue("error", "SOURCE_ID", "来源缺少 id", path))
        elif source_id in source_ids:
            errors.append(issue("error", "SOURCE_DUPLICATE", "来源 id 重复", path))
        else:
            source_ids.add(source_id)
        source_type = source.get("type", "search")
        if source_type not in VALID_SOURCE_TYPES:
            warnings.append(issue("warning", "SOURCE_TYPE", "未知来源类型", path))
        url = source.get("url")
        if url and not str(url).startswith(("https://", "http://")):
            errors.append(issue("error", "SOURCE_URL", "来源 URL 必须使用 HTTP(S)", path))
        age = checked_age_days(source.get("checked_at"), today)
        if source.get("checked_at") and age is None:
            errors.append(issue("error", "SOURCE_DATE", "核实日期格式应为 YYYY-MM-DD", path))
        elif age is not None and age > 180:
            warnings.append(
                issue("warning", "SOURCE_STALE", "来源已超过 180 天，建议重新核实", path)
            )
    return source_ids


def validate_source_refs(record, path, source_ids, warnings):
    refs = record.get("source_ids", [])
    for source_id in refs:
        if source_id not in source_ids:
            warnings.append(
                issue(
                    "warning",
                    "SOURCE_MISSING",
                    "引用了不存在的来源 {}".format(source_id),
                    path,
                )
            )


def validate_days(guide, source_ids, errors, warnings, conflicts):
    days = guide.get("days", [])
    expected_days = guide.get("meta", {}).get("days")
    if isinstance(expected_days, int) and expected_days != len(days):
        errors.append(
            issue(
                "error",
                "DAY_COUNT",
                "meta.days 与实际 days 数量不一致",
                "days",
            )
        )
    has_intercity = bool(guide.get("intercity"))
    for day_index, day in enumerate(days):
        day_path = "days[{}]".format(day_index)
        # 末位日视为返程日：衔接缓冲放宽到 3 小时（预留回程余量）
        is_return_day = day_index == len(days) - 1
        try:
            day_date = parse_iso_date(day.get("date"))
        except (TypeError, ValueError):
            errors.append(issue("error", "DAY_DATE", "日期格式应为 YYYY-MM-DD", day_path))
            day_date = None
        items = day.get("items", [])
        if not items:
            errors.append(issue("error", "DAY_EMPTY", "每天至少需要一个行程项", day_path))
            continue
        previous_end = None
        previous_name = None
        previous_coords = None
        day_first_start = None
        for item_index, item in enumerate(items):
            path = "{}.items[{}]".format(day_path, item_index)
            if not item.get("name"):
                errors.append(issue("error", "ITEM_NAME", "行程项缺少名称", path))
            validate_source_refs(item, path, source_ids, warnings)
            try:
                start = parse_hhmm(item.get("start"))
                end = parse_hhmm(item.get("end"))
            except (TypeError, ValueError, AttributeError):
                errors.append(issue("error", "ITEM_TIME", "时间格式应为 HH:MM", path))
                continue
            if end <= start:
                errors.append(issue("error", "ITEM_RANGE", "结束时间必须晚于开始时间", path))
            if previous_end is not None and start < previous_end:
                conflicts.append(
                    issue(
                        "conflict",
                        "TIME_OVERLAP",
                        "{} 与 {} 时间重叠".format(previous_name, item.get("name", "未命名项")),
                        path,
                    )
                )
            coords = item_coords(item)
            route = item.get("route_from_previous") or {}
            if previous_end is not None and route.get("duration_min") is not None:
                available = start - previous_end
                if int(route["duration_min"]) > available:
                    conflicts.append(
                        issue(
                            "conflict",
                            "TRANSIT_TOO_SHORT",
                            "预留 {} 分钟，但交通需要约 {} 分钟".format(
                                available, route["duration_min"]
                            ),
                            path,
                        )
                    )
                if route.get("estimated") and not route.get("method"):
                    warnings.append(
                        issue("warning", "ESTIMATE_METHOD", "估算路线缺少估算方法", path)
                    )
            elif previous_end is not None and coords and previous_coords:
                # 未声明交通用时：离线估算（不依赖 12306），gap 须 >= 估算 + 缓冲
                estimate = estimate_transit(previous_coords, coords, item, guide)
                if estimate:
                    buffer_min = (
                        RETURN_DAY_BUFFER_MIN if is_return_day else GAP_BUFFER_MIN
                    )
                    required = int(estimate["duration_min"]) + buffer_min
                    available = start - previous_end
                    if available < required:
                        conflicts.append(
                            issue(
                                "conflict",
                                "TRANSIT_TOO_SHORT",
                                "仅留 {} 分钟，估算交通约 {} 分钟 + 缓冲 {} 分钟".format(
                                    available, estimate["duration_min"], buffer_min
                                ),
                                path,
                            )
                        )
                # 规则 3：同日跨城移动而无对应跨城交通段
                if haversine_km(previous_coords, coords) > INTERCITY_KM and not has_intercity:
                    conflicts.append(
                        issue(
                            "conflict",
                            "CROSS_CITY_NO_TRANSIT",
                            "{} 与 {} 相距约 {:.0f} 公里，当日无对应跨城交通段".format(
                                previous_name,
                                item.get("name", "未命名项"),
                                haversine_km(previous_coords, coords),
                            ),
                            path,
                        )
                    )
            opening = item.get("opening_hours") or {}
            if opening:
                try:
                    opening_min = parse_hhmm(opening.get("open"))
                    closing_min = parse_hhmm(opening.get("close"))
                    if start < opening_min or end > closing_min:
                        conflicts.append(
                            issue(
                                "conflict",
                                "OUTSIDE_OPENING_HOURS",
                                "行程超出开放时间 {}-{}".format(
                                    opening.get("open"), opening.get("close")
                                ),
                                path,
                            )
                        )
                except (TypeError, ValueError, AttributeError):
                    warnings.append(
                        issue("warning", "OPENING_HOURS", "开放时间格式无效", path)
                    )
            if day_date and day_date.strftime("%a").lower() in {
                str(value).lower() for value in item.get("closed_weekdays", [])
            }:
                conflicts.append(
                    issue("conflict", "CLOSED_DAY", "该地点在行程当天可能闭馆", path)
                )
            if day_first_start is None:
                day_first_start = start
            previous_end = max(previous_end or 0, end)
            previous_name = item.get("name", "未命名项")
            previous_coords = coords
        # advisory：单日跨度过长，节奏提示
        if (
            day_first_start is not None
            and previous_end is not None
            and previous_end - day_first_start > LONG_DAY_MIN
        ):
            warnings.append(
                issue(
                    "warning",
                    "PACE_LONG_DAY",
                    "全天行程超过 12 小时，节奏偏紧",
                    day_path,
                )
            )


def validate_preferences(guide, errors, warnings):
    preferences = guide.get("preferences", {})
    pace = preferences.get("pace")
    if pace and pace not in VALID_PACES:
        warnings.append(issue("warning", "PACE", "未知行程节奏", "preferences.pace"))
    earliest = preferences.get("earliest_start")
    latest = preferences.get("latest_end")
    if earliest and latest:
        try:
            earliest_min = parse_hhmm(earliest)
            latest_min = parse_hhmm(latest)
            for day_index, day in enumerate(guide.get("days", [])):
                for item_index, item in enumerate(day.get("items", [])):
                    try:
                        start = parse_hhmm(item.get("start"))
                        end = parse_hhmm(item.get("end"))
                    except (TypeError, ValueError, AttributeError):
                        continue
                    path = "days[{}].items[{}]".format(day_index, item_index)
                    if start < earliest_min:
                        warnings.append(
                            issue("warning", "TOO_EARLY", "早于用户偏好的出发时间", path)
                        )
                    if end > latest_min:
                        warnings.append(
                            issue("warning", "TOO_LATE", "晚于用户偏好的结束时间", path)
                        )
        except (TypeError, ValueError, AttributeError):
            errors.append(
                issue("error", "PREFERENCE_TIME", "偏好时间格式应为 HH:MM", "preferences")
            )


def validate_guide(guide, today=None):
    """Validate guide structure and return errors, warnings and conflicts."""
    errors = []
    warnings = []
    conflicts = []
    if not isinstance(guide, dict):
        return {
            "valid": False,
            "errors": [issue("error", "ROOT", "攻略根节点必须是对象", "$")],
            "warnings": [],
            "conflicts": [],
            "blocking_count": 0,
            "advisory_count": 0,
            "alerts": [],
            "watermark": False,
        }
    if guide.get("schema_version") != "1.0":
        errors.append(
            issue("error", "SCHEMA_VERSION", "schema_version 必须为 1.0", "schema_version")
        )
    meta = guide.get("meta")
    if not isinstance(meta, dict):
        errors.append(issue("error", "META", "缺少 meta 对象", "meta"))
        meta = {}
    for field in REQUIRED_META_FIELDS:
        if meta.get(field) in (None, ""):
            errors.append(
                issue("error", "META_FIELD", "缺少字段 {}".format(field), "meta.{}".format(field))
            )
    try:
        parse_iso_date(meta.get("start_date"))
    except (TypeError, ValueError):
        errors.append(
            issue("error", "START_DATE", "开始日期格式应为 YYYY-MM-DD", "meta.start_date")
        )
    if not isinstance(guide.get("days"), list):
        errors.append(issue("error", "DAYS", "days 必须是数组", "days"))
        guide = dict(guide)
        guide["days"] = []
    source_ids = validate_sources(guide, errors, warnings, today or date.today())
    validate_preferences(guide, errors, warnings)
    validate_days(guide, source_ids, errors, warnings, conflicts)
    validate_budget(guide, conflicts)
    for collection in ("transport", "hotels", "foods", "avoid"):
        for index, record in enumerate(guide.get(collection, [])):
            validate_source_refs(record, "{}[{}]".format(collection, index), source_ids, warnings)
    alerts = build_alerts(conflicts, warnings)
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "conflicts": conflicts,
        "blocking_count": len(conflicts),
        "advisory_count": len(warnings),
        "alerts": alerts,
        "watermark": bool(conflicts),
    }


def main():
    parser = argparse.ArgumentParser(description="校验结构化旅游攻略")
    parser.add_argument("input", help="攻略 JSON 文件")
    parser.add_argument("--strict", action="store_true", help="冲突或警告也返回非零状态")
    args = parser.parse_args()
    try:
        report = validate_guide(load_json(args.input))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        report = {
            "valid": False,
            "errors": [issue("error", "INPUT", str(error), "$")],
            "warnings": [],
            "conflicts": [],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed = not report["valid"] or (
        args.strict and (report["warnings"] or report["conflicts"])
    )
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
