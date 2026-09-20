#!/usr/bin/env python3
"""One-command real-source trip research and candidate comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .compare_trip import render_html, summarize
    from .research_trip import REFERENCE_PLATFORMS, _time_minutes, collect
except ImportError:
    from compare_trip import render_html, summarize
    from research_trip import REFERENCE_PLATFORMS, _time_minutes, collect


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect and compare real travel sources")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destinations", required=True, help="Comma-separated city names")
    parser.add_argument("--start-date", help="YYYY-MM-DD; required for 12306 queries")
    parser.add_argument("--return-date", help="YYYY-MM-DD; defaults to day N")
    parser.add_argument("--reference-platforms", default="xiaohongshu")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--nights", type=int, default=2)
    parser.add_argument("--travelers", type=int, default=1)
    parser.add_argument("--depart-after")
    parser.add_argument("--return-before")
    parser.add_argument("--reference-limit", "--xhs-limit", dest="reference_limit", type=int, default=8)
    parser.add_argument("--reference-details", "--xhs-details", dest="reference_details", type=int, default=0)
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
                "status": "ok",
                "files": [
                    str(research_path),
                    str(comparison_path),
                    str(comparison_json_path),
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
