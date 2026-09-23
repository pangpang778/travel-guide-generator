#!/usr/bin/env python3
"""Render a self-contained comparison page from research_trip.py output."""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

try:
    from .research_trip import _clean_command_message
except ImportError:
    from research_trip import _clean_command_message


def _text(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _status_class(status: str) -> str:
    return "good" if status == "可售" else ("bad" if status == "不可售" else "warn")


def _minutes(value: Any) -> int | None:
    if not value:
        return None
    try:
        hours, minutes = str(value).split(":", 1)
        return int(hours) * 60 + int(minutes)
    except (TypeError, ValueError):
        return None


def _records(leg: dict[str, Any]) -> list[dict[str, Any]]:
    return leg.get("trains", []) if isinstance(leg.get("trains"), list) else []


def _reference_notes(result: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    references = result.get("references")
    if not isinstance(references, dict):
        references = {"xiaohongshu": result.get("xiaohongshu", {})}
    notes = []
    for platform, payload in references.items():
        for note in payload.get("notes", []) if isinstance(payload, dict) else []:
            notes.append((platform, note))
    return notes


def _detail_text(note: dict[str, Any]) -> str:
    detail = note.get("detail")
    if isinstance(detail, list):
        field_values = {
            item.get("field"): item.get("value")
            for item in detail
            if isinstance(item, dict) and item.get("field")
        }
        for key in ("content", "description", "desc", "text"):
            value = field_values.get(key)
            if isinstance(value, str) and value.strip():
                return value
        parts = []
        for item in detail:
            if isinstance(item, dict):
                parts.append(_detail_text({"detail": item}))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(part for part in parts if part)
    if isinstance(detail, dict):
        for key in ("content", "description", "desc", "text"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value
        for key in ("data", "note", "result"):
            if key in detail:
                return _detail_text({"detail": detail[key]})
    return ""


def _structured_reference_summary(content: str, titles: list[str]) -> str:
    """Turn source prose into compact decision-oriented evidence."""
    content = re.sub(r"\s+", " ", content).strip()
    if not content:
        topic_titles = "；".join(dict.fromkeys(titles[:4]))
        return (
            "本平台检索到相关内容，主题集中在：{}。\n"
            "当前只有标题证据，详细行程仍需在生成阶段核验正文。"
        ).format(topic_titles or "暂无可读主题")

    sentences = [
        part.strip()
        for part in re.split(r"(?<=[。！？!?])\s+|\n+", content)
        if len(part.strip()) >= 12
    ]
    groups = [
        ("路线共识", ("day", "路线", "上午", "下午", "晚上", "→")),
        ("预约提醒", ("预约", "抢票", "门票", "提前")),
        ("住宿线索", ("住宿", "酒店", "住在", "入住")),
        ("美食线索", ("美食", "餐厅", "小吃", "必点", "火锅")),
        ("避坑提醒", ("建议", "注意", "避坑", "交通", "不要", "提醒")),
    ]
    lines = []
    used = set()
    for label, keywords in groups:
        matches = []
        for sentence in sentences:
            if sentence in used:
                continue
            if any(keyword.lower() in sentence.lower() for keyword in keywords):
                matches.append(sentence)
                used.add(sentence)
            if len(matches) == 2:
                break
        if matches:
            lines.append("{}：{}".format(label, " ".join(matches)[:260]))
    if not lines:
        lines.append("内容摘要：{}".format(" ".join(sentences[:3])[:520]))
    return "\n".join(lines)[:900]


def _reference_summaries(result: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for platform, note in _reference_notes(result):
        grouped.setdefault(platform, []).append(note)

    summaries = []
    for platform, notes in grouped.items():
        titles = []
        first_url = ""
        first_source_id = ""
        content_parts = []
        for note in notes:
            content = re.sub(r"\s+", " ", _detail_text(note)).strip()
            if content:
                content_parts.append(content)
            if note.get("title"):
                titles.append(str(note["title"]).strip())
            first_url = first_url or note.get("url", "")
            first_source_id = first_source_id or note.get("source_id", "")

        summary = _structured_reference_summary(" ".join(content_parts), titles)

        summaries.append(
            {
                "platform": platform,
                "title": "{} 参考共识".format(platform),
                "summary": summary,
                "url": first_url,
                "source_id": first_source_id,
            }
        )
    return summaries


def _transport_conclusion(result: dict[str, Any]) -> dict[str, Any]:
    transport = result.get("transport", {})
    outbound = transport.get("outbound", {})
    return_leg = transport.get("return", {})
    outbound_train = outbound.get("recommended") or (_records(outbound)[:1] or [None])[0]
    return_train = return_leg.get("recommended") or (_records(return_leg)[:1] or [None])[0]
    if not outbound_train or not return_train:
        return {
            "headline": "交通不适合直接纳入三天两夜",
            "reason": "当前日期没有完整的直达去返车次样本，需要另查中转换乘或更换日期。",
            "detail": "12306 没有返回完整直达结果，不把缺失数据当成有票。",
        }

    outbound_minutes = _minutes(outbound_train.get("duration"))
    return_minutes = _minutes(return_train.get("duration"))
    total_minutes = (outbound_minutes or 0) + (return_minutes or 0)
    if total_minutes <= 360:
        headline = "优先候选：往返交通对三天两夜友好"
        reason = "往返首选车次样本的总乘车时间约 {} 小时，抵达后仍有完整游玩时间。".format(
            round(total_minutes / 60, 1)
        )
    elif total_minutes <= 720:
        headline = "可选：行程需要压缩首尾两天"
        reason = "往返首选车次样本的总乘车时间约 {} 小时，第一天和返程日不要排太满。".format(
            round(total_minutes / 60, 1)
        )
    else:
        headline = "时间成本高：不建议作为轻松三天两夜"
        reason = "往返首选车次样本的总乘车时间约 {} 小时，交通会吃掉大量行程。".format(
            round(total_minutes / 60, 1)
        )
    detail = "去程 {} {}-{}；返程 {} {}-{}。".format(
        outbound_train.get("code", ""),
        outbound_train.get("start_time", ""),
        outbound_train.get("arrive_time", ""),
        return_train.get("code", ""),
        return_train.get("start_time", ""),
        return_train.get("arrive_time", ""),
    )
    return {"headline": headline, "reason": reason, "detail": detail}


def _date_label(request: dict[str, Any], index: int) -> str:
    """Return YYYY-MM-DD for itinerary day index (0-based) from request.start_date."""
    try:
        day = date.fromisoformat(str(request.get("start_date") or "")) + timedelta(days=index)
    except (TypeError, ValueError):
        return ""
    return day.isoformat()


def _slot(time: str, title: str, note: str = "", pending: bool = False) -> dict[str, Any]:
    return {"time": time, "title": title, "note": note, "pending": pending}


def _itinerary_days(result: dict[str, Any], request: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive a day-by-day skeleton from transport + references.

    Hard data (trains) fills day 1 and the last day; everything the research
    did not actually verify stays pending (待核验) instead of being invented.
    """
    try:
        day_count = int(request.get("days") or 3)
    except (TypeError, ValueError):
        day_count = 3
    day_count = max(1, min(day_count, 7))

    transport = result.get("transport", {})
    outbound_leg = transport.get("outbound", {})
    return_leg = transport.get("return", {})
    outbound = outbound_leg.get("recommended") or (_records(outbound_leg)[:1] or [None])[0]
    return_train = return_leg.get("recommended") or (_records(return_leg)[:1] or [None])[0]

    clues = [
        item.get("summary", "")
        for item in _reference_summaries(result)
        if isinstance(item, dict)
    ]
    clue_text = "\n".join(part for part in clues if part)

    days = []
    for index in range(day_count):
        is_first = index == 0
        is_last = index == day_count - 1
        slots = []
        if is_first:
            slots.append(
                _slot(
                    "{}-{}".format(outbound.get("start_time", ""), outbound.get("arrive_time", ""))
                    if outbound
                    else "待核验",
                    "{} {} → {}".format(
                        outbound.get("code", ""), outbound.get("from_station", ""), outbound.get("to_station", "")
                    ) if outbound else "去程车次未查到直达，需中转或另选日期",
                    "出站后预留进城、寄存行李的时间，不把第一站卡得太死。" if outbound else "",
                    pending=not outbound,
                )
            )
            slots.append(
                _slot("抵达后", "进城 + 入住 / 寄存行李", "", pending=True)
            )
            slots.append(
                _slot("首日下午", "首日活动（按攻略线索安排）", clue_text[:260], pending=True)
            )
        elif is_last:
            slots.append(
                _slot("上午", "收尾活动 + 回酒店取行李", "", pending=True)
            )
            slots.append(
                _slot(
                    "{}-{}".format(return_train.get("start_time", ""), return_train.get("arrive_time", ""))
                    if return_train
                    else "待核验",
                    "返程 {} {} → {}".format(
                        return_train.get("code", ""), return_train.get("from_station", ""), return_train.get("to_station", "")
                    ) if return_train else "返程车次未查到直达，出发前重新筛选",
                    "至少预留 90 分钟到站。" if return_train else "",
                    pending=not return_train,
                )
            )
        else:
            slots.append(_slot("上午", "核心景点（按攻略线索安排）", clue_text[:260], pending=True))
            slots.append(_slot("下午", "第二片区或替换安排", clue_text[260:520], pending=True))
            slots.append(_slot("晚上", "住宿片区附近活动", "", pending=True))

        theme = "抵达日" if is_first else ("返程日" if is_last else "深度游玩")
        days.append(
            {
                "day": index + 1,
                "date": _date_label(request, index),
                "theme": theme,
                "slots": slots,
            }
        )
    return days


def build_bookings(result: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per booking item; only 12306-returned facts count as confirmed."""
    transport = result.get("transport", {})
    rows = []
    for label, leg in (("去程车票", transport.get("outbound", {})), ("返程车票", transport.get("return", {}))):
        train = leg.get("recommended") or (_records(leg)[:1] or [None])[0]
        if train:
            rows.append(
                {
                    "item": label,
                    "detail": "{} {}-{} {}".format(
                        train.get("code", ""),
                        train.get("start_time", ""),
                        train.get("arrive_time", ""),
                        train.get("second_seat", ""),
                    ).strip(),
                    "status": "可售" if train.get("available") else "需核验余票",
                }
            )
        else:
            rows.append(
                {
                    "item": label,
                    "detail": "12306 未返回直达车次，需查中转或另选日期",
                    "status": "待核验",
                }
            )

    clue_text = " ".join(
        item.get("summary", "") for item in _reference_summaries(result) if isinstance(item, dict)
    )
    if re.search(r"预约|门票|抢票", clue_text):
        rows.append(
            {
                "item": "景点预约 / 门票",
                "detail": "攻略线索提到预约或门票，按官方渠道逐项确认",
                "status": "待核验",
            }
        )
    if re.search(r"住宿|酒店|入住", clue_text):
        rows.append(
            {
                "item": "住宿",
                "detail": "攻略线索提到住宿选择，按行程片区比价确认",
                "status": "待核验",
            }
        )
    return rows


def _leg_text(leg: dict[str, Any]) -> str:
    trains = _records(leg)
    if not trains:
        return _clean_command_message(leg.get("message")) or "暂无直达车次"
    lines = []
    for train in trains[:3]:
        availability = "可售" if train.get("available") else "不可售"
        lines.append(
            "{} {}-{} {} {} {}".format(
                train.get("code", ""),
                train.get("start_time", ""),
                train.get("arrive_time", ""),
                train.get("duration", ""),
                availability,
                train.get("second_seat", ""),
            )
        )
    return "\n".join(lines)


def _duration(result: dict[str, Any]) -> int | None:
    outbound = result.get("transport", {}).get("outbound", {})
    return_leg = result.get("transport", {}).get("return", {})
    values = [_minutes(train.get("duration")) for train in _records(outbound)[:1]]
    values += [_minutes(train.get("duration")) for train in _records(return_leg)[:1]]
    valid = [value for value in values if value is not None]
    return sum(valid) if valid else None


def summarize(research: dict[str, Any]) -> list[dict[str, Any]]:
    request = research.get("request", {})
    rows = []
    for result in research.get("destinations", []):
        transport = result.get("transport", {})
        total = _duration(result)
        outbound = transport.get("outbound", {})
        return_leg = transport.get("return", {})
        rows.append(
            {
                "destination": result.get("destination", ""),
                "total_train_minutes": total,
                "outbound_count": len(_records(outbound)),
                "return_count": len(_records(return_leg)),
                "reference_count": len(_reference_notes(result)),
                "reference_summary": _reference_summaries(result),
                "transport_conclusion": _transport_conclusion(result),
                "warnings": result.get("warnings", []),
                "status": transport.get("status", "unknown"),
                "itinerary": _itinerary_days(result, request),
                "bookings": build_bookings(result),
                "guide_files": result.get("guide_files", []),
                "guide_html": result.get("guide_html", ""),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row["total_train_minutes"] is None,
            row["total_train_minutes"] or 999999,
        ),
    )


def render_html(research: dict[str, Any]) -> str:
    request = research.get("request", {})
    rows = summarize(research)
    cards = []
    panels = []
    tabs = []
    for index, summary in enumerate(rows):
        destination = summary["destination"]
        result = next(
            item
            for item in research.get("destinations", [])
            if item.get("destination") == destination
        )
        transport = result.get("transport", {})
        notes = _reference_notes(result)
        note_links = "".join(
            '<li><b>{}</b> <a href="{}" target="_blank" rel="noopener">{}</a> <span>{}</span></li>'.format(
                _text(platform),
                _text(note.get("url", "")),
                _text(note.get("title", "小红书笔记")),
                _text(note.get("likes", "")),
            )
            for platform, note in notes[:5]
            if note.get("url")
        )
        reference_summary = "".join(
                '<li><b>{}</b><div class="insight">{}</div></li>'.format(
                _text(item.get("platform", "")),
                _text(item.get("summary", "")),
            )
            for item in summary["reference_summary"][:5]
        )
        recommendation = "优先候选" if index == 0 else ""
        day_cards = "".join(
            """
            <article class="day-card">
              <header class="day-head">
                <span class="day-num">DAY {day}</span>
                <span class="day-date">{date}</span>
              </header>
              <h4>{theme} · {destination}</h4>
              <div class="slots">
                {slots}
              </div>
            </article>
            """.format(
                day=day.get("day"),
                date=_text(day.get("date") or "日期待定"),
                theme=_text(day.get("theme", "")),
                destination=_text(destination),
                slots="".join(
                    """
                    <div class="slot-row {pending}">
                      <span class="slot-time">{time}</span>
                      <div>
                        <b>{title}</b>
                        {note_block}
                      </div>
                    </div>
                    """.format(
                        pending="pending" if slot.get("pending") else "",
                        time=_text(slot.get("time", "")),
                        title=_text(slot.get("title", "")),
                        note_block=(
                            '<p class="slot-note">{}</p>'.format(_text(slot.get("note", "")))
                            if slot.get("note")
                            else ""
                        ),
                    )
                    for slot in day.get("slots", [])
                ),
            )
            for day in summary["itinerary"]
        )
        booking_rows = "".join(
            "<tr><td>{}</td><td>{}</td><td><span class=\"status {}\">{}</span></td></tr>".format(
                _text(row.get("item", "")),
                _text(row.get("detail", "")),
                _status_class(row.get("status", "")),
                _text(row.get("status", "")),
            )
            for row in summary["bookings"]
        )
        panels.append(
            """
            <div class="cand-panel" data-cand-panel="{index}" {active}>
              <div class="day-grid">{day_cards}</div>
              <h4 class="booking-head">预约区（每条独立核验）</h4>
              <table class="booking-table">
                <thead><tr><th>事项</th><th>当前依据</th><th>状态</th></tr></thead>
                <tbody>{booking_rows}</tbody>
              </table>
              {warning_block}
            </div>
            """.format(
                index=index,
                active="" if index == 0 else "hidden",
                day_cards=day_cards,
                booking_rows=booking_rows,
                warning_block=(
                    '<div class="warnings">{}</div>'.format(
                        "".join("<p>{}</p>".format(_text(item)) for item in summary["warnings"])
                    )
                    if summary["warnings"]
                    else ""
                ),
            )
        )
        tabs.append(
            '<button type="button" class="cand-tab {selected}" data-cand-tab="{index}" '
            'aria-selected="{selected_attr}">{name}</button>'.format(
                index=index,
                name=_text(destination),
                selected="selected" if index == 0 else "",
                selected_attr="true" if index == 0 else "false",
            )
        )
        duration = (
            "{} 小时".format(round(summary["total_train_minutes"] / 60, 1))
            if summary["total_train_minutes"] is not None
            else "暂无直达数据"
        )
        warnings = "".join("<li>{}</li>".format(_text(item)) for item in summary["warnings"])
        guide_link = (
            '<p class="guide-link"><a href="{}">打开 {} 的完整攻略</a></p>'.format(
                _text(summary["guide_html"]), _text(destination)
            )
            if summary.get("guide_html")
            else ""
        )
        cards.append(
            """
            <article class="candidate {recommended}">
              <header>
                <div>
                  <span class="eyebrow">候选 {rank}</span>
                  <h2>{destination}</h2>
                </div>
                <span class="badge">{recommendation}</span>
              </header>
              <div class="metrics">
                <div><b>{duration}</b><span>往返首班样本耗时</span></div>
                <div><b>{outbound}/{return_count}</b><span>去程/返程车次</span></div>
                <div><b>{xhs_count}</b><span>参考平台证据</span></div>
              </div>
              <section class="decision">
                <span class="decision-label">先看结论</span>
                <h3>{verdict}</h3>
                <p>{reason}</p>
                <p class="transport-conclusion">{transport_summary}</p>
              </section>
              {guide_link}
              <section>
                <h3>建议怎么走</h3>
                <pre>{transport}</pre>
              </section>
              <section>
                <h3>攻略内容总结</h3>
                <ul>{reference_summary}</ul>
              </section>
              <section class="raw-sources">
                <details>
                  <summary>查看原始来源（用于核验）</summary>
                  <ul>{notes}</ul>
                </details>
              </section>
              {warning_block}
            </article>
            """.format(
                recommended="recommended" if recommendation else "",
                rank=index + 1,
                destination=_text(destination),
                recommendation=_text(recommendation),
                duration=_text(duration),
                outbound=summary["outbound_count"],
                return_count=summary["return_count"],
                xhs_count=summary["reference_count"],
                verdict=_text(summary["transport_conclusion"]["headline"]),
                reason=_text(summary["transport_conclusion"]["reason"]),
                transport_summary=_text(summary["transport_conclusion"]["detail"]),
                guide_link=guide_link,
                transport=_text(
                    "{}\n\n返程:\n{}".format(
                        _leg_text(transport.get("outbound", {})),
                        _leg_text(transport.get("return", {})),
                    )
                ),
                notes=note_links or "<li>暂无原始来源</li>",
                reference_summary=reference_summary or "<li>暂无可总结的正文内容</li>",
                warning_block=(
                    "<section class=\"warnings\"><h3>需要补充</h3><ul>{}</ul></section>".format(warnings)
                    if warnings
                    else ""
                ),
            )
        )

    title = "{} → {} 候选对比".format(
        request.get("origin", ""),
        " / ".join(request.get("destinations", [])),
    )
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; --ink:#20252d; --muted:#66707c; --line:#dfe4e8; --paper:#f7f8f6; --accent:#176b87; --accent-soft:#e4f1f4; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--paper); color:var(--ink); font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; }}
    main {{ max-width:1120px; margin:0 auto; padding:32px 20px 56px; }}
    .hero {{ padding:24px 0 28px; border-bottom:1px solid var(--line); }}
    .eyebrow {{ color:var(--accent); font-size:12px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }}
    h1,h2,h3,p {{ margin-top:0; }}
    h1 {{ margin-bottom:8px; font-size:clamp(28px,5vw,48px); line-height:1.08; letter-spacing:0; }}
    .meta {{ color:var(--muted); }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:16px; margin-top:24px; }}
    .candidate {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:20px; box-shadow:0 4px 16px rgba(26,35,43,.04); }}
    .candidate.recommended {{ border:2px solid var(--accent); }}
    .decision {{ background:#edf7f4; border:1px solid #b6ded1; border-radius:6px; padding:14px; }}
    .decision-label {{ color:var(--accent); font-size:12px; font-weight:700; }}
    .decision h3 {{ margin:4px 0 6px; font-size:18px; }}
    .decision p {{ margin:4px 0; }}
    .transport-conclusion {{ color:#40515b; font-size:13px; }}
    .guide-link {{ margin:14px 0 0; font-weight:700; }}
    header {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }}
    header h2 {{ margin:4px 0 0; font-size:26px; }}
    .badge {{ min-height:24px; color:var(--accent); background:var(--accent-soft); border-radius:999px; padding:3px 9px; font-size:12px; font-weight:700; }}
    .metrics {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin:18px 0; }}
    .metrics div {{ padding:10px; background:#f2f5f5; border-radius:6px; }}
    .metrics b,.metrics span {{ display:block; }}
    .metrics b {{ font-size:18px; }}
    .metrics span {{ color:var(--muted); font-size:12px; }}
    section {{ border-top:1px solid var(--line); padding-top:14px; margin-top:14px; }}
    h3 {{ font-size:14px; margin-bottom:8px; }}
    pre {{ white-space:pre-wrap; margin:0; color:#39434e; font:13px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace; }}
    ul {{ padding-left:20px; margin:0; }}
    a {{ color:var(--accent); }}
    .warnings {{ color:#854d0e; background:#fff8e6; border:1px solid #f2d69b; padding:12px; border-radius:6px; }}
    .raw-sources {{ color:var(--muted); font-size:13px; }}
    .raw-sources summary {{ cursor:pointer; color:var(--accent); font-weight:600; }}
    .insight {{ white-space:pre-line; margin-top:5px; color:#39434e; }}
    .itinerary-block {{ margin-top:36px; padding-top:20px; border-top:2px solid var(--accent); }}
    .itinerary-block h2 {{ font-size:26px; }}
    .cand-tabs {{ display:flex; flex-wrap:wrap; gap:8px; margin:14px 0 18px; }}
    .cand-tab {{ padding:7px 16px; background:#fff; border:1px solid var(--line); border-radius:999px; color:var(--muted); font-size:14px; font-weight:600; }}
    .cand-tab.selected {{ color:#fff; background:var(--accent); border-color:var(--accent); }}
    .cand-panel[hidden] {{ display:none; }}
    .day-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }}
    .day-card {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .day-head {{ display:flex; justify-content:space-between; color:var(--accent); font-size:13px; font-weight:700; }}
    .day-card h4 {{ margin:6px 0 10px; font-size:16px; }}
    .slot-row {{ display:grid; grid-template-columns:118px 1fr; gap:10px; padding:9px 0; border-top:1px solid var(--line); }}
    .slot-time {{ color:var(--accent); font-size:13px; font-weight:600; }}
    .slot-row b {{ font-size:14px; }}
    .slot-note {{ margin:3px 0 0; color:var(--muted); font-size:12px; }}
    .slot-row.pending .slot-time, .slot-row.pending b {{ color:var(--muted); }}
    .slot-row.pending::after {{ content:"待核验"; color:#854d0e; background:#fff8e6; border:1px solid #f2d69b; border-radius:999px; padding:1px 8px; font-size:11px; align-self:start; }}
    .booking-head {{ margin:22px 0 10px; }}
    .booking-table {{ width:100%; border-collapse:collapse; background:#fff; }}
    .booking-table th, .booking-table td {{ padding:10px 12px; border:1px solid var(--line); text-align:left; font-size:14px; }}
    .booking-table th {{ background:#f2f5f5; font-size:12px; color:var(--muted); }}
    .status {{ border-radius:999px; padding:2px 9px; font-size:12px; font-weight:700; white-space:nowrap; }}
    .status.good {{ color:#1f6b3a; background:#e6f4ea; }}
    .status.warn {{ color:#854d0e; background:#fff8e6; }}
    .status.bad {{ color:#9d4c3d; background:#fdecea; }}
    footer {{ margin-top:24px; color:var(--muted); font-size:13px; }}
    @media (max-width:600px) {{ main {{ padding:20px 14px 40px; }} .metrics {{ grid-template-columns:1fr; }} }}
    /* Keep the comparison page on the same Apple-like visual system as the prototype. */
    :root {{
      --ink:#1d1d1f;
      --muted:#6e6e73;
      --line:#e0e0e0;
      --paper:#f5f5f7;
      --accent:#0066cc;
      --accent-soft:#eaf3ff;
      --dark:#1d1d1f;
    }}
    body {{ background:#fff; color:var(--ink); font:17px/1.47 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; -webkit-font-smoothing:antialiased; }}
    main {{ max-width:none; margin:0; padding:0 0 64px; }}
    .hero {{ margin:0; padding:64px max(24px, calc((100vw - 1080px) / 2)) 56px; color:#fff; background:var(--dark); border:0; }}
    .eyebrow {{ color:#70b9ff; letter-spacing:.03em; }}
    h1 {{ max-width:900px; margin-bottom:18px; color:#fff; font-size:clamp(38px,5.4vw,68px); font-weight:600; line-height:1.08; }}
    .meta {{ color:#b8b8bd; }}
    .grid, .itinerary-block, main > footer {{ max-width:1080px; width:100%; margin-left:auto; margin-right:auto; }}
    .grid {{ padding:0 24px; gap:16px; margin-top:36px; }}
    .candidate {{ background:#fff; border:1px solid var(--line); border-radius:11px; padding:24px; box-shadow:none; }}
    .candidate.recommended {{ border:2px solid var(--accent); }}
    .decision {{ background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .decision-label {{ color:var(--accent); }}
    .transport-conclusion {{ color:#424245; }}
    .metrics div {{ background:var(--paper); border-radius:8px; }}
    .badge {{ color:var(--accent); background:var(--accent-soft); }}
    .guide-link {{ color:var(--accent); }}
    section {{ border-color:var(--line); }}
    .itinerary-block {{ margin-top:56px; padding:32px 24px 0; border-top:2px solid var(--accent); }}
    .itinerary-block h2 {{ color:var(--ink); font-size:32px; font-weight:600; }}
    .cand-tab {{ color:var(--muted); background:#fff; border-color:var(--line); border-radius:999px; }}
    .cand-tab.selected {{ color:#fff; background:var(--accent); border-color:var(--accent); }}
    .day-card {{ background:#fff; border-color:var(--line); border-radius:11px; box-shadow:none; }}
    .day-head, .slot-time {{ color:var(--accent); }}
    .slot-row, .booking-table th, .booking-table td {{ border-color:var(--line); }}
    .booking-table th {{ background:var(--paper); }}
    .warnings {{ background:#fff8e6; border-color:#f2d69b; }}
    main > footer {{ padding:32px 24px 0; color:var(--muted); }}
    @media (max-width:600px) {{
      body {{ overflow-x:hidden; }}
      main {{ width:100%; max-width:100%; overflow:hidden; }}
      .hero {{ width:100%; max-width:100%; overflow:hidden; padding:48px 18px 42px; }}
      h1 {{ max-width:100%; font-size:38px; overflow-wrap:anywhere; word-break:break-word; }}
      .meta {{ display:block; max-width:100%; white-space:normal; overflow-wrap:anywhere; word-break:break-word; }}
      .grid, .itinerary-block, main > footer {{ width:100%; max-width:100%; padding-left:16px; padding-right:16px; }}
      .grid {{ display:block; min-width:0; margin-top:24px; }}
      .candidate {{ width:100%; max-width:100%; min-width:0; margin-bottom:16px; overflow:hidden; }}
      .candidate h2, .candidate h3, .candidate p, .candidate pre {{ overflow-wrap:anywhere; word-break:break-word; }}
      .itinerary-block {{ margin-top:40px; }}
    }}
  </style>
</head>
<body>
<main>
  <section class="hero">
    <span class="eyebrow">Travel Guide Generator · source comparison</span>
    <h1>{title}</h1>
    <p class="meta">{origin} · {start_date} 至 {return_date} · {days} 天 {nights} 夜 · {travelers} 人</p>
  </section>
  <div class="grid">{cards}</div>
  <section class="itinerary-block">
    <h2>动态行程（按候选切换）</h2>
    <p class="meta">行程骨架由 12306 车次与攻略线索自动生成；未核验项均标记待核验。</p>
    <div class="cand-tabs" role="tablist" aria-label="候选行程切换">{tabs}</div>
    {panels}
  </section>
  <footer>车次数据来自 12306 读取后端；平台内容来自小红书搜索。出发前请重新核验余票、营业时间和价格。</footer>
</main>
<script>
  (() => {{
    const tabs = [...document.querySelectorAll("[data-cand-tab]")];
    const panels = [...document.querySelectorAll("[data-cand-panel]")];
    const show = (index) => {{
      tabs.forEach((tab) => {{
        const active = Number(tab.dataset.candTab) === index;
        tab.classList.toggle("selected", active);
        tab.setAttribute("aria-selected", String(active));
      }});
      panels.forEach((panel) => {{
        panel.hidden = Number(panel.dataset.candPanel) !== index;
      }});
    }};
    tabs.forEach((tab) => tab.addEventListener("click", () => show(Number(tab.dataset.candTab))));
  }})();
</script>
</body>
</html>
""".format(
        title=_text(title),
        origin=_text(request.get("origin", "")),
        start_date=_text(request.get("start_date") or "待定"),
        return_date=_text(request.get("return_date") or "待定"),
        days=_text(request.get("days", "")),
        nights=_text(request.get("nights", "")),
        travelers=_text(request.get("travelers", "")),
        cards="".join(cards),
        tabs="".join(tabs),
        panels="".join(panels),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a candidate destination comparison page")
    parser.add_argument("research", help="research_trip.py output JSON")
    parser.add_argument("--output-base", help="Output path without extension")
    args = parser.parse_args()
    research_path = Path(args.research)
    research = json.loads(research_path.read_text(encoding="utf-8"))
    output_base = Path(args.output_base or research_path.with_name("comparison"))
    output_base.parent.mkdir(parents=True, exist_ok=True)
    output_base.with_suffix(".json").write_text(
        json.dumps({"request": research.get("request", {}), "candidates": summarize(research)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output_base.with_suffix(".html").write_text(render_html(research), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "files": [str(output_base.with_suffix(".json")), str(output_base.with_suffix(".html"))],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
