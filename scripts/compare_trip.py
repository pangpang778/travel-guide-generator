#!/usr/bin/env python3
"""Render a self-contained comparison page from research_trip.py output."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any


def _text(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


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


def _leg_text(leg: dict[str, Any]) -> str:
    trains = _records(leg)
    if not trains:
        return leg.get("message", "暂无直达车次")
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
        duration = (
            "{} 小时".format(round(summary["total_train_minutes"] / 60, 1))
            if summary["total_train_minutes"] is not None
            else "暂无直达数据"
        )
        warnings = "".join("<li>{}</li>".format(_text(item)) for item in summary["warnings"])
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
    footer {{ margin-top:24px; color:var(--muted); font-size:13px; }}
    @media (max-width:600px) {{ main {{ padding:20px 14px 40px; }} .metrics {{ grid-template-columns:1fr; }} }}
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
  <footer>车次数据来自 12306 读取后端；平台内容来自小红书搜索。出发前请重新核验余票、营业时间和价格。</footer>
</main>
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
