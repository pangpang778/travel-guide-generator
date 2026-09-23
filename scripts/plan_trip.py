#!/usr/bin/env python3
"""One-command real-source trip research and candidate comparison."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

try:
    from . import jev_client
    from .build_guide import build
    from .compare_trip import render_html, summarize
    from .guide_utils import format_hhmm
    from .research_trip import REFERENCE_PLATFORMS, _clean_command_message, _slug, _time_minutes, collect
except ImportError:
    import jev_client
    from build_guide import build
    from compare_trip import render_html, summarize
    from guide_utils import format_hhmm
    from research_trip import REFERENCE_PLATFORMS, _clean_command_message, _slug, _time_minutes, collect


# jev 行程合理性判断点（advisory-only，见 docs/adr/0001）
JEV_PLAUSIBILITY_INSTRUCTIONS = (
    "对一天行程编排的语义合理性评分（0-10），关注强度、动线、节奏："
    "行程点数量与时长是否在一天可承受范围内、时间窗衔接是否从容、"
    "活动类型分布是否合理（不至于全天赶场或大片空窗）→ 高分；"
    "单日排点过密、衔接过紧或明显空转 → 低分。"
)
JEV_PLAUSIBILITY_CRITERIA = [
    "0-3 单日强度明显超载或动线节奏严重错乱",
    "4-6 排点偏紧、衔接仓促或存在明显空窗/折返",
    "7-8 强度适中、衔接从容，体验上靠谱",
    "9-10 强度、动线、节奏俱佳",
]


def _day_plausibility_state(day) -> str:
    """把当天 items 序列（名称/时间窗/时长）打包成 jev 评分用描述。

    ponytail: 不做跨点距离估算——生成端 items 无坐标，物理可行的动线
    校验已由 validate_guide 的硬规则层（route_estimator）覆盖，这里只
    交给 jev 语义层。
    """
    lines = []
    for index, item in enumerate(day.get("items") or [], start=1):
        start = _time_minutes(item.get("start"))
        end = _time_minutes(item.get("end"))
        duration = (
            "{}分钟".format(end - start)
            if start is not None and end is not None and end > start
            else "时长未知"
        )
        lines.append(
            "{}. {}（{}–{}，{}，类型 {}）".format(
                index,
                item.get("name"),
                item.get("start"),
                item.get("end"),
                duration,
                item.get("type"),
            )
        )
    return "\n".join(lines)


def apply_jev_plausibility_scoring(
    guide: dict,
    client: Any,
    threshold: float | None = None,
) -> "tuple[dict, list[dict]]":
    """jev 行程合理性判断点：每天行程整体评估一次（强度/动线/节奏）。

    与 T11 素材点同构：低分天数产出 JEV advisory 告警并入统一告警列表；
    任何不可用（无 key/超时/失败/超限）退回启发式孪生（宿主 AI 自行
    评估），不抛异常，降级显式写入返回的 summary。client 与素材点共享
    同一个限次预算。
    """
    threshold = client.threshold if threshold is None else threshold
    summary = {
        "ok": True,
        "calls": client.calls_used,
        "limit": client.limit,
        "threshold": threshold,
        "days_evaluated": 0,
        "degraded": False,
    }
    alerts: list[dict] = []

    if not client.api_key:  # 无 key：显式降级，退回启发式孪生
        summary.update(ok=False, degraded=True, reason=jev_client.NO_KEY)
        return summary, alerts

    def _on_degraded(result: dict) -> None:
        summary.update(
            ok=False, degraded=True, reason=result["reason"], calls=client.calls_used
        )

    destination = (guide.get("meta") or {}).get("destination", "")
    for day in guide.get("days") or []:
        items = day.get("items") or []
        result = client.score(
            "行程合理性评分。目的地：{}。Day {}「{}」，共 {} 个行程点：\n{}".format(
                destination,
                day.get("day"),
                day.get("title"),
                len(items),
                _day_plausibility_state(day),
            ),
            instructions=JEV_PLAUSIBILITY_INSTRUCTIONS,
            criteria=JEV_PLAUSIBILITY_CRITERIA,
        )
        if not result["ok"]:
            _on_degraded(result)
            break
        summary["days_evaluated"] += 1
        score = result["score"]
        if score < threshold:
            alerts.append(
                {
                    "source": "JEV",
                    "level": "advisory",
                    "type": "行程合理性",
                    "title": "Day {} 行程合理性评分 {:.1f} 低于阈值 {}，建议复核当日强度/动线/节奏".format(
                        day.get("day"), score, threshold
                    ),
                    "detail": "当日 {} 个行程点：{}。建议检查单日排点是否过密、"
                    "衔接是否仓促、是否存在大片空窗或不合理折返。".format(
                        len(items), _day_plausibility_state(day)
                    ),
                }
            )
    summary["calls"] = client.calls_used
    return summary, alerts


def _first_train(leg):
    if not isinstance(leg, dict):
        return None
    recommended = leg.get("recommended")
    if isinstance(recommended, dict):
        return recommended
    trains = leg.get("trains")
    return trains[0] if isinstance(trains, list) and trains else None


def _station_name(station):
    if not isinstance(station, dict):
        return ""
    return str(station.get("name") or station.get("station_name") or "")


def _train_window(train, fallback_start, fallback_end):
    start = _time_minutes(train.get("start_time")) if train else None
    end = _time_minutes(train.get("arrive_time")) if train else None
    if start is not None and end is not None and end > start:
        return start, end
    return fallback_start, fallback_end


def _train_label(leg, train):
    code = (train or {}).get("code") or (train or {}).get("train_no") or "直达车次"
    origin = _station_name((leg or {}).get("from"))
    destination = _station_name((leg or {}).get("to"))
    return "{} {} → {}".format(code, origin, destination).strip()


def _train_detail(leg, train):
    if not train:
        return _clean_command_message(leg.get("message")) or "当前日期未返回直达车次，不能据此判断有票。"
    parts = []
    if train.get("start_time") and train.get("arrive_time"):
        parts.append("{}—{}".format(train["start_time"], train["arrive_time"]))
    if train.get("duration"):
        parts.append("历时{}".format(train["duration"]))
    if train.get("available") is True:
        parts.append("本次结果标记为可售")
    elif train.get("available") is False:
        parts.append("本次结果标记为不可售")
    else:
        parts.append("余票状态以12306实时结果为准")
    parts.append("出发前重新核验余票、价格和经停站")
    return "；".join(parts)


def _train_price(train):
    value = (train or {}).get("second_seat")
    if value in (None, ""):
        return "价格以12306实时结果为准"
    return "二等座：{}".format(value)


def _ticket_status(train) -> str:
    """Classify ticket availability: ok / tight / soldout / unknown / none."""
    if train is None:
        return "none"
    if train.get("available") is True:
        return "ok"
    if train.get("available") is False:
        return "soldout"
    seats = [
        str(train.get(key) or "")
        for key in ("second_seat", "first_seat", "business_seat", "no_seat")
    ]
    if any("候补" in seat or "无" in seat for seat in seats):
        return "tight"
    for seat in seats:
        try:
            if 0 < int(seat) <= 20:
                return "tight"
        except ValueError:
            continue
    return "unknown"


def _alternate_train(leg, train):
    """First other train in the leg that still has tickets."""
    trains = (leg or {}).get("trains")
    if not isinstance(trains, list):
        return None
    code = (train or {}).get("code")
    train_no = (train or {}).get("train_no")
    for candidate in trains:
        if not isinstance(candidate, dict):
            continue
        if code and candidate.get("code") == code and candidate.get("train_no") == train_no:
            continue
        if candidate.get("available") is not False:
            return candidate
    return None


def _ticket_warning(leg, train) -> str | None:
    """Warning line for a sold-out or tight recommended train, with an alternate."""
    status = _ticket_status(train)
    if status not in ("soldout", "tight"):
        return None
    alternate = _alternate_train(leg, train)
    alt = ""
    if alternate:
        alt = "{} {}".format(
            alternate.get("start_time") or "",
            alternate.get("code") or alternate.get("train_no") or "",
        ).strip()
    if status == "tight":
        return "⚠ 余票紧张，建议尽快购票或备选 {}".format(alt) if alt else "⚠ 余票紧张，建议尽快购票"
    if alt:
        return "⚠ 该车次无票，建议改选 {} 或调整日期".format(alt)
    return "⚠ 该车次无票，建议改选其他车次或调整日期"


def _transport_confidence(transport) -> str:
    """realtime when at least one leg carries a live 12306 train sample."""
    for key in ("outbound", "return"):
        leg = (transport or {}).get(key) or {}
        if leg.get("status") != "error" and _first_train(leg):
            return "realtime"
    return "estimated"


def _intercity(transport):
    """Schema intercity leg built from the outbound 12306 train, or None."""
    outbound = (transport or {}).get("outbound") or {}
    train = _first_train(outbound)
    if not train:
        return None
    price = train.get("second_seat")
    return {
        "from": {
            "name": _station_name(outbound.get("from")),
            "time": str(train.get("start_time") or ""),
        },
        "to": {
            "name": _station_name(outbound.get("to")),
            "time": str(train.get("arrive_time") or ""),
        },
        "line": str(train.get("code") or train.get("train_no") or ""),
        "duration": str(train.get("duration") or ""),
        "price": "¥{}".format(price) if price not in (None, "") else "",
        "train_no": str(train.get("train_no") or ""),
    }


# 估算层单价（元）。12306 之外的品类没有实时价，按保守人均/间夜估算，
# confidence.budget 标记为 estimated；出发前按官方渠道核验。
ESTIMATE_TICKET_PER_DAY = 120   # 门票 元/人/天
ESTIMATE_MEAL_PER_DAY = 100     # 餐饮 元/人/天
ESTIMATE_HOTEL_PER_NIGHT = 300  # 住宿 元/间/夜


def _parse_price(value):
    """Numeric second-class fare from 12306 seat text, or None."""
    try:
        return float(str(value).replace("¥", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _ticket_cost(transport, travelers):
    """Round-trip train fare per candidate from realtime 12306 prices."""
    total = 0.0
    for key in ("outbound", "return"):
        train = _first_train((transport or {}).get(key) or {})
        price = _parse_price(train.get("second_seat")) if train else None
        if price is not None:
            total += price
    return round(total * max(1, int(travelers or 1)))


def _budget_section(research, transport):
    """Numeric budget profile: realtime fares where available, estimates otherwise."""
    request = research.get("request") or {}
    travelers = max(1, int(request.get("travelers") or 1))
    days = max(1, int(request.get("days") or 1))
    nights = max(1, int(request.get("nights") or days - 1))
    rooms = max(1, (travelers + 1) // 2)
    categories = {
        "车票": _ticket_cost(transport, travelers),
        "门票": round(ESTIMATE_TICKET_PER_DAY * days * travelers),
        "餐饮": round(ESTIMATE_MEAL_PER_DAY * days * travelers),
        "住宿": round(ESTIMATE_HOTEL_PER_NIGHT * nights * rooms),
    }
    return categories


def _hotel_records(destination, transport):
    """First-choice / value / avoid three-tier stay suggestions (no OTA prices)."""
    return_station = _station_name(((transport or {}).get("return") or {}).get("from"))
    station_hint = "（返程车站：{}）".format(return_station) if return_station else ""
    return [
        {
            "tier": "首选",
            "area": "{}核心片区（近地铁、近核心景点）".format(destination),
            "price": "待实时比价",
            "reason": "紧邻地铁线路与核心景点，减少每日通勤和跨城折返；具体店铺出发前按官方渠道实时比价，不虚构推荐。",
            "connection": "以地铁为主要通勤方式{}；返程日预留取行李与进站余量".format(station_hint),
            "source_ids": ["generated-itinerary"],
        },
        {
            "tier": "性价比",
            "area": "{}地铁沿线次核心片区".format(destination),
            "price": "待实时比价",
            "reason": "位于地铁沿线但避开核心商圈溢价，多一至两站通勤换取预算余量，适合预算敏感的行程。",
            "connection": "依赖地铁通勤至核心片区，建议优先选紧邻地铁站点的房源",
            "source_ids": ["generated-itinerary"],
        },
        {
            "tier": "避免",
            "area": "避免：远离地铁的远郊低价房与景区门口高价房",
            "price": "价格倒挂风险",
            "reason": "远郊低价房通勤时间不可控；景区门口高价房溢价高且节假日翻倍，性价比最差。",
            "connection": "若只能选这类区域，先核算每日通勤与溢价成本再决定",
            "source_ids": ["generated-itinerary"],
        },
    ]


def _advisory_transport_records():
    """Non-realtime advisory comparison modes (plane / drive / bus)."""
    return [
        {
            "mode": "plane",
            "title": "飞机（建议层，非实时）",
            "detail": "适合更远距离或高铁超过6小时的线路；票价随日期和舱位波动大，需自行比价，并预留机场往返与安检时间。",
            "price": "以实时查询为准",
            "recommended": False,
            "source_ids": ["generated-itinerary"],
        },
        {
            "mode": "drive",
            "title": "自驾（建议层，非实时）",
            "detail": "行程灵活、适合多点分散的目的地；需承担长途疲劳、油费过路费和停车成本，节假日易拥堵。",
            "price": "按油费与过路费实时核算",
            "recommended": False,
            "source_ids": ["generated-itinerary"],
        },
        {
            "mode": "bus",
            "title": "大巴/长途客运（建议层，非实时）",
            "detail": "票价通常低于高铁，但耗时更长、班次少；适合短途或预算优先的线路，班次以客运站实时信息为准。",
            "price": "以客运站实时票价为准",
            "recommended": False,
            "source_ids": ["generated-itinerary"],
        },
    ]


WEATHER_ICONS: list[tuple[int, int, str]] = [
    (0, 0, "☀️"),
    (1, 2, "⛅"),
    (3, 3, "☁️"),
    (45, 48, "🌫️"),
    (51, 67, "🌧️"),
    (71, 77, "🌨️"),
    (80, 82, "🌧️"),
    (85, 86, "🌨️"),
    (95, 99, "⛈️"),
]
RAIN_CODES = set(range(51, 68)) | set(range(80, 83)) | set(range(95, 100))


def _weather_icon(code) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "❔"
    for low, high, icon in WEATHER_ICONS:
        if low <= code <= high:
            return icon
    return "☁️"


def _is_rainy(day) -> bool:
    code = day.get("weather_code")
    try:
        if int(code) in RAIN_CODES:
            return True
    except (TypeError, ValueError):
        pass
    try:
        return float(day.get("precip_prob")) >= 50.0
    except (TypeError, ValueError):
        return False


def _weather_advice(day) -> str:
    if _is_rainy(day):
        prob = day.get("precip_prob")
        return "降水概率约 {}%，建议带伞并预留室内景点替代方案".format(
            prob if prob is not None else "较高"
        )
    icon = _weather_icon(day.get("weather_code"))
    if icon == "☀️":
        return "晴天外出注意防晒补水"
    if icon == "⛈️":
        return "雷雨天气避免长时间户外活动，备室内方案"
    if icon == "🌨️":
        return "低温雨雪天气注意保暖防滑"
    return "天气平稳，适合按计划出行"


def _weather_section(research, candidate) -> tuple[list[dict], list[dict], str]:
    """Map research weather payload to (schema weather[], alerts[], confidence)."""
    weather = candidate.get("weather") or {}
    request = research.get("request") or {}
    days_count = max(1, int(request.get("days") or 1))
    start_date = request.get("start_date")
    if weather.get("status") != "ok" or not weather.get("days") or not start_date:
        alert = {
            "source": "RULE",
            "type": "天气数据不可用",
            "title": "天气数据不可用",
            "detail": str(weather.get("message") or "天气接口不可用或超时，本次为无天气版面；建议出行前自行查询天气预报。"),
        }
        return [], [alert], "estimated"
    entries = []
    alerts = []
    base = date.fromisoformat(start_date)
    for index, day in enumerate(weather["days"][:days_count]):
        day_date = base + timedelta(days=index)
        icon = _weather_icon(day.get("weather_code"))
        temp_low = day.get("temp_min")
        temp_high = day.get("temp_max")
        entries.append(
            {
                "day": "Day {} · {:02d}/{:02d}".format(index + 1, day_date.month, day_date.day),
                "date": day_date.isoformat(),
                "icon": icon,
                "temp": "{}–{}℃".format(
                    round(float(temp_low)) if temp_low is not None else "?",
                    round(float(temp_high)) if temp_high is not None else "?",
                ),
                "advice": _weather_advice(day),
            }
        )
        if _is_rainy(day):
            alerts.append(
                {
                    "source": "RULE",
                    "type": "雨天替代",
                    "title": "Day {} 预报有雨".format(index + 1),
                    "detail": "当日降水概率约 {}%，建议将该天户外景点替换为室内项目；替代方案由宿主 AI 基于已有景点池重排。".format(
                        day.get("precip_prob") if day.get("precip_prob") is not None else "较高"
                    ),
                }
            )
    return entries, alerts, "realtime"


def _candidate_notes(candidate):
    references = candidate.get("references")
    if not isinstance(references, dict):
        references = {"xiaohongshu": candidate.get("xiaohongshu", {})}
    notes = []
    for platform, payload in references.items():
        if not isinstance(payload, dict):
            continue
        for note in payload.get("notes", []):
            if isinstance(note, dict):
                notes.append((platform, note))
    return notes


def _candidate_source_ids(candidate):
    return list(
        dict.fromkeys(
            note.get("source_id")
            for _, note in _candidate_notes(candidate)
            if note.get("source_id")
        )
    )


def _guide_sources(research, candidate):
    source_ids = {"12306-official"} | set(_candidate_source_ids(candidate))
    sources = []
    seen_ids = set()
    for source in research.get("sources", []):
        source_id = source.get("id")
        if source_id not in source_ids or source_id in seen_ids:
            continue
        seen_ids.add(source_id)
        sources.append(dict(source))
    if not any(source.get("id") == "12306-official" for source in sources):
        sources.insert(
            0,
            {
                "id": "12306-official",
                "title": "12306 train and station data",
                "type": "official",
                "checked_at": research.get("collected_at", date.today().isoformat()),
                "url": "https://www.12306.cn/",
            },
        )
    sources.append(
        {
            "id": "generated-itinerary",
            "title": "基于本次采集结果生成的行程编排，出发前需核验",
            "type": "estimate",
            "checked_at": research.get("collected_at", date.today().isoformat()),
        }
    )
    return sources


def _guide_item(name, item_type, start, end, description, source_ids):
    return {
        "type": item_type,
        "name": name,
        "start": format_hhmm(start),
        "end": format_hhmm(end),
        "description": description,
        "source_ids": list(dict.fromkeys(source_ids)),
    }


def _append_item(items, cursor, desired_start, duration, name, item_type, description, source_ids):
    start = max(cursor, desired_start)
    end = start + duration
    if end > 23 * 60:
        return cursor
    items.append(_guide_item(name, item_type, start, end, description, source_ids))
    return end


def _build_days(request, candidate, generated_source_ids):
    days = max(1, int(request.get("days") or 1))
    start_date = date.fromisoformat(request["start_date"])
    destination = candidate.get("destination", "目的地")
    rail = candidate.get("transport") or {}
    reference_titles = [
        str(note.get("title", "")).strip()
        for _, note in _candidate_notes(candidate)
        if str(note.get("title", "")).strip()
    ]
    reference_hint = (
        "参考线索：{}；具体景点、开放时间和预约规则出发前核验。".format("；".join(reference_titles[:3]))
        if reference_titles
        else "当前没有可用的参考平台详情；请按目的地官方信息补齐景点和预约安排。"
    )
    reference_ids = list(_candidate_source_ids(candidate))
    source_ids = ["generated-itinerary"] + reference_ids[:3]
    outbound = rail.get("outbound", {})
    return_leg = rail.get("return", {})
    result = []

    for index in range(days):
        day_number = index + 1
        items = []
        if days == 1:
            cursor = 6 * 60
            train = _first_train(outbound)
            if train:
                start, end = _train_window(train, 6 * 60, 9 * 60)
                items.append(
                    _guide_item(
                        "去程：{}".format(_train_label(outbound, train)),
                        "transport",
                        start,
                        end,
                        _train_detail(outbound, train),
                        ["12306-official"],
                    )
                )
                cursor = end
            cursor = _append_item(
                items,
                cursor,
                max(cursor + 30, 10 * 60),
                120,
                "{}核心片区探索".format(destination),
                "activity",
                reference_hint,
                source_ids,
            )
            if not items:
                items.append(
                    _guide_item(
                        "当前候选行程待补充",
                        "activity",
                        9 * 60,
                        10 * 60,
                        reference_hint,
                        source_ids,
                    )
                )
        elif index == 0:
            cursor = 6 * 60
            train = _first_train(outbound)
            if train:
                start, end = _train_window(train, 6 * 60, 9 * 60)
                items.append(
                    _guide_item(
                        "去程：{}".format(_train_label(outbound, train)),
                        "transport",
                        start,
                        end,
                        _train_detail(outbound, train),
                        ["12306-official"],
                    )
                )
                cursor = end
            cursor = _append_item(
                items,
                cursor,
                max(cursor + 30, 9 * 60 + 30),
                30,
                "抵达后寄存行李 / 办理入住",
                "hotel",
                "先确认住宿区域与当日核心片区的通达性，不把未核验酒店当成推荐。",
                ["generated-itinerary"],
            )
            cursor = _append_item(
                items,
                cursor,
                10 * 60 + 30,
                120,
                "{}首个核心片区".format(destination),
                "activity",
                reference_hint,
                source_ids,
            )
            _append_item(
                items,
                cursor,
                14 * 60,
                180,
                "{}主题路线（按参考线索筛选）".format(destination),
                "activity",
                "本段只使用参考内容作为线索，景点营业状态和预约结果需单独核验。",
                source_ids,
            )
        elif index == days - 1:
            cursor = 9 * 60
            train = _first_train(return_leg)
            if train:
                start, end = _train_window(train, 15 * 60, 18 * 60)
                if start < 12 * 60:
                    prep_start = max(5 * 60, start - 75)
                    prep_end = max(prep_start + 30, start - 20)
                    if prep_end < start:
                        items.append(
                            _guide_item(
                                "退房并前往车站",
                                "activity",
                                prep_start,
                                prep_end,
                                "按12306车次和车站重新核算进站时间。",
                                ["generated-itinerary"],
                            )
                        )
                else:
                    cursor = _append_item(
                        items,
                        cursor,
                        9 * 60,
                        90,
                        "住宿区早餐与最后补逛",
                        "activity",
                        "只安排住宿区附近内容，给取行李和进站留出余量。",
                        ["generated-itinerary"],
                    )
                    cursor = max(cursor, start - 45)
                if not items or items[-1]["end"] != format_hhmm(end):
                    items.append(
                        _guide_item(
                            "返程：{}".format(_train_label(return_leg, train)),
                            "transport",
                            start,
                            end,
                            _train_detail(return_leg, train),
                            ["12306-official"],
                        )
                    )
            else:
                _append_item(
                    items,
                    cursor,
                    9 * 60,
                    120,
                    "返程方案待核验",
                    "transport",
                    "当前日期没有可用直达样本；不要把缺少数据当成有票，需重新查询中转换乘或调整日期。",
                    ["12306-official", "generated-itinerary"],
                )
        else:
            cursor = _append_item(
                items,
                9 * 60,
                9 * 60,
                180,
                "{}核心片区主题路线".format(destination),
                "activity",
                reference_hint,
                source_ids,
            )
            cursor = _append_item(
                items,
                cursor,
                12 * 60,
                60,
                "午餐与休息",
                "meal",
                "餐厅和价格不使用未核验名单，按当天位置和实时营业情况选择。",
                ["generated-itinerary"],
            )
            _append_item(
                items,
                cursor,
                14 * 60,
                180,
                "{}参考线索延展路线".format(destination),
                "activity",
                "根据当天预约和体力在同片区内取舍，避免跨城折返。",
                source_ids,
            )
        if not items:
            items.append(
                _guide_item(
                    "{}行程待核验".format(destination),
                    "activity",
                    9 * 60,
                    10 * 60,
                    "当前采集结果不足以编排更多硬事实，出发前补齐官方景点和交通信息。",
                    ["generated-itinerary"],
                )
            )
        result.append(
            {
                "day": day_number,
                "date": (start_date + timedelta(days=index)).isoformat(),
                "title": (
                    "抵达与首个核心片区"
                    if index == 0
                    else "收尾与返程"
                    if index == days - 1
                    else "{}主题路线".format(destination)
                ),
                "items": items,
            }
        )
    return result


def guide_from_research(research, candidate):
    request = research.get("request", {})
    destination = candidate.get("destination", "目的地")
    source_ids = ["12306-official"]
    source_ids.extend(_candidate_source_ids(candidate))
    source_ids.append("generated-itinerary")
    notes = _candidate_notes(candidate)
    transport = candidate.get("transport") or {}
    transport_confidence = _transport_confidence(transport)
    transport_records = []
    ticket_alerts = []
    for label, leg in (("去程", transport.get("outbound", {})), ("返程", transport.get("return", {}))):
        train = _first_train(leg)
        detail = _train_detail(leg, train)
        warning = _ticket_warning(leg, train)
        if warning:
            detail = "{}；{}".format(detail, warning)
            ticket_alerts.append(
                {
                    "source": "RULE",
                    "type": "余票告警",
                    "title": "{} {} 余票告警".format(
                        label, (train or {}).get("code") or (train or {}).get("train_no") or "车次"
                    ),
                    "detail": warning,
                }
            )
        title = "{}：{}".format(label, _train_label(leg, train) if train else "暂无直达样本")
        if transport_confidence != "realtime":
            title += "（非实时）"
            detail = "{}；「非实时」建议层：此为按参考线索的建议方案，出发前请重新查询 12306 实时车次。".format(detail)
        transport_records.append(
            {
                "mode": "train",
                "title": title,
                "detail": detail,
                "price": _train_price(train),
                "recommended": bool(train),
                "source_ids": ["12306-official"],
            }
        )
    transport_records.extend(_advisory_transport_records())
    reference_titles = [
        str(note.get("title")).strip() for _, note in notes if str(note.get("title", "")).strip()
    ]
    warnings = list(candidate.get("warnings", []))
    if not transport_records or transport.get("status") != "ok":
        warnings.append("交通数据不完整，出发前需要重新核验")
    weather_entries, weather_alerts, weather_confidence = _weather_section(research, candidate)
    budget_categories = _budget_section(research, transport)
    confidence = {"transport": transport_confidence, "weather": weather_confidence, "budget": "estimated"}
    guide = {
        "schema_version": "1.0",
        "meta": {
            "title": "{}出发 {}天{}夜候选攻略".format(
                request.get("origin", ""), request.get("days", 1), request.get("nights", 0)
            ),
            "subtitle": "{}候选的独立行程与证据".format(destination),
            "destination": destination,
            "origin": request.get("origin", ""),
            "language": "zh-CN",
            "start_date": request["start_date"],
            "days": max(1, int(request.get("days") or 1)),
            "travelers": request.get("travelers", 1),
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
        },
        "preferences": {
            "budget_level": "comfortable",
            "pace": "balanced",
            "group_type": "自由行",
            "transport": ["transit", "walk"],
            "earliest_start": "05:00",
            "latest_end": "21:30",
        },
        "sources": _guide_sources(research, candidate),
        "transport": transport_records,
        "hotels": _hotel_records(destination, transport),
        "days": _build_days(request, candidate, source_ids),
        "avoid": [
            {
                "wrong": "把参考平台笔记当成实时开放和预约信息",
                "right": "只把它当作线索，出发前核验官方页面",
                "source_ids": list(dict.fromkeys(source_ids)),
            },
            {
                "wrong": "把12306没有返回结果当成有票",
                "right": "保留为待核验，重新查询直达或中转换乘方案",
                "source_ids": ["12306-official"],
            },
        ],
        "budget": {
            "selected": "comfortable",
            "profiles": {
                "comfortable": {
                    "categories": budget_categories,
                    "total": sum(budget_categories.values()),
                }
            },
        },
        "tips": [
            "这是{}的独立候选攻略，不会复用推荐城市的景点和行程。".format(destination),
            "交通样本来自本次12306采集；余票、价格、经停站和车次在出发前重新核验。",
            "参考平台线索：{}。".format("；".join(reference_titles[:3]) or "暂无可用详情"),
            "预算说明：车票按12306实时票价累计；门票（{}元/人/天）、餐饮（{}元/人/天）、住宿（{}元/间/夜）为估算层单价，出发前按官方渠道和实时比价核验。".format(
                ESTIMATE_TICKET_PER_DAY, ESTIMATE_MEAL_PER_DAY, ESTIMATE_HOTEL_PER_NIGHT
            ),
        ]
        + ["交通提醒：{}".format(warning) for warning in warnings],
    }
    intercity = _intercity(transport)
    if intercity:
        guide["intercity"] = intercity
    guide["confidence"] = confidence
    if weather_entries:
        guide["weather"] = weather_entries
    alerts = ticket_alerts + weather_alerts
    if jev_client.plausibility_point_enabled():
        # 行程合理性判断点（advisory-only，见 docs/adr/0001）：每天整体
        # 评估一次，与素材可信度点共享同一客户端的限次预算。
        summary, plausibility_alerts = apply_jev_plausibility_scoring(
            guide, jev_client.shared_client()
        )
        guide["jev_plausibility"] = summary
        alerts.extend(plausibility_alerts)
    if alerts:
        guide["alerts"] = alerts
    return guide


def build_candidate_guides(research, output_dir):
    output_dir = Path(output_dir)
    guides_dir = output_dir / "guides"
    guides = []
    for index, candidate in enumerate(research.get("destinations", []), start=1):
        destination = candidate.get("destination", "目的地")
        output_base = guides_dir / "{:02d}-{}".format(index, _slug(destination))
        result = build(guide_from_research(research, candidate), output_base)
        files = []
        if result.get("status") == "ok":
            files = [
                Path(path).resolve().relative_to(output_dir.resolve()).as_posix()
                for path in result["files"]
            ]
            candidate["guide_files"] = files
            candidate["guide_html"] = next(path for path in files if path.endswith(".html"))
        else:
            candidate["guide_files"] = []
            candidate["guide_html"] = ""
        entry = {"destination": destination, "status": result.get("status"), "files": files}
        if result.get("status") != "ok":
            entry["message"] = result.get("message", "攻略构建失败")
            entry["report"] = result.get("report", {})
        guides.append(entry)
    return guides


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect and compare real travel sources")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destinations", required=True, help="Comma-separated city names")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD; required for 12306 queries")
    parser.add_argument("--return-date", help="YYYY-MM-DD; defaults to day N")
    parser.add_argument("--reference-platforms", default="xiaohongshu")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--nights", type=int, default=2)
    parser.add_argument("--travelers", type=int, default=1)
    parser.add_argument("--depart-after")
    parser.add_argument("--return-before")
    parser.add_argument("--reference-limit", "--xhs-limit", dest="reference_limit", type=int, default=8)
    parser.add_argument("--reference-details", "--xhs-details", dest="reference_details", type=int, default=3)
    parser.add_argument("--rail-limit", type=int, default=10)
    parser.add_argument("--rail-details", action="store_true")
    parser.add_argument("--output-dir", default="generated/trip")
    args = parser.parse_args()
    args.reference_platforms = [
        platform.strip().lower()
        for platform in args.reference_platforms.split(",")
        if platform.strip()
    ]
    unknown_platforms = set(args.reference_platforms) - REFERENCE_PLATFORMS
    if unknown_platforms:
        parser.error("unsupported reference platforms: {}".format(", ".join(sorted(unknown_platforms))))
    if args.start_date:
        try:
            from datetime import date

            date.fromisoformat(args.start_date)
        except ValueError:
            parser.error("start-date must use YYYY-MM-DD")
    if args.return_date:
        try:
            from datetime import date

            date.fromisoformat(args.return_date)
        except ValueError:
            parser.error("return-date must use YYYY-MM-DD")
    for name in ("depart_after", "return_before"):
        value = getattr(args, name)
        if value is not None and _time_minutes(value) is None:
            parser.error("{} must use HH:MM".format(name.replace("_", "-")))

    research = collect(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    guides = build_candidate_guides(research, output_dir)
    research_path = output_dir / "research.json"
    comparison_path = output_dir / "comparison.html"
    comparison_json_path = output_dir / "comparison.json"
    research_path.write_text(
        json.dumps(research, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    comparison_path.write_text(render_html(research), encoding="utf-8")
    comparison_json_path.write_text(
        json.dumps(
            {
                "request": research.get("request", {}),
                "candidates": summarize(research),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "ok" if all(item["status"] == "ok" for item in guides) else "error",
                "files": [
                    str(research_path),
                    str(comparison_path),
                    str(comparison_json_path),
                ],
                "guides": guides,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all(item["status"] == "ok" for item in guides) else 1


if __name__ == "__main__":
    raise SystemExit(main())
