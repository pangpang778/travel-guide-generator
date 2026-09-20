---
name: travel-guide
description: Generate a sourced travel guide with selectable reference platforms and 12306 transport data.
---

# Travel Guide

Use this command as the explicit entry point for the travel-guide-generator
Skill.

Before any tool call, require:

- origin
- destination or candidate destinations
- departure date in YYYY-MM-DD
- trip length in days or nights

If any required field is missing, ask for only the missing fields and stop.
Never infer a date, place, or duration.

After the request is complete:

1. Select the requested reference platform, defaulting to XiaoHongShu.
2. Keep 12306 as the transport backend.
3. Run the internal research and comparison workflow.
4. Build the selected destination through the existing guide JSON pipeline.
5. Return the generated HTML first, followed by Markdown, ICS and GeoJSON.

Read-only only. Do not book, pay, publish, comment, like, or manage accounts.
