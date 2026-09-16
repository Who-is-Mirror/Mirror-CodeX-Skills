#!/usr/bin/env python3
"""Validate, render, archive, and hash one performance draft as one atomic delivery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from runtime_paths import resolve_runtime_paths
from validate_draft import validate


DEFAULT_ARCHIVE_ROOT = resolve_runtime_paths().draft_archive_root


def number(value: int | float) -> str:
    return f"{float(value):g}"


def render(data: dict, draft_path: Path, digest: str) -> str:
    lines = [
        f"# {data['month']} 绩效草稿",
        "",
        "> 此文档仅供检查；尚未操作绩效页面，也未提交审核。",
        "",
        "## 项目汇总",
        "",
        "| 项目 | 任务类型 | 日期 | 工时 |",
        "|---|---|---|---:|",
    ]
    for entry in data["entries"]:
        lines.append(
            f"| {entry['project']} | {entry['task_type']} | "
            f"{entry['start_date']}～{entry['end_date']} | {number(entry['work_days'])} 天 |"
        )
    lines.extend(
        [
            f"| **合计** |  |  | **{number(data['confirmed_work_days'])} 天** |",
            "",
            "## 逐项目填写内容",
            "",
        ]
    )
    for entry in data["entries"]:
        lines.extend(
            [
                f"### {entry['project']}",
                "",
                f"- 任务类型：{entry['task_type']}",
                f"- 任务描述：{entry['task_description']}",
                f"- 日期：{entry['start_date']}～{entry['end_date']}",
                f"- 工时：{number(entry['work_days'])} 天",
                "- 工作成果（每项单独编号）：",
                "",
            ]
        )
        lines.extend(f"  {index}. {value}" for index, value in enumerate(entry["work_results"], 1))
        lines.extend(["", "- 证据："])
        lines.extend(f"  - {value}" for value in entry["evidence"])
        lines.append("")

    lines.extend(
        [
            f"- 工时依据：{data['work_days_basis']}",
            f"- 来源：{len(data['weekly_sources'])} 份周报、{len(data['daily_sources'])} 份日报",
            "",
            "## 请检查",
            "",
            "请逐项检查项目代码、任务类型、日期、工时、任务描述，以及每个独立编号的工作成果。",
            "检查完成后再次明确回复“按这个填写”或“检查完成，开始填写”，才会进入浏览器阶段。",
            "",
            f"- 结构化草稿：`{draft_path}`",
            f"- SHA-256：`{digest}`",
            "",
        ]
    )
    return "\n".join(lines)


def atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def atomic_copy(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("draft", type=Path)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="real-file delivery directory inside the current task; defaults to ./outputs",
    )
    parser.add_argument("--timestamp", help="YYYYMMDD-HHMMSS; defaults to current local time")
    args = parser.parse_args()

    source = args.draft.resolve()
    data = json.loads(source.read_text(encoding="utf-8"))
    validation = validate(data)
    timestamp = args.timestamp or datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    datetime.strptime(timestamp, "%Y%m%d-%H%M%S")
    workspace = Path.cwd().resolve()
    requested_output = args.output_dir or (workspace / "outputs")
    if requested_output.is_symlink():
        raise ValueError("output directory must be a real directory, not a symlink")
    output_dir = requested_output.resolve()
    try:
        output_dir.relative_to(workspace)
    except ValueError as error:
        raise ValueError("output directory must stay inside the current task workspace") from error

    destination = (args.archive_root / validation["month"]).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    draft_path = destination / f"performance-draft-{timestamp}.json"
    review_path = destination / f"performance-review-{timestamp}.md"
    delivery_draft_path = output_dir / draft_path.name
    delivery_review_path = output_dir / review_path.name
    all_paths = (draft_path, review_path, delivery_draft_path, delivery_review_path)
    if any(path.exists() or path.is_symlink() for path in all_paths):
        raise FileExistsError("timestamp already exists; do not overwrite an earlier reviewed draft")
    atomic_copy(source, draft_path)
    digest = hashlib.sha256(draft_path.read_bytes()).hexdigest()
    atomic_text(review_path, render(data, delivery_draft_path, digest))
    atomic_copy(draft_path, delivery_draft_path)
    atomic_copy(review_path, delivery_review_path)
    print(
        json.dumps(
            {
                "status": "ready_for_review",
                "month": validation["month"],
                "entries": validation["entries"],
                "work_days": validation["work_days"],
                "work_days_by_project": [
                    {"project": entry["project"], "work_days": entry["work_days"]}
                    for entry in data["entries"]
                ],
                "draft_path": str(draft_path),
                "review_path": str(review_path),
                "delivery_draft_path": str(delivery_draft_path),
                "delivery_review_path": str(delivery_review_path),
                "sha256": digest,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
