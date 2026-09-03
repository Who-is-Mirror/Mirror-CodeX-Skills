#!/usr/bin/env python3
"""Plan and validate task-level incremental review without replacing daily-intake semantics."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from draft_daily_from_sessions import discover_latest_plugin


INDEX_SCHEMA = "android-daily-task-index-v1"
PLAN_SCHEMA = "android-daily-task-review-plan-v1"
EVIDENCE_SCHEMA = "android-daily-task-evidence-v1"
MANIFEST_SCHEMA = "android-daily-task-review-manifest-v1"
FACTS_SCHEMA = "akbs-daily-work-facts-v4"
STATUS_VALUES = {"已完成", "处理中", "待验证", "阻塞"}
SESSION_FIELD_KEYS = {
    "work_summary",
    "command_summary",
    "project_hint",
    "work_scope_hint",
    "outcome_summary",
    "status_evidence",
}
ATTENTION_KEYS = {"key_points", "dependencies", "tomorrow_plan_evidence"}
FORBIDDEN_KEYS = {
    "messages",
    "commands",
    "cwd",
    "path",
    "raw",
    "raw_text",
    "tool_outputs",
    "thread_title",
    "retrieval_summary",
}
UNSAFE_TEXT = (
    re.compile(r"\[PATH\]|<workspace_", re.I),
    re.compile(r"(?:^|\s)(?:/[A-Za-z0-9_.-]+/|[A-Za-z]:[\\/])"),
)
GENERIC_FALLBACK = {"修改或适配相关实现", "执行相关验证或回归测试", "执行构建验证"}


class CacheError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise CacheError(message)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plugin_tree_sha256(root: Path) -> str:
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        entries.append([path.relative_to(root).as_posix(), sha256_file(path)])
    if not entries:
        fail("日报插件树为空，无法计算指纹")
    return sha256_json(entries)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CacheError(f"无法读取 JSON {path}: {exc}") from exc


def write_json(path: Path, payload: Any) -> bool:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") == rendered:
            return False
        fail(f"目标已存在且内容不同，拒绝覆盖: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return True


def parse_date(value: str) -> str:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CacheError("日期必须是 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        fail("日期必须是 YYYY-MM-DD")
    return value


def plugin_fingerprint(ops_root: Path | None = None) -> dict[str, Any]:
    version, root = discover_latest_plugin(ops_root)
    relative_files = (
        "skills/android-daily-report-intake/SKILL.md",
        "skills/android-daily-report-intake/scripts/android_daily_report_intake.py",
        "skills/android-knowledge-intake/references/daily-facts-contract.md",
        "skills/android-knowledge-intake/scripts/akbs_intake/config.py",
        "skills/android-knowledge-intake/scripts/akbs_intake/session_privacy.py",
        "skills/android-knowledge-intake/scripts/akbs_intake/report_sessions.py",
        "skills/android-knowledge-intake/scripts/akbs_intake/reports/session_summary.py",
        "skills/android-knowledge-intake/scripts/akbs_intake/reports/identity.py",
        "skills/android-knowledge-intake/scripts/akbs_intake/reports/daily_facts.py",
    )
    components: dict[str, str] = {}
    for relative in relative_files:
        path = root / relative
        if not path.is_file():
            fail(f"日报插件指纹文件缺失: {relative}")
        components[relative] = sha256_file(path)
    identity = {"plugin_version": version, "plugin_tree_sha256": plugin_tree_sha256(root), "components": components}
    return {**identity, "combined_sha256": sha256_json(identity)}


def normalize_revision(task: dict[str, Any]) -> dict[str, Any]:
    thread_id = str(task.get("thread_id") or "").strip()
    if not thread_id:
        fail("任务缺少 thread_id")
    updated_at = task.get("updated_at")
    if not isinstance(updated_at, (int, float)) or isinstance(updated_at, bool):
        fail(f"任务 {thread_id} updated_at 必须是时间数值")
    status = str(task.get("status") or "").strip()
    if not status:
        fail(f"任务 {thread_id} 缺少 status")
    latest_turn_id = task.get("latest_turn_id")
    if latest_turn_id is not None:
        latest_turn_id = str(latest_turn_id).strip() or None
    return {"updated_at": updated_at, "status": status, "latest_turn_id": latest_turn_id}


def normalize_index(payload: Any, report_date: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != INDEX_SCHEMA:
        fail(f"任务索引 schema 必须是 {INDEX_SCHEMA}")
    if payload.get("report_date") != report_date:
        fail("任务索引 report_date 不匹配")
    if payload.get("listing_crossed_lower_boundary") is not True:
        fail("任务列表未证明已跨过报告日期下边界")
    if payload.get("archived_checked") is not True:
        fail("任务索引未检查归档任务")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        fail("任务索引 tasks 必须是数组")
    normalized = []
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            fail("任务索引项必须是对象")
        if task.get("kind") != "codex":
            fail("增量复核索引只接受 kind=codex 的任务")
        thread_id = str(task.get("thread_id") or "").strip()
        if thread_id in seen:
            fail(f"任务索引存在重复 thread_id: {thread_id}")
        seen.add(thread_id)
        normalized.append({
            "thread_id": thread_id,
            "host_id": str(task.get("host_id") or "").strip() or None,
            "kind": "codex",
            "revision": normalize_revision(task),
        })
    normalized.sort(key=lambda item: item["thread_id"])
    return {
        "schema": INDEX_SCHEMA,
        "report_date": report_date,
        "listing_crossed_lower_boundary": True,
        "archived_checked": True,
        "tasks": normalized,
    }


def build_plan(index: dict[str, Any], fingerprint: dict[str, Any], previous: Any | None) -> dict[str, Any]:
    current = {item["thread_id"]: item for item in index["tasks"]}
    previous_tasks: dict[str, Any] = {}
    reasons: list[str] = []
    full_review = previous is None
    if previous is None:
        reasons.append("no_previous_manifest")
    elif not isinstance(previous, dict) or previous.get("schema") != MANIFEST_SCHEMA or previous.get("report_date") != index["report_date"]:
        full_review = True
        reasons.append("previous_manifest_invalid")
    elif previous.get("plugin_fingerprint", {}).get("combined_sha256") != fingerprint["combined_sha256"]:
        full_review = True
        reasons.append("daily_plugin_fingerprint_changed")
    else:
        previous_tasks = {item["thread_id"]: item for item in previous.get("tasks", []) if isinstance(item, dict) and item.get("thread_id")}

    review: list[str] = []
    reuse: list[str] = []
    if full_review:
        review = sorted(current)
    else:
        for thread_id, task in current.items():
            old = previous_tasks.get(thread_id)
            if not old or old.get("revision") != task["revision"]:
                review.append(thread_id)
            else:
                reuse.append(thread_id)
        if review:
            reasons.append("new_or_changed_tasks")
    removed = sorted(set(previous_tasks) - set(current))
    if removed:
        reasons.append("tasks_removed_from_complete_index")
    return {
        "schema": PLAN_SCHEMA,
        "report_date": index["report_date"],
        "plugin_fingerprint": fingerprint,
        "task_index_sha256": sha256_json(index),
        "full_review": full_review,
        "review_thread_ids": sorted(review),
        "reuse_thread_ids": sorted(reuse),
        "removed_thread_ids": removed,
        "reason_codes": reasons,
    }


def validate_safe_text(value: str, *, context: str) -> None:
    if any(pattern.search(value) for pattern in UNSAFE_TEXT):
        fail(f"紧凑证据含原始路径或占位符: {context}")
    if value.strip() in GENERIC_FALLBACK:
        fail(f"紧凑证据含通用兜底描述: {context}")


def string_list(value: Any, *, context: str, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        fail(f"{context} 必须是非空字符串数组")
    if nonempty and not value:
        fail(f"{context} 不能为空")
    for index, item in enumerate(value):
        validate_safe_text(item, context=f"{context}[{index}]")
    return value


def validate_compact_evidence(value: Any, *, thread_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"任务 {thread_id} compact_evidence 必须是对象")
    if FORBIDDEN_KEYS.intersection(value):
        fail(f"任务 {thread_id} compact_evidence 含禁止原始字段")
    include = value.get("include")
    if not isinstance(include, bool):
        fail(f"任务 {thread_id} compact_evidence.include 必须是布尔值")
    if not include:
        reason = str(value.get("exclusion_reason") or "").strip()
        if not reason:
            fail(f"任务 {thread_id} 排除时必须写 exclusion_reason")
        validate_safe_text(reason, context=f"{thread_id}.exclusion_reason")
        extra = set(value) - {"include", "exclusion_reason", "source_turn_ids"}
        if extra:
            fail(f"任务 {thread_id} 排除证据含多余字段: {sorted(extra)}")
        string_list(value.get("source_turn_ids", []), context=f"{thread_id}.source_turn_ids")
        return value

    allowed = {"include", "session_fields", "key_points", "dependencies", "tomorrow_plan_evidence", "source_turn_ids"}
    extra = set(value) - allowed
    if extra:
        fail(f"任务 {thread_id} compact_evidence 含未授权字段: {sorted(extra)}")
    fields = value.get("session_fields")
    if not isinstance(fields, list) or not fields:
        fail(f"任务 {thread_id} session_fields 必须是非空数组")
    for position, field in enumerate(fields):
        if not isinstance(field, dict) or set(field) != SESSION_FIELD_KEYS:
            fail(f"任务 {thread_id} session_fields[{position}] 必须精确包含当前会话字段与结果证据")
        string_list(field["work_summary"], context=f"{thread_id}.work_summary", nonempty=True)
        string_list(field["command_summary"], context=f"{thread_id}.command_summary")
        for key in ("project_hint", "work_scope_hint"):
            if not isinstance(field[key], str):
                fail(f"任务 {thread_id} {key} 必须是字符串")
            validate_safe_text(field[key], context=f"{thread_id}.{key}")
        string_list(field["outcome_summary"], context=f"{thread_id}.outcome_summary", nonempty=True)
        statuses = string_list(field["status_evidence"], context=f"{thread_id}.status_evidence", nonempty=True)
        if any(status not in STATUS_VALUES for status in statuses):
            fail(f"任务 {thread_id} status_evidence 含非法状态")
    for key in ATTENTION_KEYS:
        string_list(value.get(key, []), context=f"{thread_id}.{key}")
    string_list(value.get("source_turn_ids", []), context=f"{thread_id}.source_turn_ids")
    return value


def evidence_map(payload: Any, *, report_date: str, fingerprint: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("schema") != EVIDENCE_SCHEMA:
        fail(f"证据 schema 必须是 {EVIDENCE_SCHEMA}")
    if payload.get("report_date") != report_date:
        fail("证据 report_date 不匹配")
    if payload.get("plugin_fingerprint", {}).get("combined_sha256") != fingerprint["combined_sha256"]:
        fail("证据与当前日报插件指纹不匹配")
    result: dict[str, dict[str, Any]] = {}
    for record in payload.get("tasks", []):
        if not isinstance(record, dict):
            fail("证据 tasks 项必须是对象")
        thread_id = str(record.get("thread_id") or "").strip()
        if not thread_id or thread_id in result:
            fail("证据 thread_id 缺失或重复")
        if not isinstance(record.get("revision"), dict):
            fail(f"任务 {thread_id} 证据缺少 revision")
        validate_compact_evidence(record.get("compact_evidence"), thread_id=thread_id)
        result[thread_id] = record
    return result


def assemble_evidence(plan: dict[str, Any], index: dict[str, Any], updated: Any, previous: Any | None) -> dict[str, Any]:
    if plan.get("schema") != PLAN_SCHEMA or plan.get("report_date") != index["report_date"]:
        fail("增量复核 plan 无效")
    if plan.get("task_index_sha256") != sha256_json(index):
        fail("任务索引在 plan 后已漂移，需重新规划")
    fingerprint = plan["plugin_fingerprint"]
    updated_map = evidence_map(updated, report_date=index["report_date"], fingerprint=fingerprint)
    previous_map = evidence_map(previous, report_date=index["report_date"], fingerprint=fingerprint) if previous is not None else {}
    review_ids = set(plan.get("review_thread_ids", []))
    reuse_ids = set(plan.get("reuse_thread_ids", []))
    if set(updated_map) != review_ids:
        fail("更新证据必须精确覆盖 review_thread_ids")
    current = {item["thread_id"]: item for item in index["tasks"]}
    assembled = []
    for thread_id in sorted(current):
        record = updated_map.get(thread_id) if thread_id in review_ids else previous_map.get(thread_id)
        if record is None or thread_id not in review_ids | reuse_ids:
            fail(f"任务 {thread_id} 没有可用的当前或复用证据")
        if record.get("revision") != current[thread_id]["revision"]:
            fail(f"任务 {thread_id} 证据 revision 与当前索引不一致")
        assembled.append(record)
    return {"schema": EVIDENCE_SCHEMA, "report_date": index["report_date"], "plugin_fingerprint": fingerprint, "tasks": assembled}


def finalize_manifest(index: dict[str, Any], evidence: Any, facts: Any, fingerprint: dict[str, Any]) -> dict[str, Any]:
    records = evidence_map(evidence, report_date=index["report_date"], fingerprint=fingerprint)
    current = {item["thread_id"]: item for item in index["tasks"]}
    if set(records) != set(current):
        fail("完整证据未覆盖当前全部任务")
    if not isinstance(facts, dict) or facts.get("schema") != FACTS_SCHEMA or facts.get("report_date") != index["report_date"]:
        fail(f"最终 facts 必须是同日的 {FACTS_SCHEMA}")
    tasks = []
    for thread_id in sorted(current):
        record = records[thread_id]
        if record.get("revision") != current[thread_id]["revision"]:
            fail(f"任务 {thread_id} 在复核期间已变化")
        tasks.append({"thread_id": thread_id, "host_id": current[thread_id]["host_id"], "revision": current[thread_id]["revision"], "evidence_sha256": sha256_json(record)})
    return {
        "schema": MANIFEST_SCHEMA,
        "report_date": index["report_date"],
        "plugin_fingerprint": fingerprint,
        "task_index_sha256": sha256_json(index),
        "task_evidence_sha256": sha256_json(evidence),
        "facts_sha256": sha256_json(facts),
        "tasks": tasks,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Plan and validate incremental Android daily task review")
    sub = result.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--date", required=True)
    plan.add_argument("--index", required=True)
    plan.add_argument("--previous-manifest")
    plan.add_argument("--ops-root")
    plan.add_argument("--output", required=True)
    assemble = sub.add_parser("assemble")
    assemble.add_argument("--plan", required=True)
    assemble.add_argument("--index", required=True)
    assemble.add_argument("--updated-evidence", required=True)
    assemble.add_argument("--previous-evidence")
    assemble.add_argument("--output", required=True)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--date", required=True)
    finalize.add_argument("--index", required=True)
    finalize.add_argument("--evidence", required=True)
    finalize.add_argument("--facts", required=True)
    finalize.add_argument("--ops-root")
    finalize.add_argument("--output", required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "plan":
            report_date = parse_date(args.date)
            index = normalize_index(load_json(Path(args.index)), report_date)
            previous = load_json(Path(args.previous_manifest)) if args.previous_manifest else None
            payload = build_plan(index, plugin_fingerprint(Path(args.ops_root) if args.ops_root else None), previous)
        elif args.command == "assemble":
            plan_payload = load_json(Path(args.plan))
            report_date = parse_date(str(plan_payload.get("report_date") or ""))
            index = normalize_index(load_json(Path(args.index)), report_date)
            previous = load_json(Path(args.previous_evidence)) if args.previous_evidence else None
            payload = assemble_evidence(plan_payload, index, load_json(Path(args.updated_evidence)), previous)
        else:
            report_date = parse_date(args.date)
            index = normalize_index(load_json(Path(args.index)), report_date)
            fingerprint = plugin_fingerprint(Path(args.ops_root) if args.ops_root else None)
            payload = finalize_manifest(index, load_json(Path(args.evidence)), load_json(Path(args.facts)), fingerprint)
        written = write_json(Path(args.output), payload)
        print(json.dumps({"status": "PASS", "output": str(Path(args.output).resolve()), "written": written, **({"review_thread_ids": payload["review_thread_ids"], "reuse_thread_ids": payload["reuse_thread_ids"], "reason_codes": payload["reason_codes"]} if args.command == "plan" else {})}, ensure_ascii=False, indent=2))
        return 0
    except CacheError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
