#!/usr/bin/env python3
"""Revise project work days deterministically without ambiguous text patches."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from validate_draft import validate


def number(value: int | float) -> int | float:
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else numeric


def parse_assignments(values: list[str]) -> dict[str, int | float]:
    assignments: dict[str, int | float] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"项目工时必须使用 PROJECT=DAYS: {value}")
        project, raw_days = value.split("=", 1)
        project = project.strip()
        if not project or project in assignments:
            raise ValueError(f"项目为空或重复: {project}")
        days = number(raw_days)
        if days <= 0:
            raise ValueError(f"项目工时必须是正数: {project}")
        assignments[project] = days
    return assignments


def revise(data: dict, ordered: list[float] | None, explicit: list[str] | None) -> dict:
    validate(data)
    entries = data["entries"]
    projects = [entry["project"] for entry in entries]
    if ordered is not None:
        if len(ordered) != len(entries):
            raise ValueError(f"工时数量 {len(ordered)} 与项目数量 {len(entries)} 不一致")
        assignments = {project: number(days) for project, days in zip(projects, ordered)}
    else:
        assignments = parse_assignments(explicit or [])
        if set(assignments) != set(projects):
            missing = [project for project in projects if project not in assignments]
            extra = [project for project in assignments if project not in projects]
            raise ValueError(f"项目映射不完整，缺少={missing}，多余={extra}")

    if any(float(days) <= 0 for days in assignments.values()):
        raise ValueError("所有项目工时必须是正数")
    for entry in entries:
        entry["work_days"] = assignments[entry["project"]]
    total = number(sum(float(assignments[project]) for project in projects))
    data["confirmed_work_days"] = total
    allocation = "、".join(f"{project} {assignments[project]:g} 天" for project in projects)
    data["work_days_basis"] = (
        f"用户确认工时分配：{allocation}，合计 {total:g} 天；"
        "项目归并与成果仍以周报为主、日报为辅。"
    )
    validate(data)
    return {
        "status": "revised",
        "work_days": total,
        "work_days_by_project": [
            {"project": project, "work_days": assignments[project]} for project in projects
        ],
    }


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("draft", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ordered", nargs="+", type=float, help="days in the existing entry order")
    group.add_argument("--set", nargs="+", dest="explicit", help="complete PROJECT=DAYS mapping")
    args = parser.parse_args()

    path = args.draft.resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    result = revise(data, args.ordered, args.explicit)
    atomic_json(path, data)
    result["draft_path"] = str(path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
