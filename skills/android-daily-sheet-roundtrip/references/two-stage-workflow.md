# Two-stage daily sheet workflow

This workflow deliberately creates an editable sheet before it creates any report package. Read the current installed intake skill and facts contract at runtime; plugin versions and interfaces may change.

## Shared invariants

- Resolve the newest installed directory under `$CODEX_HOME/plugins/cache/android-framework-codex-suite/android-framework-ops/` with version-aware ordering. Never pin a cached version.
- Keep the report date explicit and reject future dates.
- Use only the bundled Playwright runtime attached to the existing user-owned Edge CDP endpoint. Default to `http://127.0.0.1:9223`; an explicit endpoint may override it.
- Playwright may operate DOM locators in the background. Never use OS-level or foreground mouse automation, never launch a replacement browser, and never call `browser.close()` on the attached browser. Disconnect only the Playwright transport.
- A changed/unknown plugin API, page structure, duplicate tab match, ambiguous identity, or unverifiable write is a hard stop.
- Do not modify the customer registry. An unregistered project is not learned from one draft; the customer guard remains authoritative in Stage B.
- `TVE1215M at 4782637` is one known Git-prose-shaped misclassification example. Treat `at`, `branch`, `commit`, `head`, and commit hashes as identity hazards, not as a general customer mapping or a correction to any real project.

## Stage A — authorized sessions to editable sheet

Stage A requires a fresh user request for one exact date and explicit session consent for this run. It must not call intake `--prepare`, `--upload`, or `--submit-latest`.

1. Generate the plugin-derived draft. Patch discovery is off unless the user explicitly requests it and a project hint is available.

   ```bash
   SKILL_ROOT="$CODEX_HOME/skills/android-daily-sheet-roundtrip"
   python3 "$SKILL_ROOT/scripts/draft_daily_from_sessions.py" \
     --profile <case-sensitive-config-profile> \
     --date YYYY-MM-DD \
     --session-consent \
     --output <draft.json>
   # Add --patch-discovery only with explicit authorization.
   ```

   The script discovers the newest plugin and contract-checks `load_config`, `configure_report_session_consent`, `parse_sessions`, `daily_work_scopes`, `daily_work_items_from_scopes`, `items_by_project`, `overview_text`, `infer_report_project`, and `build_daily_facts`. It stores no raw session messages, raw commands, thread names, cwd, or source paths. If `unresolved` is non-empty, stop before row generation or sheet writing and ask only for those facts.

2. Convert the clean draft to deterministic A:F values.

   ```bash
   python3 "$SKILL_ROOT/scripts/facts_to_sheet_rows.py" \
     --input <draft.json> \
     --output <rows.json>
   ```

   GMS is rejected because the confirmed A:F template cannot preserve all current cycle fields losslessly.

3. Call the Codex desktop `load_workspace_dependencies` capability to resolve the bundled Playwright runtime, then attach it to background Edge and find exactly one enterprise WeChat daily-sheet page. Do not install another Playwright. Set `CODEX_PLAYWRIGHT_MODULE` to the returned bundled module entry when it is not resolvable from the current Node working directory; the Stage B reader honors the same setting.

   If the user explicitly asks for a new workbook, create it through the existing enterprise WeChat document home page and capture the new document ID before rendering:

   ```bash
   node "$SKILL_ROOT/scripts/create_daily_sheet_workbook.mjs" \
     --reference-document-id <old-reference-id>
   ```

   Then render the new blank workbook through the dual-MIME fast path. For a template verification run, omit `--input` to use the canonical fake-data asset. For a real Stage A draft, pass the generated rows JSON:

   ```bash
   node "$SKILL_ROOT/scripts/render_daily_sheet_fast.mjs" \
     --document-id <new-document-id> \
     --input <rows.json> \
     --title <workbook-title> \
     --tab YYYY-MM-DD \
     --output-dir <evidence-dir>
   ```

