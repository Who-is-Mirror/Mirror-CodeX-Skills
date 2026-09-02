# Daily sheet template contract

The canonical editable-sheet template is `assets/daily-sheet-template.json`. It contains fake demonstration values and the exact reusable visual contract. Never use the fake values as report facts.

## Structure

- Six columns: `项目 / 客户`, `类型`, `任务`, `分项`, `内容`, `状态`.
- Each scope begins with `今日概况`, followed by one `今日主题` row and one `当前结果` row.
- Each numbered task uses exactly three rows: `做了什么`, `怎么做的`, `结果`. Only the `结果` row carries status.
- `重点说明`, `依赖 / 需协调`, and `明日计划` place content in one D:F merged cell.
- A 50-pixel blank separator row separates scopes.
- `Other` occupies column A; its concrete work name occupies column B.

## Visual rules

- Column widths and ranges come only from the asset. Do not infer them from screenshots.
- Header fill is `#8CDDFA` and the header is centered.
- Body cells are left aligned.
- B, C, E and merged footer content wrap. This preserves the user's explicit B-column wrap adjustment while keeping task/content columns readable.
- Task titles remain regular weight. The header B:F, `今日概况`, all field labels, and all three footer labels—including `依赖 / 需协调`—are bold.
- Row heights are calculated from the actual browser canvas font metrics, configured column widths, explicit newlines, merged D:F width, and fixed vertical padding. Enforce minimum, maximum, separator height and rounding from the asset.
- Screenshots are verification evidence, not the source of formatting decisions.

## Fast clipboard rendering

- `text/plain` is a six-column TSV compatibility representation. Embedded line breaks and quotes must use spreadsheet-compatible quoting.
- `text/html` is the formatting representation. It must carry exact column widths, header fill, alignment, bold, wrap, explicit `<br>` line breaks, and `colspan="3"` for every D:F footer merge.
- Derive footer, separator, scope and field rows from the current values; do not reuse the fake asset's row numbers for a differently sized real report.
- The renderer must snapshot and restore the clipboard. Clipboard restoration failure is not a successful Stage A completion.
- Activate only the target CDP tab and dispatch one Playwright `Control+V`; never drive the user's system pointer or foreground the Edge window at the OS level.
- HTML row heights are advisory because enterprise WeChat does not preserve them reliably during paste. Apply calculated row heights and fixed column widths through one verified workbook-dimension batch after the paste.
- Validate the generated TSV/HTML structure before dispatch, then verify resulting values, styles, merges and dimensions from the workbook model. Avoid screenshot pixel analysis and per-row context-menu coordinates.

## Safety

- A generated workbook must have a new document ID when the user requests a new workbook. The old workbook may be read as a reference but must not be renamed, cleared, or used as the output.
- Before writing, reject any template asset containing a `forbidden_real_markers` token.
- After writing, read every A:F cell back, compare normalized values, verify required merges/wrap state where the UI exposes stable state, and capture screenshots.
- Attach only to the existing background Edge CDP session and disconnect the transport without closing Edge.
