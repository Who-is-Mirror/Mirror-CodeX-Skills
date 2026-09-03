# Two-stage daily sheet workflow

This workflow deliberately creates an editable sheet before it creates any report package. Read the current installed intake skill and facts contract at runtime; plugin versions and interfaces may change.

## Shared invariants

- Resolve the newest installed directory under `$CODEX_HOME/plugins/cache/android-framework-codex-suite/android-framework-ops/` with version-aware ordering. Never pin a cached version.
- Keep the report date explicit and reject future dates.
- Run `scripts/check_browser_prerequisites.mjs` before either stage. Prefer the Codex-bundled Playwright runtime; use an already approved dedicated local runtime only when the bundle is unavailable. Attach only to the existing user-owned Edge CDP endpoint, default `http://127.0.0.1:9223`; an explicit endpoint may override it.
- Never install Playwright, Node, npm, `pngjs`, or change/restart Edge without explicit user approval. Never create task-local Playwright bridge modules, `node_modules` symlinks, or repeatedly probe guessed package entrypoints. A missing runtime or CDP endpoint is a prerequisite gate, not a reason to improvise setup.
- Playwright may operate DOM locators in the background. Never use OS-level or foreground mouse automation, never launch a replacement browser, and never call `browser.close()` on the attached browser. Disconnect only the Playwright transport.
- A changed/unknown plugin API, page structure, duplicate tab match, ambiguous identity, or unverifiable write is a hard stop.
- Do not modify the customer registry. An unregistered project is not learned from one draft; the customer guard remains authoritative in Stage B.
- `TVE1215M at 4782637` is one known Git-prose-shaped misclassification example. Treat `at`, `branch`, `commit`, `head`, and commit hashes as identity hazards, not as a general customer mapping or a correction to any real project.

## Stage A — authorized sessions to editable sheet

Stage A requires a fresh user request for one exact date and explicit session consent for this run. It must not call intake `--prepare`, `--upload`, or `--submit-latest`.

1. Resolve and verify browser prerequisites before session scanning or workbook creation:

   ```bash
   SKILL_ROOT="$CODEX_HOME/skills/android-daily-sheet-roundtrip"
   node "$SKILL_ROOT/scripts/check_browser_prerequisites.mjs" \
     --result <browser-prerequisites.json>
   ```

   The command validates Node execution, resolves a usable Playwright library export, and attaches read-only to Edge CDP. On `PASS`, set `CODEX_PLAYWRIGHT_MODULE` to the returned exact `playwright_module` for every later browser command in the stage. If automatic resolution reports `PLAYWRIGHT_MISSING`, use the desktop workspace-dependency locator once when available and rerun with `--playwright-module <exact-returned-path>`. Do not try `index.js`, `NODE_PATH`, bridge files, symlinks, or global CLI installation as fallbacks.

   If no existing runtime works, stop and ask the user to approve the reported local installation plan. Only after explicit approval may the agent install the Node library into the dedicated runtime prefix with browser download disabled, then rerun this preflight. If Node or npm itself is unavailable, report that separately and ask before any system-level installation. The optional slow compatibility renderer also needs `pngjs`; request separate approval before adding it when that route is explicitly chosen. On `CDP_UNAVAILABLE`, stop before session discovery and ask the user to authorize or perform the necessary Edge start/restart; never terminate their browser implicitly.

   After browser prerequisites pass, run the read-only enterprise WeChat login gate:

   ```bash
   SKILL_ROOT="$CODEX_HOME/skills/android-daily-sheet-roundtrip"
   node "$SKILL_ROOT/scripts/check_wecom_login.mjs" \
     --output-dir <login-evidence-dir>
   ```

   The script never clears cookies or browser storage. `PASS` authorizes the workflow to continue. `LOGIN_REQUIRED` provides one exact screenshot of `.wwLogin_qrcode_img`; show that image and stop the current Stage A attempt. If the user later asks to restart or confirms scanning, rerun the same command. It refreshes an expired login page and captures one new exact QR image only when login is still required. Do not take a viewport screenshot, full-page screenshot, or a second fallback screenshot.