4. Before writing, read the workbook through CDP and match the tab by exact `YYYY-MM-DD` title.

   - If the date tab exists, stop unless the user explicitly requested **重新生成草稿** for that date.
   - If it does not exist, duplicate the nearest confirmed date template, rename it to the target date, and position date tabs in descending order.
   - Require a unique visible tab, a visible name box, and a visible formula input. Ambiguity or missing controls is fatal.

5. Write only the `grid_data.values` A:F rectangle from `daily-sheet-rows-v1` through the fast renderer:

   - derive footer, separator, merge, wrap and bold ranges from the current A:F values while retaining the asset's fixed visual measurements;
   - snapshot the current system clipboard, then place both `text/plain` TSV and `text/html` table data on it;
   - activate the target CDP tab and send one Playwright `Control+V`; `text/plain` is the compatibility representation and `text/html` carries widths, fill, alignment, bold, wrap, line breaks and D:F footer merges;
   - calculate row heights with browser canvas metrics, then batch all row heights and six column widths through the current workbook dimension API;
   - audit the generated dual-MIME payload before dispatch, then read values, styles, merges and dimensions from the workbook model after paste; restore the original clipboard before returning.

   The write-side clipboard item must contain both MIME types. Generated TSV/HTML structure is validated deterministically, while the workbook model is authoritative for the resulting values, styles, merges and dimensions. Any missing dimension class, invalid payload, clipboard restore failure, or model mismatch is a hard stop. Do not silently drop to cell-by-cell writes. `scripts/render_daily_sheet.mjs` remains available only when the user explicitly accepts the slower compatibility route, such as for a nonblank workbook.

   Apply the visual contract from `assets/daily-sheet-template.json` and `references/sheet-template-contract.md`:

   - header fill `#8CDDFA`;
   - header, `今日概况`, numbered task headings, `重点说明`, `依赖 / 需协调`, and `明日计划` bold;
   - task/content columns wrap;
   - ordinary row height 52, enlarged by browser canvas text measurement for long content;
   - 50-height blank separator between scopes;
   - overview and all non-result rows have an empty F cell; only task result rows carry status.

   If the page cannot express a formatting action through stable DOM controls, copy that formatting from the confirmed date template through the same background CDP page. Do not approximate silently.

6. Require all fast-render audits to pass: generated TSV/HTML structure, normalized workbook values, required bold/wrap/header styles, exact D:F merges, calculated row heights, fixed column widths, and forbidden-marker checks. Capture sheet-region PNGs using Playwright `page.screenshot`. A mismatch, wrong tab order, clipboard restore failure, or screenshot failure means Stage A is incomplete.

7. Report the draft path, rows path, exact tab, plugin version, raw/unique session counts, and screenshot path. Then say that the editable sheet is ready and **stop**. Do not produce Markdown, `report_view`, pending packages, or any intake command in this stage.

## Stage B — edited sheet to validated pending package

Enter Stage B only after a new explicit statement such as “修改完成” or “从表格反推日报”. That statement authorizes conversion and preparation, not submission.

1. Reattach to the existing background CDP session and read the exact date tab. The converter defaults to `http://127.0.0.1:9223` when `--cdp` is omitted:

   ```bash
   node "$SKILL_ROOT/scripts/daily_sheet_to_facts.mjs" \
     --sheet YYYY-MM-DD \
     --date YYYY-MM-DD \
     --output <facts.json>
   ```

   Use `--input <cells.json>` only for offline tests. CDP mode is read-only and disconnects without closing Edge. It rejects future dates, overview status, invalid/missing task fields, ambiguous identities, and GMS.

   After the read completes, reattach through the same background CDP endpoint, select the exact date tab, and capture a full-page or sheet-region PNG with Playwright `page.screenshot`. Treat a missing tab or screenshot failure as a failed Stage B read. Report the screenshot path together with the generated facts path.

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

   Confirm `validate-latest` points to the exact prepared package. Report the package and compact project/customer-chain summary. Do not submit.

## Separate submission authorization

Only a later, explicit submit/upload instruction authorizes HTTP submission. Immediately before submitting, rerun `validate-latest daily --date YYYY-MM-DD`, confirm the same package and customer summary, then call current intake `--submit-latest` for that date. Never use `--upload` and never reuse Stage A/B authorization.
