#!/usr/bin/env python3
"""Export a structured guide to Markdown, iCalendar, GeoJSON and XHS notes."""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .guide_utils import load_json, write_json
except ImportError:
    from guide_utils import load_json, write_json


DEFAULT_TIMEZONE = "Asia/Shanghai"
HOTEL_SLOT = ("14:00", "14:30")
SUGGESTED_SLOTS = (
    ("09:00", "11:00"),
    ("11:00", "12:30"),
    ("13:00", "15:00"),
    ("15:30", "17:00"),
    ("17:30", "19:00"),
    ("19:30", "21:00"),
)
MODE_EMOJI = {"train": "🚄", "flight": "✈️", "drive": "🚗", "bus": "🚌"}
VTIMEZONE_SHANGHAI = (
    "BEGIN:VTIMEZONE",
    "TZID:" + DEFAULT_TIMEZONE,
    "BEGIN:STANDARD",
    "DTSTART:19700101T000000",
    "TZOFFSETFROM:+0800",
    "TZOFFSETTO:+0800",
    "TZNAME:CST",
    "END:STANDARD",
    "END:VTIMEZONE",
)


def markdown_text(guide: Dict[str, Any]) -> str:
    meta = guide.get("meta", {})
    lines = ["# {}".format(meta.get("title", "旅游攻略")), ""]
    lines.append(
        "- 目的地：{}".format(meta.get("destination", ""))
    )
    lines.append("- 日期：{}".format(meta.get("start_date", "")))
    lines.append("- 人数：{}".format(meta.get("travelers", 1)))
    lines.append("")
    for day in guide.get("days", []):
        lines.extend(
            [
                "## Day {} · {}".format(day.get("day", ""), day.get("title", "")),
                "",
            ]
        )
        for item in day.get("items", []):
            line = "- {}–{} **{}**".format(
                item.get("start", ""), item.get("end", ""), item.get("name", "")
            )
            if item.get("description"):
                line += " — {}".format(item["description"])
            lines.append(line)
        lines.append("")
    if guide.get("tips"):
        lines.extend(["## 出行提示", ""])
        lines.extend("- {}".format(tip) for tip in guide["tips"])
        lines.append("")
    lines.append("## 数据来源")
    lines.append("")
    for source in guide.get("sources", []):
        label = source.get("title", source.get("id", "来源"))
        url = source.get("url")
        checked = source.get("checked_at", "未注明")
        lines.append("- [{}]({})（核实：{}）".format(label, url, checked) if url else "- {}（核实：{}）".format(label, checked))
    return "\n".join(lines).rstrip() + "\n"


