#!/usr/bin/env python3
"""Discover canonical weekly and daily evidence for one performance month."""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from runtime_paths import resolve_runtime_paths


RUNTIME_PATHS = resolve_runtime_paths()
DEFAULT_ROOT = RUNTIME_PATHS.source_root
DEFAULT_LEGACY_ROOT = RUNTIME_PATHS.legacy_source_root
WEEKLY_RE = re.compile(r"^(\d{8})-(\d{8})\.json$")
WEEK_RANGE_RE = re.compile(r"^(\d{8})-(\d{8})$")
ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
REPORT_KINDS = {"weekly": "weekly_trace", "daily": "daily_trace"}


class DiscoveryError(ValueError):
    """The local archive cannot safely select one member's sources."""


def parse_month(value: str) -> tuple[date, date]:
    start = date.fromisoformat(value + "-01")
    end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    return start, end


def ymd(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _valid_alias(value: object, *, label: str) -> str:
    alias = value.strip() if isinstance(value, str) else ""
    if alias in {"member_alias", "admin_alias", "unknown"} or not ALIAS_RE.fullmatch(alias):
        raise DiscoveryError(f"{label} has no safe member alias")
    return alias


def _load_toml(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DiscoveryError(f"AKBS profile must be a regular non-symlink file: {path}")
    try:
        import tomllib

        with path.open("rb") as stream:
            payload = tomllib.load(stream)
    except (ImportError, OSError, ValueError) as error:
        raise DiscoveryError(f"cannot read authoritative AKBS profile {path}: {error}") from error
    if not isinstance(payload, dict):
        raise DiscoveryError(f"authoritative AKBS profile is not a TOML table: {path}")
    return payload


def configured_member_alias(codex_home: Path) -> tuple[str, str] | None:
    """Resolve only the public, target AKBS profile identity; never import plugin code."""
    explicit = os.environ.get("PERFORMANCE_MEMBER_ALIAS", "").strip()
    if explicit:
        return _valid_alias(explicit, label="PERFORMANCE_MEMBER_ALIAS"), "PERFORMANCE_MEMBER_ALIAS"

    profile_path = codex_home / "akbs-member-ops.toml"
    if not (profile_path.exists() or profile_path.is_symlink()):
        return None
    payload = _load_toml(profile_path)
    requested = next(
        (
            os.environ.get(name, "").strip()
            for name in ("CODEX_REPORT_PROFILE", "CODEX_WORK_REPORT_PROFILE")
            if os.environ.get(name, "").strip()
        ),
        "",
    )
    selected = requested or str(payload.get("default_profile") or "").strip()
    profiles = payload.get("profiles")
    if not selected:
        raise DiscoveryError("authoritative akbs-member-ops.toml has no selected profile")
    if not isinstance(profiles, dict) or not isinstance(profiles.get(selected), dict):
        raise DiscoveryError(f"authoritative AKBS profile does not exist: {selected}")
    profile = profiles[selected]
    alias = profile.get("member_alias", payload.get("member_alias", ""))
    return _valid_alias(alias, label=f"AKBS profile {selected}"), f"akbs-member-ops.toml:{selected}"


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _relative_file(package_dir: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = (package_dir / relative).resolve()
    return candidate if candidate.is_file() and _within(candidate, package_dir.resolve()) else None


def _report_source(manifest: dict[str, Any], package_dir: Path) -> Path | None:
    files = manifest.get("files")
    display = files.get("display") if isinstance(files, dict) else None
    if isinstance(display, list):
        for value in display:
            candidate = _relative_file(package_dir, value)
            if candidate and candidate.name == "report_view.json":
                return candidate
    return _relative_file(package_dir, manifest.get("report_path"))


def _manifest_rows(
    root: Path, month_start: date, month_end: date, member_alias: str | None = None
) -> list[dict[str, Any]]:
    submitted = root / "submitted"
    if not submitted.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    pattern = f"*/{member_alias}/*/manifest.json" if member_alias else "*/*/*/manifest.json"
    for path in sorted(submitted.glob(pattern)):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        report_type = manifest.get("report_type")
        if report_type not in REPORT_KINDS or manifest.get("package_kind") != REPORT_KINDS[report_type]:
            continue
        try:
            member_alias = _valid_alias(manifest.get("member_alias"), label=f"manifest {path}")
            package_alias = path.parent.parent.name
            package_date = path.parent.parent.parent.name
            if package_alias != member_alias:
                continue
            if report_type == "daily":
                report_date = date.fromisoformat(str(manifest.get("date") or ""))
                if package_date != report_date.strftime("%Y%m%d"):
                    continue
                if not month_start <= report_date <= month_end:
                    continue
                report_key = (report_type, report_date.isoformat())
            else:
                week_range = str(manifest.get("week_range") or "")
                match = WEEK_RANGE_RE.fullmatch(week_range)
                if not match:
                    continue
                week_start, week_end = ymd(match.group(1)), ymd(match.group(2))
                manifest_date = date.fromisoformat(str(manifest.get("date") or ""))
                if package_date != manifest_date.strftime("%Y%m%d"):
                    continue
                if week_start > week_end or week_start > month_end or week_end < month_start:
                    continue
                report_key = (report_type, week_range)
        except (DiscoveryError, ValueError):
            continue
        run_id = manifest.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            continue
        rows.append(
            {
                "manifest": manifest,
                "path": path,
                "package_dir": path.parent,
                "member_alias": member_alias,
                "report_type": report_type,
                "report_key": report_key,
                "run_id": run_id.strip(),
                "root": root.resolve(),
            }
        )
    return rows


def _inferred_member_alias(rows: list[dict[str, Any]]) -> tuple[str, str]:
    aliases = sorted({row["member_alias"] for row in rows})
    if len(aliases) == 1:
        return aliases[0], "target-month submitted archive"
    if len(aliases) > 1:
        raise DiscoveryError(
            "target-month submitted archive contains multiple member aliases; "
            "set PERFORMANCE_MEMBER_ALIAS before discovering performance sources"
        )
    raise DiscoveryError(
        "no active AKBS member alias is configured and the target-month submitted archive "
        "cannot infer one; set PERFORMANCE_MEMBER_ALIAS"
    )


def _replaced_run_ids(rows: list[dict[str, Any]]) -> set[tuple[tuple[str, str], str]]:
    """Keep replacement identity local to its daily date or weekly range."""
    replaced: set[tuple[tuple[str, str], str]] = set()
    for row in rows:
        manifest = row["manifest"]
        for value in (
            manifest.get("replacement_for_run_id"),
            (manifest.get("supersedes") or {}).get("run_id")
            if isinstance(manifest.get("supersedes"), dict)
            else None,
        ):
            if isinstance(value, str) and value.strip():
                replaced.add((row["report_key"], value.strip()))
    return replaced


def _effective_sources(rows: list[dict[str, Any]], member_alias: str) -> tuple[list[Path], list[Path]]:
    member_rows = [row for row in rows if row["member_alias"] == member_alias]
    replaced = _replaced_run_ids(member_rows)
    leaves = [
        row for row in member_rows if (row["report_key"], row["run_id"]) not in replaced
    ]
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for key in sorted({row["report_key"] for row in leaves}):
        candidates = [row for row in leaves if row["report_key"] == key]
        primary_root = min(row["root_index"] for row in candidates)
        primary = [row for row in candidates if row["root_index"] == primary_root]
        chosen[key] = max(primary, key=lambda row: (row["run_id"], str(row["path"])))
    weekly: list[Path] = []
    daily: list[Path] = []
    for row in sorted(chosen.values(), key=lambda row: (row["report_key"], row["run_id"])):
        source = _report_source(row["manifest"], row["package_dir"])
        if source is None:
            continue
        (weekly if row["report_type"] == "weekly" else daily).append(source)
    return weekly, daily


def _is_default_personal_root(root: Path) -> bool:
    artifacts = RUNTIME_PATHS.codex_home / "artifacts"
    return root in {
        (artifacts / "akbs-member-ops").resolve(),
        (artifacts / "android-knowledge-intake").resolve(),
    }


def _weekly_facts(
    roots: list[Path], month_start: date, month_end: date, member_alias: str
) -> tuple[list[Path], list[str]]:
    selected: dict[str, Path] = {}
    warnings: list[str] = []
    for root in roots:
        personal_root = _is_default_personal_root(root)
        for path in sorted((root / "weekly-facts").glob("*.json")):
            match = WEEKLY_RE.fullmatch(path.name)
            if not match:
                continue
            try:
                start, end = ymd(match.group(1)), ymd(match.group(2))
            except ValueError:
                continue
            if start <= month_end and end >= month_start:
                if not personal_root:
                    try:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        fact_alias = _valid_alias(
                            payload.get("member_alias") if isinstance(payload, dict) else None,
                            label=f"weekly facts {path}",
                        )
                    except (DiscoveryError, OSError, json.JSONDecodeError):
                        warnings.append(
                            f"omitted unscoped or unsafe weekly facts from shared root: {path.resolve()}"
                        )
                        continue
                    if fact_alias != member_alias:
                        warnings.append(
                            f"omitted weekly facts for a different member from shared root: {path.resolve()}"
                        )
                        continue
                selected.setdefault(path.name, path.resolve())
    return [selected[name] for name in sorted(selected)], warnings


def discover(month: str, root: Path = DEFAULT_ROOT, legacy_root: Path = DEFAULT_LEGACY_ROOT) -> dict:
    month_start, month_end = parse_month(month)
    roots = []
    for candidate in (root, legacy_root):
        resolved = candidate.resolve()
        if resolved not in roots:
            roots.append(resolved)
    configured = configured_member_alias(RUNTIME_PATHS.codex_home)
    configured_alias = configured[0] if configured else None
    rows = []
    for root_index, source_root in enumerate(roots):
        for row in _manifest_rows(source_root, month_start, month_end, configured_alias):
            row["root_index"] = root_index
            rows.append(row)
    member_alias, member_alias_source = configured or _inferred_member_alias(rows)
    weekly_reports, daily_reports = _effective_sources(rows, member_alias)
    facts, fact_warnings = _weekly_facts(roots, month_start, month_end, member_alias)
    weekly = [*weekly_reports, *facts]

    result = {
        "month": month,
        "canonical_root": str(root.resolve()),
        "legacy_root": str(legacy_root.resolve()),
        "source_roots": [str(item) for item in roots],
        "member_alias": member_alias,
        "member_alias_source": member_alias_source,
        "weekly_sources": [str(path) for path in weekly],
        "daily_sources": [str(path) for path in daily_reports],
        "weekly_report_sources": [str(path) for path in weekly_reports],
        "weekly_fact_sources": [str(path) for path in facts],
        "daily_report_sources": [str(path) for path in daily_reports],
        "weekly_count": len(weekly),
        "daily_count": len(daily_reports),
        "weekly_report_count": len(weekly_reports),
        "weekly_fact_count": len(facts),
        "weekly_fact_omitted_count": len(fact_warnings),
        "weekly_fact_warnings": fact_warnings,
        "weekly_ready": bool(weekly),
    }
    if not weekly:
        result["action"] = (
            "No effective submitted weekly report or weekly facts found for the active member. Search "
            "referenced Codex tasks and explicit user-provided paths next; do not claim the month is "
            "missing from a filename-only Documents search."
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("month", help="YYYY-MM")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--legacy-root", type=Path, default=DEFAULT_LEGACY_ROOT)
    args = parser.parse_args()
    try:
        result = discover(args.month, args.root, args.legacy_root)
    except DiscoveryError as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2
    except ValueError:
        print(json.dumps({"status": "error", "error": "month must be YYYY-MM"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["weekly_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