2. Resolve and read the newest installed `android-daily-report-intake/SKILL.md` and its `daily-facts-contract.md`. This is the semantic content contract for Stage A even though Stage A stops before formal report preparation.

   Run the automatic plugin pass only as a candidate and drift diagnostic. Patch discovery is off unless the user explicitly requests it and a project hint is available.

   ```bash
   SKILL_ROOT="$CODEX_HOME/skills/android-daily-sheet-roundtrip"
   python3 "$SKILL_ROOT/scripts/draft_daily_from_sessions.py" \
     --profile <case-sensitive-config-profile> \
     --date YYYY-MM-DD \
     --session-consent \
     --output <candidates.json>
   # Add --patch-discovery only with explicit authorization.
   ```

   The script discovers the newest plugin and contract-checks `load_config`, `configure_report_session_consent`, `parse_sessions`, `daily_work_scopes`, `daily_work_items_from_scopes`, `items_by_project`, `overview_text`, `infer_report_project`, and `build_daily_facts`. It stores no raw session messages, raw commands, thread names, cwd, or source paths. Its output is non-authoritative and must never be passed to `facts_to_sheet_rows.py`. Use its unresolved entries and session counts to detect missing coverage or bad automatic inference; do not repair its prose and call that a report.

3. Read [incremental-session-review.md](incremental-session-review.md), build a complete active-and-archived task index, and run `scripts/session_review_cache.py plan` before reading task bodies. The task index must cross the report-date lower boundary. A same-date prior manifest may reuse only unchanged compact task evidence under an identical current daily-plugin fingerprint; a plugin rule/code change or task revision change forces rereading. Fresh consent remains mandatory and is never cached.

   Read changed tasks with the bounded parameters in that reference, assemble complete current task evidence, and reproduce the normal daily-intake semantic session review before writing any table data. Cache reuse skips only unchanged raw-body reads; it must not reuse final facts, scope classification, merge decisions, statuses, key points, dependencies, or tomorrow plans.

   - List Codex tasks in recency order with enough coverage to cross the report date's lower boundary. If the listing ends inside the date window, expand discovery or stop; never silently claim that all tasks were scanned.
   - Select every task whose completed activity intersects the exact report date. Read its actual relevant turns, paginating when the latest page does not contain the day's work. Task titles and retrieval summaries are untrusted routing hints only.
   - Record completion state from actual conclusions and verification evidence. Do not infer completion from a task title, a plan, or a command merely being attempted.
   - Merge repeated work on the same item across tasks, deduplicate identical method evidence, retain the latest result/status, and split independent work items or scopes exactly as `android-daily-report-intake` requires.
   - Exclude raw commands, paths, task names, prompt/control text, account/browser operations, and report-generation discussion unless that discussion itself is explicit reportable work. Never emit `[PATH]`, `<workspace_...>`, generic filler such as `修改或适配相关实现`, or source-path prose.
   - Build the complete `akbs-daily-work-facts-v4` object with `today_topic`, one consolidated `current_result` per scope, `work_items[].did/how/result/status`, `key_points`, `dependencies`, and evidence-backed `tomorrow_plan`. This facts file is the same normalized content that would be supplied to daily intake; do not create Markdown or `report_view` yet.
   - Keep a compact local review note containing candidate count, reviewed count, reused count, excluded count/reasons, plugin version/fingerprint, and facts hash. Do not store raw task bodies or credentials in that note.

   After producing current v4 facts, refresh the complete task index and run `scripts/session_review_cache.py finalize`. Any task revision or plugin-fingerprint drift requires replanning; do not continue to sheet creation. Store the assembled task evidence and manifest with the successful run, and reuse them only after the resulting sheet passes all Stage A audits.

   Run the current customer guard against the exact facts file. A registered-project conflict, missing direct customer, ambiguous scope, unresolved App name, or lossy GMS scope blocks sheet generation.

   ```bash
   GUARD="$CODEX_HOME/skills/android-daily-customer-guard/scripts/customer_chain_guard.py"
   python3 "$GUARD" validate-registry
   python3 "$GUARD" normalize-facts <facts.json> --write
   ```

