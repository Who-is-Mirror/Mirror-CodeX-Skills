#!/usr/bin/env python3
"""Validate a performance-entry draft without touching the browser."""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path


SCHEMA = "mirror-performance-draft-v3"
SOURCE_POLICY = "weekly-primary-daily-secondary"
TASK_TYPES = {"计划任务", "临时任务"}
FORBIDDEN_KEYS = {"username", "account", "password", "token", "cookie", "cookies", "credential", "credentials"}
COMPOUND_RESULT_MARKS = ("；", ";", "、", "\n", "\r")
PRENUMBERED_RESULT = re.compile(r"^\s*\d+\s*[.、)）]")
SECOND_ACTION = re.compile(
    r"(?:，|,|并且|并|以及|同时|随后|然后)\s*"
    r"(?:完成|修复|定位|验证|推进|适配|解决|确认|交付|实现|优化|分析|排查|处理|更新|新增|支持|收敛)"
)


def fail(message: str) -> None:
    raise ValueError(message)


def parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        fail(f"{field} 必须是 YYYY-MM-DD 字符串")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        fail(f"{field} 不是有效日期: {value}")
        raise AssertionError from exc


def validate(data: object) -> dict:
    if not isinstance(data, dict):
        fail("草稿根节点必须是对象")

    def reject_secrets(value: object, location: str = "root") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in FORBIDDEN_KEYS:
                    fail(f"草稿禁止包含凭据字段: {location}.{key}")
                reject_secrets(child, f"{location}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reject_secrets(child, f"{location}[{index}]")

    reject_secrets(data)
    if data.get("schema") != SCHEMA:
        fail(f"schema 必须是 {SCHEMA}")

    month = data.get("month")
    if not isinstance(month, str) or len(month) != 7:
        fail("month 必须是 YYYY-MM")
    try:
        date.fromisoformat(month + "-01")
    except ValueError:
        fail(f"month 不是有效月份: {month}")

    if data.get("source_policy") != SOURCE_POLICY:
        fail(f"source_policy 必须是 {SOURCE_POLICY}")
    weekly_sources = data.get("weekly_sources")
    daily_sources = data.get("daily_sources")
    if not isinstance(weekly_sources, list) or not weekly_sources:
        fail("weekly_sources 必须至少包含一份周报主来源")
    if not isinstance(daily_sources, list):
        fail("daily_sources 必须是数组")
    for field, sources in (("weekly_sources", weekly_sources), ("daily_sources", daily_sources)):
        if any(not isinstance(item, str) or not item.strip() for item in sources):
            fail(f"{field} 不能包含空项")
        if any(not Path(item).is_absolute() for item in sources):
            fail(f"{field} 必须使用绝对路径")
        missing = [item for item in sources if not Path(item).is_file()]
        if missing:
            fail(f"{field} 存在不可读取来源: {missing[0]}")

    work_days_basis = data.get("work_days_basis")
    if not isinstance(work_days_basis, str) or not work_days_basis.strip():
        fail("work_days_basis 必须说明总工时的来源或拟定依据")
    if "review_notes" in data:
        fail("v3 草稿不再包含 review_notes 或审阅说明")

    confirmed = data.get("confirmed_work_days")
    if not isinstance(confirmed, (int, float)) or isinstance(confirmed, bool) or confirmed <= 0:
        fail("confirmed_work_days 必须是正数")

    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        fail("entries 必须是非空数组")

    seen: set[tuple[str, str]] = set()
    total = 0.0
    for index, entry in enumerate(entries, 1):
        prefix = f"entries[{index}]"
        if not isinstance(entry, dict):
            fail(f"{prefix} 必须是对象")
        task_type = entry.get("task_type")
        if task_type not in TASK_TYPES:
            fail(f"{prefix}.task_type 必须是计划任务或临时任务")
        for field in ("project", "task_description"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                fail(f"{prefix}.{field} 不能为空")
        project = entry["project"].strip()
        key = (month, project)
        if key in seen:
            fail(f"同一月份存在重复项目: {project}")
        seen.add(key)

        start = parse_date(entry.get("start_date"), f"{prefix}.start_date")
        end = parse_date(entry.get("end_date"), f"{prefix}.end_date")
        if start > end:
            fail(f"{prefix} 开始时间晚于结束时间")
        if start.strftime("%Y-%m") != month or end.strftime("%Y-%m") != month:
            fail(f"{prefix} 日期必须位于目标月份 {month}")

        work_days = entry.get("work_days")
        if not isinstance(work_days, (int, float)) or isinstance(work_days, bool) or work_days <= 0:
            fail(f"{prefix}.work_days 必须是正数")
        total += float(work_days)

        results = entry.get("work_results")
        if not isinstance(results, list) or not results:
            fail(f"{prefix}.work_results 必须是非空数组")
        if any(not isinstance(item, str) or not item.strip() for item in results):
            fail(f"{prefix}.work_results 不能包含空项")
        for result_index, item in enumerate(results, 1):
            text = item.strip()
            item_prefix = f"{prefix}.work_results[{result_index}]"
            if PRENUMBERED_RESULT.search(text):
                fail(f"{item_prefix} 不要预先编号，编号由输出脚本统一生成")
            if any(mark in text for mark in COMPOUND_RESULT_MARKS):
                fail(f"{item_prefix} 包含合并标记，必须把每个成果拆成独立数组元素")
            without_terminal = text.rstrip("。！？!?")
            if re.search(r"[。！？!?]", without_terminal):
                fail(f"{item_prefix} 包含多个句子，必须拆成独立数组元素")
            if SECOND_ACTION.search(text):
                fail(f"{item_prefix} 包含第二个成果动作，必须拆成独立数组元素")

        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            fail(f"{prefix}.evidence 必须至少包含一项来源")
        if any(not isinstance(item, str) or not item.strip() for item in evidence):
            fail(f"{prefix}.evidence 不能包含空项")
        if not any(item.startswith("weekly:") for item in evidence):
            fail(f"{prefix}.evidence 必须至少包含一项 weekly: 周报证据")

    if abs(total - float(confirmed)) > 1e-9:
        fail(f"工时合计 {total:g} 与 confirmed_work_days {confirmed:g} 不一致")
    return {"month": month, "entries": len(entries), "work_days": total}


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: validate_draft.py <draft.json>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        result = validate(data)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        f"PASS month={result['month']} entries={result['entries']} "
        f"work_days={result['work_days']:g}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
