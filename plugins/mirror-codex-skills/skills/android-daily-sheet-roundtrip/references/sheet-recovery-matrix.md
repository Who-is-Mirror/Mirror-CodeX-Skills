# Daily sheet recovery matrix

Use this reference only in Stage B. The exact Stage A rows, facts hash, template layout, and current A:F read are the evidence boundary. Screenshots help the user review but never replace model reads.

## Decision model

Do not attempt to enumerate every possible human mistake. Map every difference into one of four outcomes:

| Outcome | Examples | Action |
|---|---|---|
| Preserve and record | Ordinary nonblank wording edits; valid added task or scope; evidence-backed status change | Keep it as `user_modifications[]`, subject to normal schema and customer guards. |
| Confirm intent | Complete task/scope deletion; valid task/scope insertion; task/scope move; identity/type change; duplicate blocks where the intended copy is unclear | Show the semantic identity and exact before/after. Do not mutate or prepare until the user confirms the intended result. |
| Deterministic consequence | Continuous renumbering after a confirmed deletion; deleting the confirmed task's blank physical rows; recalculating row height; removing stale overview/footer text explicitly approved with the deletion | Apply to the sheet first, record in `authorized_repairs[]`, then reread. These are authorized only by the associated confirmed intent. |
| Block and recover | Partial field clearing; orphan `怎么做的`/`结果`; missing/duplicated footer; unknown nonblank row; illegal status; malformed project/customer/type; formula or unsupported GMS loss; concurrent drift | Stop. Offer the smallest baseline restoration or complete-block decision. Never guess missing prose or identity. |

Unknown shapes always use **Block and recover**. A converter successfully skipping a malformed or blank row does not make the sheet valid.

## Error families and recovery

- **Single-cell typo or replacement:** preserve when the row remains structurally valid. Record semantic field, old value, and new value.
- **Partial clear within a task or scope:** restore only the exact missing Stage A values, or omit the whole semantic block after explicit confirmation. Never synthesize the missing text.
- **Complete task clear:** ask whether deletion was intended. If confirmed, delete its physical rows, renumber surviving tasks, and review today topic/current result/key points/dependencies/tomorrow plan for stale claims.
- **Complete scope clear:** ask whether deletion was intended. If confirmed, delete the whole scope plus exactly one adjacent separator while preserving one 50-height separator between remaining scopes.
- **Inserted task or scope:** accept only when every required field is valid. Recalculate numbering, separators, merges, wrapping, and row heights. Ambiguous project/App/GMS identity blocks.
- **Moved task or scope:** identify by semantic identity rather than row number. Require confirmation because movement can be accidental. Preserve content exactly after confirmation.
- **Duplicate paste:** detect duplicate scope identity or duplicate task content. Ask which copy to retain; never delete both or select one by position alone.
- **Blank row insertion:** an internal blank row inside a scope is invalid. Delete it only when surrounding anchors prove it contains no semantic block. Scope separators remain exactly one row.
- **Row deletion that shifts content:** realign by structural markers and semantic identity. Do not restore by absolute row number until anchors prove the intended block.
- **Numbering or label drift:** after task intent is settled, rewrite only the numbering prefix or canonical structural label. Preserve task text byte-for-byte.
- **Status loss or illegal status:** use the unchanged Stage A status only for a confirmed accidental clear; otherwise ask for one current allowed status.
- **Project/customer/type change:** run the customer guard. Registry conflicts and lossy GMS/App identity block; never update the registry from the sheet.
- **Formatting-only drift:** compare header fill, bold ranges, wrap ranges, D:F footer merges, row heights, and column widths with the template-derived layout. Restore formatting without changing values, and disclose it.
- **Formula, link, rich object, or content outside A:F:** stop. The A:F daily template does not preserve these losslessly.
- **Date tab rename, deletion, duplication, or wrong order:** use the date-tab gate. Recreating/replacing a same-date tab requires explicit regeneration authority.
- **Concurrent edit during repair:** every operation has exact before anchors. Any mismatch stops the transaction; refresh the audit instead of retrying stale repairs.

## Repair transaction

1. Save the exact current A:F read and baseline identity in the change audit. Do not create a report package yet.
2. Present all confirmation-required findings in one compact batch. Separate ordinary edits from suspected errors. Every complete task deletion must include a separate `dependent_consistency_review` covering the two overview fields and three footer fields, even when the deleted task name is not literally present. Store confirmed `finding_id` values in `schema=android-daily-sheet-change-decisions-v2` with exact `baseline_sha256`, `current_sha256`, and `accepted_finding_ids[]`; never accept blocker or repair IDs through this ledger. The audit recomputes these evidence hashes from normalized rows plus available document/sheet/date identity; a payload-declared snapshot hash is not authoritative. Any evidence drift invalidates the ledger.
3. Build a versioned v2 scalar repair plan only from confirmed choices and the current audit's `suggested_repairs[]`. Build a v3 row-repair plan from accepted complete-task deletions and exact `suggested_row_repairs[]` for missing/extra separators. Each write carries the exact audit hashes, finding ID, before/after values and reason; each row operation carries its action, exact finding, range, reason, and surrounding-anchor checks. Never type a baseline restoration value or separator row operation manually when the audit has not emitted it.
4. Apply physical row deletions from bottom to top, then restorations/insertions, deterministic renumbering, dependent summary/footer rewrites, and formatting repair.
5. Reread the complete sheet once with `run_stage_b_fast.mjs`. Because deterministic repair changes the snapshot hash, refresh the ledger binding from the verified repair transaction before the final audit; do not replay a stale ledger. Require `status=PASS`: no unresolved blocker or confirmation, no deterministic repair remaining, no internal blank hole, continuous task numbering, one separator between scopes, and no unknown nonblank rows. A confirmation ledger never suppresses blockers or unfinished repairs.
6. Convert only the final reread to facts. Run schema and customer guards, prepare the report, then compare Markdown and `report_view.json` with those facts.
7. Hand off the final screenshot and Markdown link with separate `用户修改` and `系统修复` lists. Include every accepted deletion/addition/move and every restoration, compaction, renumbering, consistency rewrite, or formatting repair.

No repair permission authorizes submission.

## Fast repair routing

- One isolated cell: use the guarded scalar repair; batching adds no benefit.
- Three or more contiguous cells: use a protected range write only after that capability exists and verifies the exact preimage and postimage. The current scalar repair remains the safe implemented path.
- Sparse edits: do not widen the write range merely for speed. For 20+ cells, a local semantic-block rerender is eligible only when every affected cell belongs to a complete confirmed block and all untouched cells have exact before anchors.
- Complete task/scope deletion: use guarded whole-row deletion after confirmation, from bottom to top. Never replace it with whole-sheet paste.
- Nonempty Stage B sheet: whole-sheet dual-MIME rerender is forbidden. It is allowed only for explicit same-date regeneration after the target tab is proven blank.
- No-repair run uses one online snapshot. A repaired run uses at most the initial snapshot and one final snapshot; audit and conversion reuse those files offline. Capture one final verification screenshot, with failure diagnostics on demand rather than redundant success screenshots.