4. Convert only the semantically reviewed and guarded v4 facts to deterministic A:F values.

   ```bash
   python3 "$SKILL_ROOT/scripts/facts_to_sheet_rows.py" \
     --input <facts.json> \
     --output <rows.json>
   ```

   Automatic candidate envelopes are rejected even when they contain no unresolved marker. GMS is rejected because the confirmed A:F template cannot preserve all current cycle fields losslessly. Keep `source_facts_sha256` from `rows.json` with the review note so the written sheet can be traced to the exact reviewed facts.

5. Reuse the exact Playwright module resolved by the prerequisite gate, then attach it to background Edge and find exactly one enterprise WeChat daily-sheet page. Do not reinstall or re-resolve it mid-stage. Set `CODEX_PLAYWRIGHT_MODULE` to the returned module entry; the Stage B reader and every write-side script honor the same setting.

   Ordinary runs reuse the managed workbook registered at `$CODEX_HOME/report/android-daily-managed-workbook.json`. If this state is absent, or the user explicitly asks to reset the managed workbook, create one workbook through the existing enterprise WeChat document home page and capture its new document ID:

   ```bash
   node "$SKILL_ROOT/scripts/create_daily_sheet_workbook.mjs" \
     --reference-document-id <old-reference-id>
   ```

   Register that exact ID and URL only after creation succeeds. Never create another workbook merely because a new report date is being added. Prepare one blank target date worksheet first:

   ```bash
   node "$SKILL_ROOT/scripts/prepare_daily_sheet_tab.mjs" \
     --document-id <managed-document-id> \
     --date YYYY-MM-DD \
     --max-tabs 5 \
     --output <tab-result.json>
   # Add --replace-existing only for an explicitly requested same-date regeneration.
   ```

   The preparation step accepts only date-named tabs, keeps at most five, deletes only the uniquely resolved oldest date when rotating, and audits final newest-to-oldest order. A fresh blank workbook's sole default tab may be renamed during bootstrap. Then render the exact blank target tab through the dual-MIME fast path. For a real Stage A draft, pass the generated rows JSON:

   ```bash
   node "$SKILL_ROOT/scripts/render_daily_sheet_fast.mjs" \
     --document-id <new-document-id> \
     --input <rows.json> \
     --title "Android 日报草稿" \
     --tab YYYY-MM-DD \
     --output-dir <evidence-dir>
   ```

6. Before writing, read the managed workbook through CDP and match the tab by exact `YYYY-MM-DD` title.

   - If the date tab already existed, stop unless the user explicitly requested **重新生成草稿** for that date.
   - Below five date tabs, add one with the bottom `+`; at five, delete the unique oldest date before adding the target.
   - Sort tabs newest to oldest and audit exact DOM order; require a unique visible target tab, name box and formula input.

