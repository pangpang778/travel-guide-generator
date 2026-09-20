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
from datetime import date, timedelta
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT = 120
PREFERRED_STATIONS = {
    "宁波": "宁波",
    "苏州": "苏州",
    "重庆": "重庆北",
    "成都": "成都东",
    "福州": "福州",
}


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


def _resolve_command(name: str) -> str | None:
    """Find commands installed by venvs or npm on Windows."""
    found = shutil.which(name)
    if found:
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
            "message": message[-1000:],
        }
    if data is None:
        return {
            "ok": False,
            "error": "invalid_json",
            "message": completed.stdout.strip()[-1000:],
        }
    if isinstance(data, dict) and data.get("ok") is False:
        return {
            "ok": False,
            "error": data.get("error") or "tool_error",
            "message": data.get("message") or data.get("help") or "tool returned an error",
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


def collect_transport(
    origin: str,
    destination: str,
    start_date: str | None,
    return_date: str | None,
    limit: int,
    enrich_details: bool,
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


def collect_destination(
    origin: str,
    destination: str,
    days: int,
    nights: int,
    start_date: str | None,
    return_date: str | None,
    xhs_limit: int,
    xhs_details: int,
    rail_limit: int,
    rail_details: bool,
    checked_at: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    query = "{} {}天{}晚 攻略".format(origin, destination, days, nights)
    xhs_result = search_xiaohongshu(query, xhs_limit)
    notes = _records(xhs_result.get("data"), "notes", "results", "data")
    xhs_items = []
    for index, note in enumerate(notes):
        note_url = note.get("url")
        source_id = "xhs-{}-{}".format(_slug(destination), index + 1)
        sources.append(
            _source(
                source_id,
                note.get("title") or "XHS travel note",
                checked_at,
                "search",
                note_url,
            )
        )
        xhs_items.append(
            {
                "rank": note.get("rank", index + 1),
                "title": note.get("title", ""),
                "author": note.get("author", ""),
                "likes": note.get("likes", ""),
                "published_at": note.get("published_at", ""),
                "url": note_url,
                "source_id": source_id,
            }
        )
        if index < xhs_details and note_url:
            detail = read_xiaohongshu_note(note_url)
            if detail["ok"] and isinstance(detail.get("data"), dict):
                xhs_items[-1]["detail"] = detail["data"]

    rail = collect_transport(
        origin,
        destination,
        start_date,
        return_date,
        rail_limit,
        rail_details,
    )
    warnings = []
    if not xhs_result["ok"]:
        warnings.append("XiaoHongShu search unavailable: {}".format(xhs_result.get("message", "unknown error")))
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
        "xiaohongshu": {
            "status": "ok" if xhs_result["ok"] else "error",
            "notes": xhs_items,
        },
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
                args.xhs_limit,
                args.xhs_details,
                args.rail_limit,
                args.rail_details,
                checked_at,
                sources,
            )
        )
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
        },
        "agent_reach": doctor(),
        "sources": sources,
        "destinations": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Agent Reach/XHS and 12306 travel evidence")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destinations", required=True, help="Comma-separated city names")
    parser.add_argument("--start-date", help="YYYY-MM-DD; required for 12306 queries")
    parser.add_argument("--return-date", help="YYYY-MM-DD; defaults to day 3 for a 3-day trip")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--nights", type=int, default=2)
    parser.add_argument("--travelers", type=int, default=1)
    parser.add_argument("--xhs-limit", type=int, default=8)
    parser.add_argument("--xhs-details", type=int, default=0, help="Read details for the first N XHS results")
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

    result = collect(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(output), "destinations": len(result["destinations"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
