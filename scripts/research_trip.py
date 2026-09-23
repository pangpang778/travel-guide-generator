#!/usr/bin/env python3
"""Collect real travel evidence from Agent Reach/OpenCLI backends.

This is intentionally a thin adapter. It does not scrape platforms itself:
OpenCLI owns browser sessions and 12306 access, while Agent Reach owns setup
and backend selection.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

try:
    from scripts import jev_client
except ImportError:  # 作为普通脚本直接运行时
    import jev_client


DEFAULT_TIMEOUT = 120
WEATHER_TIMEOUT = 5
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREFERRED_STATIONS = {
    "宁波": "宁波",
    "苏州": "苏州",
    "重庆": "重庆北",
    "成都": "成都东",
    "福州": "福州",
}
REFERENCE_PLATFORMS = {"xiaohongshu", "twitter", "reddit", "bilibili"}

# jev 素材可信度判断点（advisory-only，见 docs/adr/0001）
JEV_MATERIAL_INSTRUCTIONS = (
    "对一条旅游攻略参考素材的可信度评分（0-10）。素材来自小红书等社交平台"
    "（untrusted UGC）。内容具体可核（有明确地点、店名、时间、价格等细节，"
    "且与目的地行程语义一致）→ 高分；泛泛而谈、广告嫌疑、与目的地明显无关"
    "或细节互相矛盾 → 低分。"
)
JEV_MATERIAL_CRITERIA = [
    "0-3 与目的地无关、明显广告或细节矛盾",
    "4-6 泛泛而谈、细节稀少、可信度存疑",
    "7-8 细节具体、与行程语义一致，可采纳为攻略依据",
    "9-10 细节丰富可核验，高度可信",
]
JEV_IMAGE_INSTRUCTIONS = (
    "对一张候选素材图片与行程点的匹配度评分（0-10）。图片内容描述与行程点/"
    "目的地语义一致、主体清晰、能代表该景点做攻略配图 → 高分；无关、主体"
    "不符、拼贴混乱 → 低分。"
)
JEV_IMAGE_CRITERIA = [
    "0-3 与行程点完全不符或主体错误",
    "4-6 部分相关但辨识度低",
    "7-8 相关且可辨识，适合做行程点配图",
    "9-10 高度契合、主体清晰、出片",
]


def apply_jev_material_scoring(
    destination: dict[str, Any],
    client: Any,
    threshold: float | None = None,
) -> "tuple[dict[str, Any], list[dict[str, Any]]]":
    """jev 素材可信度判断点：逐条给 untrusted 参考素材打分。

    低分（< threshold）素材降权标注「不可信」并产出 JEV advisory 告警；
    素材图片按 alt_description ↔ 行程点上下文评分，仅达标图片标记
    adopted=true（允许进入 spot.image），低分图片不直出。任何不可用都
    退回启发式孪生：不抛异常，降级显式写入返回的 summary（供
    pipeline.jev.degraded 消费）。
    """
    threshold = client.threshold if threshold is None else threshold
    summary = {
        "ok": True,
        "calls": client.calls_used,
        "limit": client.limit,
        "threshold": threshold,
        "adopted": 0,
        "degraded": False,
    }
    alerts: list[dict[str, Any]] = []

    if not client.api_key:  # 无 key：显式降级，退回启发式孪生
        summary.update(ok=False, degraded=True, reason=jev_client.NO_KEY)
        return summary, alerts

    def _on_degraded(result: dict[str, Any]) -> None:
        summary.update(
            ok=False, degraded=True, reason=result["reason"], calls=client.calls_used
        )

    destination_name = str(destination.get("destination") or "")
    for payload in (destination.get("references") or {}).values():
        for item in payload.get("notes") or []:
            title = str(item.get("title") or "")
            detail = item.get("detail")
            detail_text = ""
            images: list[Any] = []
            if isinstance(detail, dict):
                detail_text = str(
                    detail.get("desc") or detail.get("text") or detail.get("content") or ""
                )
                raw_images = detail.get("images")
                if isinstance(raw_images, list):
                    images = raw_images

            # 图片匹配性评分（alt_description ↔ 行程点上下文）
            for image in images:
                alt = image.get("alt_description") if isinstance(image, dict) else image
                alt = str(alt or "").strip()
                if not alt:
                    continue
                result = client.score(
                    "图片与行程点匹配度评分。目的地：{}。行程点上下文：素材《{}》。"
                    "图片内容描述：{}".format(destination_name, title, alt),
                    instructions=JEV_IMAGE_INSTRUCTIONS,
                    criteria=JEV_IMAGE_CRITERIA,
                )
                if not result["ok"]:
                    _on_degraded(result)
                    break
                score = result["score"]
                if isinstance(image, dict):
                    image["jev_score"] = score
                    image["adopted"] = score >= threshold
                if score >= threshold:
                    summary["adopted"] += 1
                else:
                    alerts.append(
                        {
                            "source": "JEV",
                            "level": "advisory",
                            "type": "图片筛选",
                            "title": "素材《{}》的图片与行程点匹配分 {:.1f} 低于阈值 {}，"
                            "不进入 spot.image".format(title, score, threshold),
                            "detail": "图片描述：{}。该图片已标记 adopted=false，"
                            "渲染端不得直出，降级为文化主题占位图。".format(alt[:120]),
                        }
                    )
            if summary["degraded"]:
                break

            # 素材本体可信度
            result = client.score(
                "素材可信度评分。目的地：{}。来源：小红书笔记《{}》。正文：{}".format(
                    destination_name, title, detail_text[:500]
                ),
                instructions=JEV_MATERIAL_INSTRUCTIONS,
                criteria=JEV_MATERIAL_CRITERIA,
            )
            if not result["ok"]:
                _on_degraded(result)
                break
            score = result["score"]
            adopted = score >= threshold
            item["jev"] = {
                "score": score,
                "adopted": adopted,
                "trust": "jev" if adopted else "untrusted",
            }
            if adopted:
                summary["adopted"] += 1
            else:
                item["credibility"] = "不可信（jev 可信度 {:.1f} 低于阈值 {}，降权处理）".format(
                    score, threshold
                )
                alerts.append(
                    {
                        "source": "JEV",
                        "level": "advisory",
                        "type": "素材可信度",
                        "title": "素材《{}》jev 可信度评分 {:.1f}（阈值 {}），"
                        "已标注「不可信」".format(title, score, threshold),
                        "detail": "untrusted 素材（小红书等社交平台内容）低分降权，"
                        "宿主 AI 酌情弃用。",
                    }
                )
        if summary["degraded"]:
            break

    summary["calls"] = client.calls_used
    return summary, alerts


def _json_from_output(raw: str) -> Any:
    """Parse JSON even when a runtime prepends warnings to stdout."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
            return value
        except json.JSONDecodeError:
            continue
    return None