7. Write only the `grid_data.values` A:F rectangle from `daily-sheet-rows-v1` through the fast renderer:

   - derive footer, separator, merge, wrap and bold ranges from the current A:F values while retaining the asset's fixed visual measurements;
   - snapshot the current system clipboard, then place both `text/plain` TSV and `text/html` table data on it;
   - activate the target CDP tab and send Playwright `Control+V`; if the workbook model is still blank, rewrite the same dual-MIME payload, reselect A1 and retry `Control+V` exactly once; only if that bounded retry is also blank may the renderer invoke the verified enterprise WeChat paste action once and dismiss its optional prompt; `text/plain` is the compatibility representation and `text/html` carries widths, fill, alignment, bold, wrap, line breaks and D:F footer merges;
   - calculate row heights with browser canvas metrics, then batch all row heights and six column widths through the current workbook dimension API; if the workbook model is still blank after the ordinary bounded retry and paste-action fallback, repeat the same ordinary dual-MIME A1 paste once after this dimension batch and reapply dimensions before auditing;
   - audit the generated dual-MIME payload before dispatch, then read values, styles, merges and dimensions from the workbook model after paste; restore the original clipboard before returning.

   The write-side clipboard item must contain both MIME types. Generated TSV/HTML structure is validated deterministically, while the workbook model is authoritative for the resulting values, styles, merges and dimensions. Any missing paste/dimension class when its route is needed, invalid payload, clipboard restore failure, or model mismatch is a hard stop. Do not silently drop to cell-by-cell writes. `scripts/render_daily_sheet.mjs` remains available only when the user explicitly accepts the slower compatibility route, such as for a nonblank workbook.

   Apply the visual contract from `assets/daily-sheet-template.json` and `references/sheet-template-contract.md`:

   - header fill `#8CDDFA`;
   - header, `今日概况`, numbered task headings, `重点说明`, `依赖 / 需协调`, and `明日计划` bold;
   - task/content columns wrap;
   - ordinary row height 52, enlarged by browser canvas text measurement for long content;
   - 50-height blank separator between scopes;
   - overview and all non-result rows have an empty F cell; only task result rows carry status.

   If the page cannot express a formatting action through stable DOM controls, copy that formatting from the confirmed date template through the same background CDP page. Do not approximate silently.

8. Require all tab-retention and fast-render audits to pass: at most five unique descending date tabs, generated TSV/HTML structure, normalized workbook values, required bold/wrap/header styles, exact D:F merges, calculated row heights, fixed column widths, and template forbidden-marker checks. Capture sheet-region PNGs using Playwright `page.screenshot`. Preserve the renderer-produced `stage-a-sheet-snapshot.json`; it binds exact values, styles, merges, dimensions, workbook ID, sheet ID and hashes without another browser read. A mismatch, wrong tab order, missing baseline snapshot, clipboard restore failure, or screenshot failure means Stage A is incomplete.

9. Report the reviewed facts path, candidate diagnostic path, review/reuse counts, task-evidence and manifest paths, rows path, exact tab, plugin version/fingerprint, facts hash, and screenshot path. Then say that the editable sheet is ready and **stop**. Do not produce Markdown, `report_view`, pending packages, or any intake command in this stage.

## Stage B — edited sheet to validated pending package

Enter Stage B only after a new explicit statement such as “修改完成” or “从表格反推日报”. That statement authorizes conversion and preparation, not submission.

Rerun `scripts/check_browser_prerequisites.mjs` before reading the edited sheet. A prior Stage A pass does not prove that the runtime or Edge CDP endpoint is still available. Apply the same approval gates for missing Playwright/Node/npm or unavailable CDP, and do not begin report preparation until the browser preflight passes.

Before conversion, locate the exact Stage A `stage-a-sheet-snapshot.json`, `rows.json`, reviewed facts and manifest for this workbook/date. If the baseline cannot be uniquely resolved, stop instead of guessing. Read [sheet-recovery-matrix.md](sheet-recovery-matrix.md). Capture the current workbook model once and reuse that immutable snapshot for audit, screenshot and conversion:

```bash
node "$SKILL_ROOT/scripts/run_stage_b_fast.mjs" \
  --baseline <exact-stage-a-sheet-snapshot.json> \
  --document-id <managed-document-id> \
  --sheet YYYY-MM-DD \
  --date YYYY-MM-DD \
  --output-dir <new-stage-b-run-dir>
```

This path performs exactly one online model read. It records A:F values, formulas/links/rich-text flags, G:Z occupancy, styles, merges and dimensions; selects A1 and captures one full visible table canvas; then runs audit and conversion offline. `BLOCKED`, `REVIEW_REQUIRED`, or `REPAIR_REQUIRED` produces no facts file and must stop before intake preparation.

The audit is diagnostic only. It must not mutate the sheet or decide whether an ambiguous difference was intentional. Classify differences as ordinary edits, complete semantic-block deletions, partial structural damage, additions, moves, duplicates, numbering drift, internal blank holes, identity changes, invalid statuses, unknown rows, or formatting drift:

