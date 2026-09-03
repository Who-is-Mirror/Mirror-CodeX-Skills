#!/usr/bin/env python3
"""Convert semantically reviewed v4 daily facts to deterministic A:F sheet rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


DRAFT_SCHEMA = "codex-daily-sheet-draft-v1"
CANDIDATE_SCHEMA = "codex-daily-session-candidates-v1"
TASK_EVIDENCE_SCHEMA = "android-daily-task-evidence-v1"
FACTS_SCHEMA = "akbs-daily-work-facts-v4"
ROWS_SCHEMA = "daily-sheet-rows-v1"
HEADER = ["项目 / 客户", "类型", "任务", "分项", "内容", "状态"]
COLUMNS = ("A", "B", "C", "D", "E", "F")
ALLOWED_STATUSES = {"已完成", "处理中", "待验证", "阻塞"}
PROJECT_TYPES = {"Patch", "App", "GMS", "Doc", "Other"}
FORBIDDEN_VISIBLE_MARKERS = ("[PATH]", "<workspace_", "<cwd>", "<source_path>")
GENERIC_FILLER_MARKERS = ("修改或适配相关实现",)
ABSOLUTE_PATH_RE = re.compile(r"(?:^|\s)(?:/[A-Za-z0-9_.-]+/|[A-Za-z]:[\\/])")


class RowsError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise RowsError(message)


def text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def require_text(value: Any, label: str) -> str:
    result = text(value)
    if not result:
        fail(f"{label} 不能为空")
    return result


def string_list(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list):
        fail(f"{label} 必须是数组")
    result = [require_text(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if nonempty and not result:
        fail(f"{label} 必须是非空数组")
    return result


def bullet_text(items: list[str]) -> str:
    if not items:
        return "无。"
    lines: list[str] = []
    for item in items:
        for line in text(item).split("\n"):
            if line.strip():
                lines.append(f"- {line.strip()}")
    return "\n".join(lines) if lines else "无。"


def row(a: str = "", b: str = "", c: str = "", d: str = "", e: str = "", f: str = "") -> dict[str, str]:
    return dict(zip(COLUMNS, (a, b, c, d, e, f), strict=True))


def unwrap(payload: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(payload, dict):
        fail("输入必须是 JSON 对象")
    schema = payload.get("schema")
    if schema in {DRAFT_SCHEMA, CANDIDATE_SCHEMA, TASK_EVIDENCE_SCHEMA}:
        fail("自动会话候选不是语义复核后的日报 facts，拒绝生成 rows 或写表")
    if schema == FACTS_SCHEMA:
        if payload.get("unresolved"):
            fail("facts 含 unresolved，拒绝生成 rows 或写表")
        return payload, ""
    fail(f"输入 schema 必须是语义复核后的 {FACTS_SCHEMA}")


def assert_display_safe(value: Any, *, label: str = "facts") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            assert_display_safe(child, label=f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_display_safe(child, label=f"{label}[{index}]")
    elif isinstance(value, str):
        if any(marker.casefold() in value.casefold() for marker in FORBIDDEN_VISIBLE_MARKERS):
            fail(f"{label} 含禁止的路径占位符")
        if any(marker in value for marker in GENERIC_FILLER_MARKERS):
            fail(f"{label} 含无证据的通用兜底描述")
        if ABSOLUTE_PATH_RE.search(value):
            fail(f"{label} 含禁止的原始绝对路径")


def facts_sha256(facts: dict[str, Any]) -> str:
    canonical = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def project_key(scope: dict[str, Any]) -> tuple[str, ...]:
    work_type = text(scope.get("work_type"))
    return (
        "project",
        text(scope.get("project")),
        text(scope.get("customer")),
        text(scope.get("downstream_customer")),
        work_type,
        text(scope.get("app_name")) if work_type == "App" else "",
    )


def document_key(scope: dict[str, Any]) -> tuple[str, ...]:
    return ("document", text(scope.get("document_name")))


def standalone_key(scope: dict[str, Any]) -> tuple[str, ...]:
    return ("standalone", text(scope.get("work_name")))


def identity_for_today(kind: str, scope: dict[str, Any], label: str) -> tuple[str, str, tuple[str, ...]]:
    work_type = require_text(scope.get("work_type"), f"{label}.work_type")
    if work_type == "Document":
        work_type = "Doc"
    if kind == "project":
        project = require_text(scope.get("project"), f"{label}.project")
        customer = require_text(scope.get("customer"), f"{label}.customer")
        downstream = text(scope.get("downstream_customer"))
        if work_type not in PROJECT_TYPES:
            fail(f"{label}.work_type 非法: {work_type}")
        if work_type == "GMS":
            fail(f"{label} 是 GMS；A:F 模板不能无损映射周期字段，拒绝生成 rows")
        column_a = f"{project} / {customer}" + (f" → {downstream}" if downstream else "")
        if work_type == "App":
            app_name = require_text(scope.get("app_name"), f"{label}.app_name")
            column_b = f"App / {app_name}"
        else:
            if scope.get("app_name") is not None:
                fail(f"{label} 非 App 不允许 app_name")
            column_b = work_type
        return column_a, column_b, project_key({**scope, "work_type": work_type})
    if kind == "document":
        if work_type not in {"Doc", "Document"}:
            fail(f"{label}.work_type 必须是 Doc")
        name = require_text(scope.get("document_name"), f"{label}.document_name")
        return "Other", f"Doc / {name}", document_key(scope)
    if work_type != "Other":
        fail(f"{label}.work_type 必须是 Other")
    name = require_text(scope.get("work_name"), f"{label}.work_name")
    if name.casefold().startswith("doc /") or name.startswith("Doc / "):
        fail(f"{label}.work_name 与非项目 Doc 编码冲突")
    return "Other", name, standalone_key(scope)


def plan_map(facts: dict[str, Any]) -> dict[tuple[str, ...], list[str]]:
    plan = facts.get("tomorrow_plan")
    if not isinstance(plan, dict):
        fail("tomorrow_plan 必须是对象")
    result: dict[tuple[str, ...], list[str]] = {}
    for collection, kind, key_fn in (
        ("projects", "project", project_key),
        ("documents", "document", document_key),
        ("standalone_work", "standalone", standalone_key),
    ):
        rows = plan.get(collection)
        if not isinstance(rows, list):
            fail(f"tomorrow_plan.{collection} 必须是数组")
        for index, scope in enumerate(rows):
            if not isinstance(scope, dict):
                fail(f"tomorrow_plan.{collection}[{index}] 必须是对象")
            label = f"tomorrow_plan.{collection}[{index}]"
            work_type = text(scope.get("work_type"))
            normalized_scope = {**scope, "work_type": "Doc" if work_type == "Document" else work_type}
            if work_type == "GMS":
                fail(f"{label} 是 GMS；A:F 模板不能无损映射周期字段")
            identity_for_today(kind, normalized_scope, label)
            if any(field in scope for field in ("today_topic", "current_result", "work_items", "status")):
                fail(f"{label} 含今日字段或状态")
            items = string_list(scope.get("plan_items"), f"{label}.plan_items", nonempty=True)
            key = key_fn(normalized_scope)
            if key in result:
                fail(f"{label} 与已有明日计划身份重复")
            result[key] = items
    return result


def validate_work_item(item: Any, label: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        fail(f"{label} 必须是对象")
    result = {
        "name": require_text(item.get("name"), f"{label}.name"),
        "did": string_list(item.get("did"), f"{label}.did", nonempty=True),
        "how": string_list(item.get("how"), f"{label}.how", nonempty=True),
        "result": require_text(item.get("result"), f"{label}.result"),
        "status": require_text(item.get("status"), f"{label}.status"),
    }
    if result["status"] not in ALLOWED_STATUSES:
        fail(f"{label}.status 非法: {result['status']}")
    return result


def scope_rows(
    *,
    kind: str,
    scope: dict[str, Any],
    label: str,
    plans: dict[tuple[str, ...], list[str]],
) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    column_a, column_b, key = identity_for_today(kind, scope, label)
    topic = require_text(scope.get("today_topic"), f"{label}.today_topic")
    current = require_text(scope.get("current_result"), f"{label}.current_result")
    work_items = scope.get("work_items")
    if not isinstance(work_items, list) or not work_items:
        fail(f"{label}.work_items 必须是非空数组")
    key_points = string_list(scope.get("key_points"), f"{label}.key_points")
    dependencies = string_list(scope.get("dependencies"), f"{label}.dependencies")
    if "status" in scope:
        fail(f"{label} 概况范围不允许 status")

    result = [
        row(column_a, column_b, "今日概况", "今日主题", bullet_text([topic]), ""),
        row(d="当前结果", e=bullet_text([current])),
    ]
    for index, raw_item in enumerate(work_items, 1):
        item = validate_work_item(raw_item, f"{label}.work_items[{index - 1}]")
        heading = f"{index}. {item['name']}"
        result.extend([
            row(c=heading, d="做了什么", e=bullet_text(item["did"])),
            row(d="怎么做的", e=bullet_text(item["how"])),
            row(d="结果", e=bullet_text([item["result"]]), f=item["status"]),
        ])
    result.extend([
        row(c="重点说明", e=bullet_text(key_points)),
        row(c="依赖 / 需协调", e=bullet_text(dependencies)),
        row(c="明日计划", e=bullet_text(plans.pop(key, []))),
    ])
    return result, key


def convert_facts_to_rows(payload: Any) -> dict[str, Any]:
    facts, plugin_version = unwrap(payload)
    if facts.get("schema") != FACTS_SCHEMA:
        fail(f"facts.schema 必须是 {FACTS_SCHEMA}")
    assert_display_safe(facts)
    report_date = require_text(facts.get("report_date"), "report_date")
    plans = plan_map(facts)
    all_rows: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    scopes: list[tuple[str, dict[str, Any], str]] = []
    for collection, kind in (("projects", "project"), ("documents", "document"), ("standalone_work", "standalone")):
        values = facts.get(collection)
        if not isinstance(values, list):
            fail(f"{collection} 必须是数组")
        for index, scope in enumerate(values):
            if not isinstance(scope, dict):
                fail(f"{collection}[{index}] 必须是对象")
            scopes.append((kind, scope, f"{collection}[{index}]"))
    if not scopes:
        fail("今天的 projects/documents/standalone_work 至少一项非空")

    scope_start_rows: list[int] = []
    blank_rows: list[int] = []
    for scope_index, (kind, scope, label) in enumerate(scopes):
        if scope_index:
            all_rows.append(row())
            blank_rows.append(len(all_rows) + 1)
        scope_start_rows.append(len(all_rows) + 2)
        generated, key = scope_rows(kind=kind, scope=scope, label=label, plans=plans)
        if key in seen:
            fail(f"{label} 身份重复")
        seen.add(key)
        all_rows.extend(generated)
    if plans:
        identities = [" / ".join(part for part in key if part) for key in plans]
        fail("存在只属于明日计划、无法用确认模板无损承载的范围: " + "; ".join(identities))

    values = [HEADER] + [[entry[column] for column in COLUMNS] for entry in all_rows]
    return {
        "schema": ROWS_SCHEMA,
        "report_date": report_date,
        "source_facts_sha256": facts_sha256(facts),
        "source_plugin_version": plugin_version,
        "header": HEADER,
        "rows": all_rows,
        "grid_data": {
            "start_cell": "A1",
            "end_cell": f"F{len(values)}",
            "values": values,
        },
        "format_hints": {
            "header_fill": "#8CDDFA",
            "ordinary_row_height": 52,
            "separator_row_height": 50,
            "scope_start_rows": scope_start_rows,
            "separator_rows": blank_rows,
            "wrap_columns": ["C", "E"],
        },
    }


def write_json_idempotent(path: Path, payload: dict[str, Any]) -> bool:
    target = path.expanduser().resolve()
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"输出已存在但无法安全读取，拒绝覆盖: {target}: {exc}")
        if existing != payload:
            fail(f"输出已存在且内容不同，拒绝覆盖: {target}")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError:
        fail(f"输出在写入时出现竞争，拒绝覆盖: {target}")
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert semantically reviewed v4 daily facts to A:F sheet rows")
    parser.add_argument("--input", required=True, help="reviewed akbs-daily-work-facts-v4 JSON")
    parser.add_argument("--output", required=True, help="new or identical rows JSON path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = json.loads(Path(args.input).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"无法读取输入 JSON: {exc}")
    result = convert_facts_to_rows(payload)
    changed = write_json_idempotent(Path(args.output), result)
    print(json.dumps({
        "status": "PASS",
        "output": str(Path(args.output).expanduser().resolve()),
        "written": changed,
        "row_count": len(result["grid_data"]["values"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RowsError as exc:
        print(f"facts_to_sheet_rows: {exc}", file=sys.stderr)
        raise SystemExit(1)
