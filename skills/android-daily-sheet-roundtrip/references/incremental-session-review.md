# Incremental and compact session review

Use this only in Stage A after the login gate passes. It accelerates evidence collection; it never replaces the current `android-daily-report-intake` rules or its final semantic merge.

## Non-negotiable authority boundary

- Resolve and read the newest daily intake skill and facts contract on every run.
- `scripts/session_review_cache.py` hashes the entire current plugin tree (excluding runtime `__pycache__/.pyc`) and separately records the daily skill, entrypoint, facts contract, and candidate API-chain modules. Any hash or plugin-version change forces a full task review.
- Cached task evidence is only a privacy-minimized substitute for rereading unchanged task bodies. Never pass it to `facts_to_sheet_rows.py`, never treat it as final facts, and never skip the current plugin's scope, merge, status, attention-field, customer, or GMS rules.
- Fresh session consent is still required for every run. Cache reuse does not reuse consent.

## Build the complete task index

After login passes, list non-pinned tasks with `list_threads(limit=50)` and archived tasks with `list_archived_threads(limit=50)`. Pinned tasks are already returned in full by `list_threads`. Expand archived pagination only while the page can still intersect the report date.

Create `task-index.json` with schema `android-daily-task-index-v1`:

```json
{
  "schema": "android-daily-task-index-v1",
  "report_date": "2026-09-02",
  "listing_crossed_lower_boundary": true,
  "archived_checked": true,
  "tasks": [
    {
      "thread_id": "thread-id",
      "host_id": "local",
      "kind": "codex",
      "updated_at": 1788332324,
      "status": "active",
      "latest_turn_id": "latest-known-turn-id"
    }
  ]
}
```

Include every visible Codex task whose actual activity can intersect the date. Titles and summaries are routing hints only and must not enter the index or evidence. Set the two coverage booleans to true only after the listing crosses the lower date boundary and archived tasks were checked.

Run `plan` with the latest successful same-date manifest when one exists:

```bash
python3 "$SKILL_ROOT/scripts/session_review_cache.py" plan \
  --date YYYY-MM-DD \
  --index <task-index.json> \
  --previous-manifest <previous-review-manifest.json> \
  --output <review-plan.json>
```

Omit `--previous-manifest` on the first run. Review every `review_thread_ids` task. Reuse only `reuse_thread_ids` from the matching previous evidence file.

## Compact reading parameters

For each changed task, call `read_thread` with:

- `turnLimit: 10`
- `includeOutputs: false`
- `maxOutputCharsPerItem: 3000`

Parse the returned JSON before displaying it. Retain only actual `userMessage`, concise `agentMessage` conclusions, turn status, turn IDs, and timestamps. Do not print or retain command executions, tool outputs, raw paths, task titles, retrieval summaries, credentials, or control prompts. Paginate only when `hasMore` is true and the oldest returned turn does not cross the report-date lower boundary.

If a task is active, capture its current latest turn ID and status. Refresh the complete task index after reading. A changed revision between the planning index and refreshed index requires replanning and rereading that task; do not reuse stale evidence.

## Write compact task evidence

Create a delta evidence file for exactly `review_thread_ids`. Each included task stores one or more session-field groups using the same four authorized session fields consumed by the daily plugin plus explicit outcome/status evidence:

```json
{
  "schema": "android-daily-task-evidence-v1",
  "report_date": "2026-09-02",
  "plugin_fingerprint": {},
  "tasks": [
    {
      "thread_id": "thread-id",
      "host_id": "local",
      "revision": {
        "updated_at": 1788332324,
        "status": "completed",
        "latest_turn_id": "turn-id"
      },
      "compact_evidence": {
        "include": true,
        "session_fields": [
          {
            "work_summary": ["完成了什么"],
            "command_summary": ["如何处理与验证"],
            "project_hint": "TVA10A0R",
            "work_scope_hint": "Patch",
            "outcome_summary": ["最新验证结果"],
            "status_evidence": ["已完成"]
          }
        ],
        "key_points": [],
        "dependencies": [],
        "tomorrow_plan_evidence": [],
        "source_turn_ids": ["turn-id"]
      }
    }
  ]
}
```

Copy `plugin_fingerprint` from the plan. Status values remain exactly `已完成`, `处理中`, `待验证`, or `阻塞`. For a non-reportable task, set `include=false` with a concise `exclusion_reason`; do not omit the task from evidence coverage.

Assemble current evidence:

```bash
python3 "$SKILL_ROOT/scripts/session_review_cache.py" assemble \
  --plan <review-plan.json> \
  --index <refreshed-task-index.json> \
  --updated-evidence <task-evidence-delta.json> \
  --previous-evidence <previous-task-evidence.json> \
  --output <task-evidence.json>
```

Omit `--previous-evidence` when no tasks are reusable.

## Re-run current plugin semantics and finalize

Use the complete assembled evidence only to avoid rereading unchanged bodies. Reperform the current daily plugin's semantic scope inference, split/merge, latest-result selection, attention-field handling, tomorrow-plan rules, and v4 facts construction across all included evidence. Run the customer guard and convert the resulting current facts as usual.

Refresh the task index once more immediately before finalization. Then create the manifest:

```bash
python3 "$SKILL_ROOT/scripts/session_review_cache.py" finalize \
  --date YYYY-MM-DD \
  --index <final-task-index.json> \
  --evidence <task-evidence.json> \
  --facts <facts.json> \
  --output <review-manifest.json>
```

Finalization recomputes the current plugin fingerprint and rejects task-revision drift, incomplete evidence, or non-v4 facts. Store the manifest and evidence with the successful Stage A run. Only a manifest from a successful, fully audited sheet run may be reused later.