- Ordinary nonblank value edits are accepted as user modifications and recorded with their semantic field, before value and after value.
- A fully blank task or scope block is only a deletion candidate. List its identity and require confirmation before omitting it; confirmation also authorizes deterministic task renumbering, but not unrelated prose changes.
- If required rows or fields are missing while sibling rows remain, treat it as partial structural damage and stop before `--prepare`. Show the exact missing semantic fields/cells and let the user choose between restoring only those baseline values or omitting the complete task/scope. Apply neither choice silently.
- When a task is removed, check whether `今日主题`, `当前结果`, `重点说明`, dependencies, or plans still describe it. Present any proposed consistency edit and require confirmation before changing that prose.

Keep a Stage B change log containing `user_modifications[]` and `authorized_repairs[]`. A repair entry records what was restored, removed, renumbered, or rewritten and why. Confirmation records use `schema=android-daily-sheet-change-decisions-v2` with `baseline_sha256`, `current_sha256`, and `accepted_finding_ids[]`. Both hashes must match the audit; stale ledgers fail closed. After deterministic repairs, create a new ledger bound to the verified final snapshot while retaining only the already authorized semantic findings. Apply every authorized repair to the enterprise WeChat sheet itself before conversion. This includes exact baseline restoration, deterministic renumbering such as changing the surviving task heading from `2.` to `1.`, and overview/footer consistency edits after a deletion. Never repair only the facts file, `report_view.json`, or Markdown.

Write confirmed repairs with a versioned plan and the guarded repair command:

```bash
node "$SKILL_ROOT/scripts/apply_daily_sheet_repairs.mjs" \
  --document-id <managed-document-id> \
  --sheet YYYY-MM-DD \
  --audit <sheet-change-audit.json> \
  --plan <repair-plan.json> \
  --output-dir <repair-evidence-dir> \
  --result <repair-result.json>
```

The plan uses `schema=android-daily-sheet-repair-plan-v2`, binds `baseline_sha256` and `current_sha256`, and uses entries shaped as `{cell,before,after,kind,reason,finding_id}`. Ordinary restoration, renumbering, and label repair must exactly match `audit.suggested_repairs[]`; a consistency rewrite is limited to cells named by the related consistency finding. The command prechecks every `before` value before changing anything, immediately rechecks each cell before its write, writes only A:F cells, verifies every `after` value, and rolls attempted cells back only while they still contain this transaction's expected postimage. A stale hash, invented/collateral write, precondition mismatch, concurrent change, rollback failure, write/readback mismatch, or screenshot failure blocks report generation.

For every confirmed complete task deletion, remove the contiguous fully blank task rows after the value repair:

```bash
node "$SKILL_ROOT/scripts/compact_daily_sheet_rows.mjs" \
  --document-id <managed-document-id> \
  --sheet YYYY-MM-DD \
  --audit <sheet-change-audit.json> \
  --plan <row-compaction-plan.json> \
  --output-dir <repair-evidence-dir> \
  --result <row-compaction-result.json>
```

The plan uses `schema=android-daily-sheet-row-repair-plan-v3`, binds the audit hashes, and contains one or more disjoint `operations[]` with `action=insert|delete`. Complete-task deletion operations name an accepted `missing_task` finding ID and its exact baseline `start_row`/`end_row`; separator repairs must be copied exactly from `audit.suggested_row_repairs[]`. Every operation includes nonblank `anchors_before[]`, `anchors_after[]`, and a reason. The validator sorts operations bottom-to-top and rejects unconfirmed, enlarged, overlapping, invented, or stale ranges. The command refuses a delete unless every A:F cell in its target range is blank and all before anchors match. It inserts/deletes entire rows through the workbook dimension API in the validated order so values, formatting, merges, and calculated heights below the edit move together, then verifies after anchors and captures a screenshot. If a later check, screenshot, or result write fails, it applies inverse row operations in reverse order, verifies the original anchors, and requires a fresh full audit because compensated row styles and merges may still differ. A failed compensation is `RECOVERY_REQUIRED` and must never be retried blindly. A blank separator is allowed only between different scopes according to the template; a deleted task must not leave blank rows inside a scope.

