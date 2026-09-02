---
name: android-daily-sheet-roundtrip
description: "Run Mirror's Android daily-report roundtrip in two gated stages: draft a dated enterprise WeChat sheet from authorized Codex sessions, then—only after the user confirms edits are complete—convert that sheet to facts and prepare a validated pending report. Do not use for weekly reports or direct submission."
---

# Android Daily Sheet Roundtrip

Keep the two stages strictly separate:

1. **Stage A — sessions to sheet draft.** Require fresh consent for the exact report date, dynamically use the newest installed `android-framework-ops` session/facts APIs, produce draft facts and A:F rows, then write and screenshot the dated sheet through the existing background Edge CDP session. Do not prepare a report or create Markdown, `report_view`, or pending material. Stop after the screenshot and wait for the user.
2. **Stage B — edited sheet to pending report.** Enter only when the user explicitly says the sheet edits are complete or asks to reverse-generate the daily report. Read the sheet through background CDP, capture a verification screenshot, convert it to v4 facts, run the current customer guard, and call the current daily intake with `--prepare`. Validate the exact package. Do not submit without a separate explicit authorization.

Read [references/two-stage-workflow.md](references/two-stage-workflow.md) before either stage. For any sheet creation or rendering, also read [references/sheet-template-contract.md](references/sheet-template-contract.md). Use the scripts and canonical fake-data asset there; never copy the current plugin's session merge, scope inference, facts normalization, or report rendering logic.

All sheet access uses bundled Playwright against the user-owned background Edge CDP endpoint (default `http://127.0.0.1:9223`). Do not use a bot/CLI sheet API, foreground mouse control, or `browser.close()`. Fail closed on plugin or enterprise WeChat internal API drift, ambiguous identity, GMS loss, unexpected sheet structure, duplicate date tabs, or failed screenshot verification.

When the user asks for a completely new workbook, run `scripts/create_daily_sheet_workbook.mjs` and then `scripts/render_daily_sheet_fast.mjs`. The fast renderer snapshots the existing clipboard, writes one `text/plain` TSV plus one `text/html` table, activates only the target CDP tab and sends one Playwright `Control+V`, applies all calculated row heights and column widths through one dimension batch, audits values/format/merges, restores the clipboard, and captures screenshots. Require the returned document ID to differ from any reference workbook. `assets/daily-sheet-template.json` is the local source of truth; screenshots are verification evidence only. Keep `scripts/render_daily_sheet.mjs` only as an explicitly chosen compatibility path for a nonblank workbook or after the user accepts a reported fast-path incompatibility; never silently fall back to slow coordinate-based formatting.