def _clean_command_message(raw: Any) -> str:
    """Keep tool errors readable when runtimes prepend Node/OpenCLI logs."""
    text = str(raw or "").strip()
    if not text:
        return ""
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("(node:", "(Use `node", "#")):
            continue
        if line.lower().startswith("message:"):
            line = line.split(":", 1)[1].strip()
        if line.lower().startswith(("ok:", "error:", "code:", "help:", "exitcode:")):
            continue
        if line:
            lines.append(line)

    message = " ".join(lines)
    lowered = message.lower()
    if "no trains found" in lowered or "returned no data" in lowered:
        return "12306 当前日期未返回直达车次，需查中转换乘或调整日期。"
    if "train does not stop" in lowered:
        return "车次不经停目标站，无法补充经停信息。"
    return message[:500]


def _resolve_command(name: str) -> str | None:
    """Find commands installed by venvs or npm on Windows."""
    found = shutil.which(name)
    if found:
        found_path = Path(found)
        if found_path.suffix.lower() == ".cmd":
            powershell_wrapper = found_path.with_suffix(".ps1")
            if powershell_wrapper.exists():
                return str(powershell_wrapper)
        return found

    candidates = [
        Path.home() / ".agent-reach-venv" / "Scripts" / "{}.exe".format(name),
        Path.home() / "AppData" / "Roaming" / "npm" / "{}.cmd".format(name),
        Path.home() / "AppData" / "Roaming" / "npm" / "{}.exe".format(name),
        Path(__file__).parents[1] / ".venv" / "Scripts" / "{}.exe".format(name),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def run_json(command: list[str], timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Run a local JSON-producing command and return a stable result envelope."""
    resolved = _resolve_command(command[0])
    if not resolved:
        return {"ok": False, "error": "command_not_found", "message": command[0]}
    command = [resolved] + command[1:]
    if Path(resolved).suffix.lower() == ".ps1":
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if shell:
            command = [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                resolved,
            ] + command[1:]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "command_not_found", "message": command[0]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "message": " ".join(command)}

    data = _json_from_output(completed.stdout)
    if completed.returncode != 0:
        message = ""
        if isinstance(data, dict):
            message = str(data.get("message") or data.get("error") or "")
        message = message or completed.stderr.strip() or completed.stdout.strip()
        return {
            "ok": False,
            "error": "command_failed",
            "code": completed.returncode,
            "message": _clean_command_message(message),
        }
    if data is None:
        return {
            "ok": False,
            "error": "invalid_json",
            "message": _clean_command_message(completed.stdout),
        }
    if isinstance(data, dict) and data.get("ok") is False:
        return {
            "ok": False,
            "error": data.get("error") or "tool_error",
            "message": _clean_command_message(data.get("message") or data.get("help") or "tool returned an error"),
            "data": data,
        }
    return {"ok": True, "data": data}


def _records(data: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _slug(value: str) -> str:
    result = re.sub(r"[^0-9A-Za-z_-]+", "-", value).strip("-").lower()
    return result or "trip"


def _source(
    source_id: str,
    title: str,
    checked_at: str,
    source_type: str,
    url: str | None = None,
) -> dict[str, Any]:
    result = {
        "id": source_id,
        "title": title,
        "type": source_type,
        "checked_at": checked_at,
    }
    if url:
        result["url"] = url
    return result


def doctor() -> dict[str, Any]:
    if not _resolve_command("agent-reach"):
        return {"status": "unavailable", "message": "agent-reach is not on PATH"}
    result = run_json(["agent-reach", "doctor", "--json"], timeout=30)
    if result["ok"]:
        return {"status": "ok", "data": result["data"]}
    return {"status": "error", "message": result.get("message", "doctor failed")}


def search_xiaohongshu(query: str, limit: int = 8) -> dict[str, Any]:
    return run_json(
        [
            "opencli",
            "xiaohongshu",
            "search",
            query,
            "--limit",
            str(limit),
            "-f",
            "json",
            "--window",
            "background",
            "--site-session",
            "persistent",
        ]
    )


def search_reference(platform: str, query: str, limit: int = 8) -> dict[str, Any]:
    """Search a configured reference platform through OpenCLI."""
    if platform == "xiaohongshu":
        return search_xiaohongshu(query, limit)
    if platform not in REFERENCE_PLATFORMS:
        return {
            "ok": False,
            "error": "unsupported_platform",
            "message": "unsupported reference platform: {}".format(platform),
        }
    return run_json(
        [
            "opencli",
            platform,
            "search",
            query,
            "-f",
            "json",
            "--window",
            "background",
            "--site-session",
            "persistent",
        ]
    )


def read_xiaohongshu_note(url: str) -> dict[str, Any]:
    return run_json(
        [
            "opencli",
            "xiaohongshu",
            "note",
            url,
            "-f",
            "json",
            "--window",
            "background",
            "--site-session",
            "persistent",
        ]
    )


def list_stations(keyword: str) -> dict[str, Any]:
    return run_json(
        ["opencli", "12306", "stations", keyword, "--limit", "50", "-f", "json"],
        timeout=60,
    )


def choose_station(city: str, result: dict[str, Any]) -> dict[str, Any] | None:
    stations = _records(result.get("data"), "stations", "results", "data")
    preferred = PREFERRED_STATIONS.get(city, city)
    for station in stations:
        if station.get("name") == preferred:
            return station
    for station in stations:
        if station.get("name") == city:
            return station
    return stations[0] if stations else None


def search_trains(
    origin: str,
    destination: str,
    travel_date: str,
    limit: int,
) -> dict[str, Any]:
    return run_json(
        [
            "opencli",
            "12306",
            "trains",
            origin,
            destination,
            "--date",
            travel_date,
            "--limit",
            str(limit),
            "-f",
            "json",
        ],
        timeout=90,
    )


def train_price(
    train: dict[str, Any],
    origin: str,
    destination: str,
    travel_date: str,
) -> dict[str, Any]:
    return run_json(
        [
            "opencli",
            "12306",
            "price",
            str(train.get("train_no", "")),
            "--from",
            origin,
            "--to",
            destination,
            "--date",
            travel_date,
            "--seat-types",
            "OM9",
            "-f",
            "json",
        ],
        timeout=60,
    )


def train_route(
    train: dict[str, Any],
    origin: str,
    destination: str,
    travel_date: str,
) -> dict[str, Any]:
    return run_json(
        [
            "opencli",
            "12306",
            "train",
            str(train.get("train_no", "")),
            "--from",
            origin,
            "--to",
            destination,
            "--date",
            travel_date,
            "-f",
            "json",
        ],
        timeout=90,
    )


def _normalize_train(train: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "code",
        "train_no",
        "from_station",
        "to_station",
        "from_code",
        "to_code",
        "start_time",
        "arrive_time",
        "duration",
        "available",
        "business_seat",
        "first_seat",
        "second_seat",
        "soft_sleeper",
        "hard_sleeper",
        "hard_seat",
        "no_seat",
    )
    return {key: train.get(key) for key in fields if key in train}


def _recommended_train(trains: list[dict[str, Any]]) -> dict[str, Any] | None:
    def available(item: dict[str, Any]) -> bool:
        return item.get("available") is not False

    candidates = [item for item in trains if available(item)]
    return candidates[0] if candidates else (trains[0] if trains else None)


def _time_minutes(value: Any) -> int | None:
    if not value:
        return None
    try:
        hour, minute = str(value).split(":", 1)
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def _filter_trains(
    trains: list[dict[str, Any]],
    earliest: str | None = None,
    latest: str | None = None,
) -> list[dict[str, Any]]:
    earliest_min = _time_minutes(earliest)
    latest_min = _time_minutes(latest)
    filtered = []
    for train in trains:
        start = _time_minutes(train.get("start_time"))
        if start is None:
            continue
        if earliest_min is not None and start < earliest_min:
            continue
        if latest_min is not None and start > latest_min:
            continue
        filtered.append(train)
    return filtered


def collect_transport(
    origin: str,
    destination: str,
    start_date: str | None,
    return_date: str | None,
    limit: int,
    enrich_details: bool,
    depart_after: str | None = None,
    return_before: str | None = None,
) -> dict[str, Any]:
    if not start_date:
        return {
            "status": "needs_date",
            "message": "start_date is required for 12306 train and availability queries",
        }

    origin_result = list_stations(origin)
    destination_result = list_stations(destination)
    origin_station = choose_station(origin, origin_result)
    destination_station = choose_station(destination, destination_result)
    if not origin_station or not destination_station:
        return {
            "status": "error",
            "message": "station lookup failed",
            "origin_lookup": origin_result,
            "destination_lookup": destination_result,
        }

    actual_return_date = return_date or (
        date.fromisoformat(start_date) + timedelta(days=2)
    ).isoformat()

    legs = []
    for label, travel_date, from_station, to_station in (
        ("outbound", start_date, origin_station, destination_station),
        ("return", actual_return_date, destination_station, origin_station),
    ):
        trains_result = search_trains(
            str(from_station.get("code") or from_station.get("name")),
            str(to_station.get("code") or to_station.get("name")),
            travel_date,
            limit,
        )
        trains = [
            _normalize_train(item)
            for item in _records(trains_result.get("data"), "trains", "results", "data")
        ]
        if label == "outbound":
            trains = _filter_trains(trains, earliest=depart_after)
        else:
            trains = _filter_trains(trains, latest=return_before)
        leg = {
            "direction": label,
            "date": travel_date,
            "from": from_station,
            "to": to_station,
            "status": "ok" if trains_result["ok"] else "error",
            "trains": trains,
        }
        if not trains_result["ok"]:
            leg["message"] = trains_result.get("message", "train query failed")
        elif not trains:
            leg["message"] = "No direct trains returned for this date"
        recommended = _recommended_train(trains)
        if recommended and enrich_details:
            price_result = train_price(
                recommended,
                str(from_station.get("code") or from_station.get("name")),
                str(to_station.get("code") or to_station.get("name")),
                travel_date,
            )
            route_result = train_route(
                recommended,
                str(from_station.get("code") or from_station.get("name")),
                str(to_station.get("code") or to_station.get("name")),
                travel_date,
            )
            leg["recommended"] = recommended
            if price_result["ok"]:
                leg["recommended"]["prices"] = price_result["data"]
            else:
                leg["price_warning"] = price_result.get("message", "price query failed")
            if route_result["ok"]:
                leg["recommended"]["stops"] = route_result["data"]
            else:
                leg["route_warning"] = route_result.get("message", "route query failed")
        legs.append(leg)

    return {
        "status": "ok",
        "origin": origin_station,
        "destination": destination_station,
        "outbound": legs[0],
        "return": legs[1],
    }


def _http_json(url: str, timeout: int = WEATHER_TIMEOUT) -> Any:
    """GET a JSON URL with a hard timeout; any failure degrades to None."""
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "travel-guide-generator/1.0"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def geocode_city(city: str) -> dict[str, float] | None:
    """Resolve a city name to coordinates via the free open-meteo geocoder."""
    if not city:
        return None
    url = "{}?name={}&count=1&language=zh&format=json".format(
        GEOCODE_URL, urllib.parse.quote(city)
    )
    data = _http_json(url)
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        return None
    first = results[0]
    try:
        return {"lat": float(first["latitude"]), "lon": float(first["longitude"])}
    except (KeyError, TypeError, ValueError):
        return None


def fetch_daily_weather(
    city: str, start_date: str | None, end_date: str | None
) -> dict[str, Any]:
    """Fetch a free open-meteo daily forecast; degrade instead of failing.

    Returns {"status": "ok", "days": [...]} on success or
    {"status": "unavailable", "message": ...} when the API is unreachable —
    the caller renders a no-weather layout in that case.
    """
    if not city or not start_date or not end_date:
        return {"status": "unavailable", "message": "缺少出行日期或目的地，天气数据不可用"}
    location = geocode_city(city)
    if not location:
        return {"status": "unavailable", "message": "城市定位失败，天气数据不可用"}
    url = (
        "{}?latitude={}&longitude={}"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max&timezone=Asia/Shanghai"
        "&start_date={}&end_date={}".format(
            FORECAST_URL, location["lat"], location["lon"], start_date, end_date
        )
    )
    data = _http_json(url)
    daily = data.get("daily") if isinstance(data, dict) else None
    if not isinstance(daily, dict) or not daily.get("time"):
        return {"status": "unavailable", "message": "天气接口未返回数据，天气数据不可用"}

    def _series(key: str) -> list[Any]:
        values = daily.get(key)
        return values if isinstance(values, list) else []

    codes = _series("weather_code")
    highs = _series("temperature_2m_max")
    lows = _series("temperature_2m_min")
    probs = _series("precipitation_probability_max")
    days = []
    for index, day_date in enumerate(daily["time"]):

        def _at(series: list[Any]) -> Any:
            return series[index] if index < len(series) else None

        days.append(
            {
                "date": str(day_date),
                "weather_code": _at(codes),
                "temp_max": _at(highs),
                "temp_min": _at(lows),
                "precip_prob": _at(probs),
            }
        )
    return {"status": "ok", "source": "open-meteo", "days": days}


def collect_destination(
    origin: str,
    destination: str,
    days: int,
    nights: int,
    start_date: str | None,
    return_date: str | None,
    reference_platforms: list[str],
    reference_limit: int,
    reference_details: int,
    rail_limit: int,
    rail_details: bool,
    depart_after: str | None,
    return_before: str | None,
    checked_at: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    query = "{} {}天{}晚 攻略".format(origin, destination, days, nights)
    references: dict[str, dict[str, Any]] = {}
    warnings = []
    for platform in reference_platforms:
        platform_result = search_reference(platform, query, reference_limit)
        records = _records(platform_result.get("data"), "notes", "results", "data", "items")
        items = []
        for index, note in enumerate(records):
            note_url = note.get("url") or note.get("link")
            source_id = "{}-{}-{}".format(platform, _slug(destination), index + 1)
            sources.append(
                _source(
                    source_id,
                    note.get("title") or note.get("name") or "{} reference".format(platform),
                    checked_at,
                    "search",
                    note_url,
                )
            )
            item = {
                "rank": note.get("rank", index + 1),
                "title": note.get("title") or note.get("name") or note.get("text", ""),
                "author": note.get("author") or note.get("author_name", ""),
                "likes": note.get("likes") or note.get("score", ""),
                "published_at": note.get("published_at") or note.get("created_at", ""),
                "url": note_url,
                "source_id": source_id,
            }
            if platform == "xiaohongshu" and index < reference_details and note_url:
                detail = read_xiaohongshu_note(note_url)
                if detail["ok"] and detail.get("data") is not None:
                    item["detail"] = detail["data"]
            items.append(item)
        references[platform] = {
            "status": "ok" if platform_result["ok"] else "error",
            "notes": items,
        }
        if not platform_result["ok"]:
            warnings.append(
                "{} reference search unavailable: {}".format(
                    platform, platform_result.get("message", "unknown error")
                )
            )

    rail = collect_transport(
        origin,
        destination,
        start_date,
        return_date,
        rail_limit,
        rail_details,
        depart_after,
        return_before,
    )
    if rail.get("status") != "ok":
        warnings.append(rail.get("message", "12306 data unavailable"))
    if rail.get("status") == "ok":
        for direction in ("outbound", "return"):
            leg = rail.get(direction, {})
            if not leg.get("trains"):
                warnings.append(
                    "{} has no direct train result for the requested date".format(direction)
                )
    return {
        "destination": destination,
        "research_query": query,
        "references": references,
        "xiaohongshu": references.get("xiaohongshu", {"status": "skipped", "notes": []}),
        "transport": rail,
        "warnings": warnings,
    }


def collect(args: argparse.Namespace) -> dict[str, Any]:
    checked_at = date.today().isoformat()
    destinations = [item.strip() for item in args.destinations.split(",") if item.strip()]
    sources = [
        _source(
            "12306-official",
            "12306 train and station data",
            checked_at,
            "official",
            "https://www.12306.cn/",
        )
    ]
    effective_return_date = args.return_date
    if args.start_date and not effective_return_date:
        effective_return_date = (
            date.fromisoformat(args.start_date) + timedelta(days=args.days - 1)
        ).isoformat()

    results = []
    for destination in destinations:
        results.append(
            collect_destination(
                args.origin,
                destination,
                args.days,
                args.nights,
                args.start_date,
                effective_return_date,
                args.reference_platforms,
                args.reference_limit,
                args.reference_details,
                args.rail_limit,
                args.rail_details,
                args.depart_after,
                args.return_before,
                checked_at,
                sources,
            )
        )
    for destination, result in zip(destinations, results):
        result["weather"] = fetch_daily_weather(
            destination, args.start_date, effective_return_date
        )
    if jev_client.material_point_enabled():
        # 判断点默认关闭；开启后所有判断点共用一个进程级客户端（共享限次预算）。
        client = jev_client.shared_client()
        for result in results:
            summary, alerts = apply_jev_material_scoring(result, client)
            result["jev"] = summary
            if alerts:
                result["jev_alerts"] = alerts
    return {
        "research_version": "1.0",
        "collected_at": checked_at,
        "request": {
            "origin": args.origin,
            "destinations": destinations,
            "start_date": args.start_date,
            "return_date": effective_return_date,
            "days": args.days,
            "nights": args.nights,
            "travelers": args.travelers,
            "reference_platforms": args.reference_platforms,
            "transport_platform": "12306",
            "depart_after": args.depart_after,
            "return_before": args.return_before,
        },
        "agent_reach": doctor(),
        "sources": sources,
        "destinations": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect reference-platform and 12306 travel evidence")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destinations", required=True, help="Comma-separated city names")
    parser.add_argument("--start-date", help="YYYY-MM-DD; required for 12306 queries")
    parser.add_argument("--return-date", help="YYYY-MM-DD; defaults to day 3 for a 3-day trip")
    parser.add_argument(
        "--reference-platforms",
        default="xiaohongshu",
        help="Comma-separated reference platforms: xiaohongshu,twitter,reddit,bilibili",
    )
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--nights", type=int, default=2)
    parser.add_argument("--travelers", type=int, default=1)
    parser.add_argument("--depart-after", help="Only keep outbound trains departing at or after HH:MM")
    parser.add_argument("--return-before", help="Only keep return trains departing at or before HH:MM")
    parser.add_argument("--reference-limit", "--xhs-limit", type=int, default=8)
    parser.add_argument("--reference-details", "--xhs-details", type=int, default=3)
    parser.add_argument("--rail-limit", type=int, default=10)
    parser.add_argument("--rail-details", action="store_true", help="Add prices and station stops for recommended trains")
    parser.add_argument("--output", default="generated/trip-research.json")
    args = parser.parse_args()
    if args.days < 1 or args.nights < 0:
        parser.error("days must be positive and nights cannot be negative")
    if args.start_date:
        try:
            date.fromisoformat(args.start_date)
        except ValueError:
            parser.error("start-date must use YYYY-MM-DD")
    if args.return_date:
        try:
            date.fromisoformat(args.return_date)
        except ValueError:
            parser.error("return-date must use YYYY-MM-DD")
    args.reference_platforms = [
        platform.strip().lower()
        for platform in args.reference_platforms.split(",")
        if platform.strip()
    ]
    unknown_platforms = set(args.reference_platforms) - REFERENCE_PLATFORMS
    if unknown_platforms:
        parser.error("unsupported reference platforms: {}".format(", ".join(sorted(unknown_platforms))))
    for name in ("depart_after", "return_before"):
        value = getattr(args, name)
        if value is not None and _time_minutes(value) is None:
            parser.error("{} must use HH:MM".format(name.replace("_", "-")))

    result = collect(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(output), "destinations": len(result["destinations"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