After all authorized repairs and row compaction, run `run_stage_b_fast.mjs` once in a new output directory. That post-repair snapshot, not the baseline, pre-repair read, repair plan, or an in-memory reconstruction, is the only input to report preparation. Audit the generated Markdown and `report_view.json` against its `daily-facts.json`, including surviving task order and numbering. If the sheet still displays `2.` after task 1 was deleted, or still contains the deleted task's blank row block, the report must not silently hide either defect; repair the sheet first or stop.

1. The fast Stage B command already reattached once, read the exact date tab, captured the verification PNG and converted the same PASS snapshot. Do not call the online converter or attach again. For offline diagnosis only, the converter accepts the saved snapshot:

   ```bash
   node "$SKILL_ROOT/scripts/daily_sheet_to_facts.mjs" \
     --input <stage-b-run-dir/sheet-snapshot.json> \
     --date YYYY-MM-DD \
     --output <facts.json>
   ```

   The saved snapshot is immutable evidence for this run. It rejects future dates, overview status, invalid/missing task fields, ambiguous identities, and GMS. Treat a missing snapshot, screenshot or hash mismatch as a failed Stage B read. Report both saved paths.

2. Dynamically resolve the current intake and read its current `SKILL.md` plus `daily-facts-contract.md` before invoking it.

   ```bash
   OPS_ROOT="$CODEX_HOME/plugins/cache/android-framework-codex-suite/android-framework-ops"
   PLUGIN_ROOT="$(find "$OPS_ROOT" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -n 1)"
   DAILY="$PLUGIN_ROOT/skills/android-daily-report-intake/scripts/android_daily_report_intake.py"
   GUARD="$CODEX_HOME/skills/android-daily-customer-guard/scripts/customer_chain_guard.py"
   ```

3. Run the customer guard and prepare. Never use one-step `--upload`.

   ```bash
   python3 "$GUARD" validate-registry
   python3 "$GUARD" normalize-facts <facts.json> --write
   python3 "$DAILY" --profile <case-sensitive-config-profile> \
     --date YYYY-MM-DD --daily-facts <facts.json> --prepare
   ```

   If the date already has an effective report, require the user's explicit revision request and pass the exact current run via `--replace-daily-run-id`; do not infer revision authority.

4. Capture the exact package path returned by prepare and validate that exact directory:

   ```bash
   python3 "$DAILY" --profile <case-sensitive-config-profile> --validate <exact-package-dir>
   python3 "$GUARD" validate-package <exact-package-dir>
   python3 "$GUARD" summary <exact-package-dir>
   python3 "$GUARD" validate-latest daily --date YYYY-MM-DD
   ```

   Confirm `validate-latest` points to the exact prepared package and verify `<exact-package-dir>/reports/daily.md` exists. In the final handoff, always provide a clickable absolute local Markdown link such as `[预览日报 Markdown](/absolute/package/path/reports/daily.md)`, together with the final verification screenshot, package path and compact project/customer-chain summary.

   The same handoff must contain two explicit lists derived from the Stage B change log:

   - `用户修改` — every accepted addition, text change, or confirmed deletion that affected the generated report;
   - `系统修复` — every user-authorized restoration, complete-block omission, task renumbering, or consistency rewrite performed before generation.

   Write `无` when a list is empty. Do not collapse the two lists, omit them because validation passed, or describe an unperformed suggestion as a repair. State that the user should use the linked Markdown and screenshot to judge the final result. A package-path-only response or an undisclosed repair is incomplete. Do not submit.

## Separate submission authorization

Only a later, explicit submit/upload instruction authorizes HTTP submission. Immediately before submitting, rerun `validate-latest daily --date YYYY-MM-DD`, confirm the same package and customer summary, then call current intake `--submit-latest` for that date. Never use `--upload` and never reuse Stage A/B authorization.
