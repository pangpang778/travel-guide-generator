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
                "xhs_count": len(result.get("xiaohongshu", {}).get("notes", [])),
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
        notes = result.get("xiaohongshu", {}).get("notes", [])
        note_links = "".join(
            '<li><a href="{}" target="_blank" rel="noopener">{}</a> <span>{}</span></li>'.format(
                _text(note.get("url", "")),
                _text(note.get("title", "小红书笔记")),
                _text(note.get("likes", "")),
            )
            for note in notes[:5]
            if note.get("url")
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
                <div><b>{xhs_count}</b><span>小红书证据</span></div>
              </div>
              <section>
                <h3>12306 车次样本</h3>
                <pre>{transport}</pre>
              </section>
              <section>
                <h3>小红书参考</h3>
                <ul>{notes}</ul>
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
                xhs_count=summary["xhs_count"],
                transport=_text(
                    "{}\n\n返程:\n{}".format(
                        _leg_text(transport.get("outbound", {})),
                        _leg_text(transport.get("return", {})),
                    )
                ),
                notes=note_links or "<li>暂无小红书结果</li>",
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