def ics_escape(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold_ics(line: str) -> List[str]:
    """RFC 5545 3.1: fold lines over 75 octets; continuations start with a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    parts = [raw[:75]]
    raw = raw[75:]
    while raw:
        cut = min(73, len(raw))
        while cut < len(raw) and raw[cut] & 0xC0 == 0x80:
            cut -= 1
        parts.append(b" " + raw[:cut])
        raw = raw[cut:]
    return [part.decode("utf-8") for part in parts]


def _parse_hhmm(value: Any) -> Optional[str]:
    """Return a normalized HH:MM string, or None when not a wall-clock time."""
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() and len(p) == 2 for p in parts):
        return None
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return "{:02d}:{:02d}".format(hour, minute)


def _shift_hhmm(hhmm: str, minutes: int) -> str:
    moment = datetime.strptime(hhmm, "%H:%M") + timedelta(minutes=minutes)
    return moment.strftime("%H:%M")


def _fallback_date(guide: Dict[str, Any], index: int) -> str:
    """Date for day ``index`` (0-based) derived from meta.start_date."""
    start = str(guide.get("meta", {}).get("start_date", "")).strip()
    try:
        base = datetime.strptime(start, "%Y-%m-%d")
    except ValueError:
        return ""
    return (base + timedelta(days=index)).strftime("%Y-%m-%d")


def _window(item: Dict[str, Any]) -> Tuple[str, str]:
    """Precise start/end when present, otherwise a suggested time slot."""
    start = _parse_hhmm(item.get("start"))
    end = _parse_hhmm(item.get("end"))
    if start and end:
        return start, end
    if start:
        return start, _shift_hhmm(start, 90)
    if end:
        return _shift_hhmm(end, -90), end
    return ("", "")


def collect_events(guide: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flatten a guide into calendar events: items, intercity, hotel check-in.

    Events without precise times get suggested slots; entries whose date cannot
    be resolved are skipped.
    """
    days = guide.get("days", [])
    dates = [
        str(day.get("date") or _fallback_date(guide, index))
        for index, day in enumerate(days)
    ]
    events: List[Dict[str, str]] = []
    for index, day in enumerate(days):
        slot = 0
        for item in day.get("items", []):
            start, end = _window(item)
            if not start:
                start, end = SUGGESTED_SLOTS[slot % len(SUGGESTED_SLOTS)]
                slot += 1
            events.append(
                {
                    "date": dates[index],
                    "summary": item.get("name", "行程"),
                    "start": start,
                    "end": end,
                    "description": item.get("description", ""),
                }
            )
    first_date = dates[0] if dates else ""
    intercity = guide.get("intercity") or {}
    if intercity.get("from") and first_date:
        start = _parse_hhmm(intercity["from"].get("time"))
        end = _parse_hhmm((intercity.get("to") or {}).get("time"))
        if not start or not end:
            start, end = SUGGESTED_SLOTS[0]
        label = intercity.get("train_no") or intercity.get("line") or "跨城交通"
        summary = "🚄 {} {}→{}".format(
            label, intercity["from"].get("name", ""), (intercity.get("to") or {}).get("name", "")
        )
        details = [intercity.get("line", ""), intercity.get("duration", "")]
        if intercity.get("price"):
            details.append("票价 {}".format(intercity["price"]))
        events.append(
            {
                "date": first_date,
                "summary": summary,
                "start": start,
                "end": end,
                "description": " · ".join(part for part in details if part),
            }
        )
    elif not intercity:
        for transport in guide.get("transport", []):
            if not transport.get("recommended"):
                continue
            emoji = MODE_EMOJI.get(transport.get("mode", ""), "🚗")
            title = transport.get("title") or transport.get("mode") or "交通"
            events.append(
                {
                    "date": first_date,
                    "summary": "{} {}".format(emoji, title),
                    "start": SUGGESTED_SLOTS[0][0],
                    "end": SUGGESTED_SLOTS[0][1],
                    "description": transport.get("detail", ""),
                }
            )
    hotels = guide.get("hotels", [])
    if hotels and first_date:
        hotel = hotels[0]
        events.append(
            {
                "date": first_date,
                "summary": "🏨 入住 {}".format(hotel.get("area", "酒店")),
                "start": HOTEL_SLOT[0],
                "end": HOTEL_SLOT[1],
                "description": hotel.get("connection") or hotel.get("reason", ""),
            }
        )
    events.sort(key=lambda event: (event["date"], event["start"]))
    return [event for event in events if event["date"]]


def ics_text(guide: Dict[str, Any]) -> str:
    timezone = guide.get("meta", {}).get("timezone", DEFAULT_TIMEZONE)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Travel Guide Generator//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    if timezone == DEFAULT_TIMEZONE:
        lines.extend(VTIMEZONE_SHANGHAI)
    for index, event in enumerate(collect_events(guide)):
        day = event["date"].replace("-", "")
        lines.extend(
            [
                "BEGIN:VEVENT",
                "UID:trip-{}@travel-guide-generator".format(index),
                "DTSTAMP:{}T000000Z".format(day),
                "DTSTART;TZID={}:{}T{}00".format(timezone, day, event["start"].replace(":", "")),
                "DTEND;TZID={}:{}T{}00".format(timezone, day, event["end"].replace(":", "")),
                "SUMMARY:{}".format(ics_escape(event["summary"])),
                "DESCRIPTION:{}".format(ics_escape(event["description"])),
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    folded: List[str] = []
    for line in lines:
        folded.extend(_fold_ics(line))
    return "\r\n".join(folded) + "\r\n"


def _is_complete(guide: Dict[str, Any]) -> bool:
    """Full copy needs intercity (train info) or item coordinates."""
    if (guide.get("intercity") or {}).get("train_no"):
        return True
    return any(
        item.get("coords")
        for day in guide.get("days", [])
        for item in day.get("items", [])
    )


def xhs_title(guide: Dict[str, Any]) -> str:
    meta = guide.get("meta", {})
    destination = meta.get("destination") or "这里"
    emoji = (meta.get("emojis") or "✨").strip()[:1] or "✨"
    count = len(guide.get("days", []))
    template = "{}{}{}日这样玩！不踩雷" if count else "{}{}这样玩！不踩雷"
    return template.format(emoji, destination, count)[:20]


def xhs_text(guide: Dict[str, Any]) -> str:
    """Xiaohongshu note copy. Only facts present in the schema are emitted."""
    meta = guide.get("meta", {})
    destination = meta.get("destination") or "这里"
    lines = [xhs_title(guide), ""]
    if not _is_complete(guide):
        lines.extend(
            [
                "⚠️ 数据不全：缺少车次/坐标信息，以下为简版参考，出发前请自行核实。",
                "",
            ]
        )
    intercity = guide.get("intercity") or {}
    days = guide.get("days", [])
    if days:
        for index, day in enumerate(days):
            lines.append("📅 Day {}｜{}".format(day.get("day", index + 1), day.get("title", "")))
            if index == 0 and intercity.get("train_no"):
                lines.append(
                    "🚄 {} {}→{} {}-{}（{}）".format(
                        intercity["train_no"],
                        (intercity.get("from") or {}).get("name", ""),
                        (intercity.get("to") or {}).get("name", ""),
                        (intercity.get("from") or {}).get("time", ""),
                        (intercity.get("to") or {}).get("time", ""),
                        intercity.get("duration", ""),
                    )
                )
            for item in day.get("items", []):
                lines.append(_xhs_item_line(item))
            lines.append("")
    else:
        # v2-only guide without days: fall back to spots time windows.
        by_day: Dict[int, List[Dict[str, Any]]] = {}
        for spot in guide.get("spots", []):
            by_day.setdefault(spot.get("day", 1), []).append(spot)
        for day_number in sorted(by_day):
            lines.append("📅 Day {}".format(day_number))
            for spot in by_day[day_number]:
                lines.append("· {} {}".format(spot.get("name", ""), spot.get("tw", "")))
            lines.append("")
    for transport in guide.get("transport", []):
        if transport.get("recommended"):
            lines.append("🚗 {}：{}".format(transport.get("title", ""), transport.get("detail", "")))
            break
    for tip in guide.get("tips", [])[:2]:
        lines.append("💡 {}".format(tip))
    lines.append(xhs_tags(guide))
    return "\n".join(lines).rstrip() + "\n"


def _xhs_item_line(item: Dict[str, Any]) -> str:
    name = item.get("name", "")
    start = _parse_hhmm(item.get("start"))
    end = _parse_hhmm(item.get("end"))
    if start and end:
        window = "{}-{}".format(start, end)
    elif item.get("tw"):
        window = str(item["tw"])
    else:
        window = ""
    line = "· {}".format(name)
    if window:
        line = "· {} {}".format(name, window)
    if item.get("price"):
        line += "（{}）".format(item["price"])
    return line


def xhs_tags(guide: Dict[str, Any]) -> str:
    destination = (guide.get("meta", {}).get("destination") or "旅行").replace(" ", "")
    candidates = [
        "#{}旅游".format(destination),
        "#{}攻略".format(destination),
        "#周末去哪儿",
        "#旅行攻略",
        "#出行建议",
        "#小众玩法",
        "#结伴旅行",
    ]
    return " ".join(candidates[:7])


def geojson_data(guide: Dict[str, Any]) -> Dict[str, Any]:
    features = []
    for day in guide.get("days", []):
        for item in day.get("items", []):
            coords = item.get("coords")
            if not coords or len(coords) != 2:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": coords},
                    "properties": {
                        "name": item.get("name", ""),
                        "day": day.get("day"),
                        "date": day.get("date"),
                        "start": item.get("start"),
                        "end": item.get("end"),
                        "type": item.get("type", "spot"),
                    },
                }
            )
    return {"type": "FeatureCollection", "features": features}


def export_all(guide: Dict[str, Any], output_base: Any) -> List[str]:
    base = Path(output_base)
    base.parent.mkdir(parents=True, exist_ok=True)
    markdown_path = base.with_suffix(".md")
    calendar_path = base.with_suffix(".ics")
    geojson_path = base.with_suffix(".geojson")
    xhs_path = base.with_suffix(".xhs.md")
    markdown_path.write_text(markdown_text(guide), encoding="utf-8")
    with calendar_path.open("w", encoding="utf-8", newline="") as calendar_file:
        calendar_file.write(ics_text(guide))
    write_json(geojson_path, geojson_data(guide))
    xhs_path.write_text(xhs_text(guide), encoding="utf-8")
    return [str(markdown_path), str(calendar_path), str(geojson_path), str(xhs_path)]


def main() -> None:
    parser = argparse.ArgumentParser(description="导出旅游攻略")
    parser.add_argument("input", help="攻略 JSON 文件")
    parser.add_argument("--output-base", help="输出基础路径（不含扩展名）")
    args = parser.parse_args()
    guide = load_json(args.input)
    output_base = args.output_base or str(Path(args.input).with_suffix(""))
    print(
        json.dumps(
            {"status": "ok", "files": export_all(guide, output_base)},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
