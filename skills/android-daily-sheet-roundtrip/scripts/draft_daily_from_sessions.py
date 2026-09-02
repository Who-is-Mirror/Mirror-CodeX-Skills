#!/usr/bin/env python3
"""Build a privacy-minimized Stage A daily draft with the current intake plugin APIs."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import importlib
import importlib.util
import inspect
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable


# Importing a cached plugin is read-only: never create/update __pycache__ there.
sys.dont_write_bytecode = True


DRAFT_SCHEMA = "codex-daily-sheet-draft-v1"
FACTS_SCHEMA = "akbs-daily-work-facts-v4"
SESSION_FIELDS = ["work_summary", "command_summary", "project_hint", "work_scope_hint"]
GMS_REQUIRED_CURRENT_FIELDS = (
    "gms_release_type",
    "gms_target",
    "gms_cycle_status",
    "gms_current_stage",
    "gms_self_test_round",
    "gms_self_test_result",
    "gms_submission_count",
    "gms_submission_result",
)
FORBIDDEN_OUTPUT_KEYS = {
    "messages",
    "message",
    "outcomes",
    "commands",
    "command",
    "cwd",
    "path",
    "thread_name",
    "raw",
    "raw_text",
    "session_text",
    "source_session_ids",
}
SUSPICIOUS_CUSTOMER_WORDS = {"at", "branch", "commit", "head"}
HASH_RE = re.compile(r"^[0-9a-f]{7,64}$", re.I)
ABSOLUTE_PATH_RE = re.compile(r"(?:^|\s)(?:/[A-Za-z0-9_.-]+/|[A-Za-z]:[\\/])")


class DraftError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise DraftError(message)


def _version_key(name: str) -> tuple[tuple[int, Any], ...]:
    parts = re.findall(r"\d+|[^\d]+", name)
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold()) for part in parts)


def default_ops_root() -> Path:
    override = os.environ.get("ANDROID_FRAMEWORK_OPS_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    home = Path(codex_home).expanduser() if codex_home else Path(__file__).resolve().parents[3]
    return home / "plugins" / "cache" / "android-framework-codex-suite" / "android-framework-ops"


def discover_latest_plugin(ops_root: Path | None = None) -> tuple[str, Path]:
    root = (ops_root or default_ops_root()).resolve()
    if not root.is_dir():
        fail(f"android-framework-ops 插件根目录不存在: {root}")
    candidates = []
    for child in root.iterdir():
        marker = child / "skills" / "android-knowledge-intake" / "scripts" / "akbs_intake"
        if child.is_dir() and marker.is_dir():
            candidates.append(child)
    if not candidates:
        fail(f"未发现可导入的 android-framework-ops 安装版本: {root}")
    selected = sorted(candidates, key=lambda path: _version_key(path.name))[-1]
    return selected.name, selected


@dataclasses.dataclass(frozen=True)
class PluginApi:
    version: str
    root: Path
    load_config: Callable[..., Any]
    configure_report_session_consent: Callable[..., Any]
    parse_sessions: Callable[..., Any]
    daily_work_scopes: Callable[..., Any]
    daily_work_items_from_scopes: Callable[..., Any]
    items_by_project: Callable[..., Any]
    overview_text: Callable[..., Any]
    infer_report_project: Callable[..., Any]
    build_daily_facts: Callable[..., Any]
    discover_patches: Callable[..., Any] | None = None


EXPECTED_PARAMETERS = {
    "load_config": {"profile_override"},
    "configure_report_session_consent": {"config", "dates", "granted", "fields"},
    "parse_sessions": {"config", "dates"},
    "daily_work_scopes": {"sessions", "patches"},
    "daily_work_items_from_scopes": {"scopes"},
    "items_by_project": {"sessions", "patches"},
    "overview_text": {"report_type", "items", "patches"},
    "infer_report_project": {"report_type", "summary", "items", "sessions", "patches"},
    "build_daily_facts": {
        "report_date",
        "explicit_path",
        "synthetic",
        "project_items",
        "daily_work_items",
        "project_customers",
        "inferred_scopes",
    },
}


def validate_api_contract(api: PluginApi, *, patch_discovery: bool = False) -> None:
    for name, expected in EXPECTED_PARAMETERS.items():
        value = getattr(api, name, None)
        if not callable(value):
            fail(f"current plugin API 不兼容：缺少可调用的 {name}")
        try:
            actual = set(inspect.signature(value).parameters)
        except (TypeError, ValueError) as exc:
            fail(f"current plugin API 不兼容：无法检查 {name} 签名: {exc}")
        missing = expected - actual
        if missing:
            fail(f"current plugin API 不兼容：{name} 缺少参数 {', '.join(sorted(missing))}")
    if patch_discovery:
        if not callable(api.discover_patches):
            fail("current plugin API 不兼容：显式 patch discovery 需要 discover_patches")
        actual = set(inspect.signature(api.discover_patches).parameters)
        missing = {"config", "sessions", "start", "end"} - actual
        if missing:
            fail("current plugin API 不兼容：discover_patches 缺少参数 " + ", ".join(sorted(missing)))


def _import_current_module(name: str, plugin_root: Path) -> Any:
    module = importlib.import_module(name)
    module_file = Path(str(getattr(module, "__file__", ""))).resolve()
    try:
        module_file.relative_to(plugin_root.resolve())
    except ValueError:
        fail(f"current plugin API 导入漂移：{name} 来自非当前插件位置")
    return module


def load_current_plugin_api(*, patch_discovery: bool = False, ops_root: Path | None = None) -> PluginApi:
    version, plugin_root = discover_latest_plugin(ops_root)
    scripts_root = plugin_root / "skills" / "android-knowledge-intake" / "scripts"
    plugin_lib = plugin_root / "lib"
    for path in (scripts_root, plugin_lib):
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))

    config = _import_current_module("akbs_intake.config", plugin_root)
    privacy = _import_current_module("akbs_intake.session_privacy", plugin_root)
    sessions = _import_current_module("akbs_intake.report_sessions", plugin_root)
    summary = _import_current_module("akbs_intake.reports.session_summary", plugin_root)
    identity = _import_current_module("akbs_intake.reports.identity", plugin_root)
    facts = _import_current_module("akbs_intake.reports.daily_facts", plugin_root)

    discover_patches = None
    if patch_discovery:
        entrypoint = scripts_root / "android_knowledge_intake.py"
        spec = importlib.util.spec_from_file_location(f"akbs_current_intake_{version.replace('.', '_')}", entrypoint)
        if spec is None or spec.loader is None:
            fail("无法加载 current plugin patch discovery 入口")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        discover_patches = getattr(module, "discover_patches", None)

    api = PluginApi(
        version=version,
        root=plugin_root,
        load_config=config.load_config,
        configure_report_session_consent=privacy.configure_report_session_consent,
        parse_sessions=sessions.parse_sessions,
        daily_work_scopes=summary.daily_work_scopes,
        daily_work_items_from_scopes=summary.daily_work_items_from_scopes,
        items_by_project=summary.items_by_project,
        overview_text=summary.overview_text,
        infer_report_project=identity.infer_report_project,
        build_daily_facts=facts.build_daily_facts,
        discover_patches=discover_patches,
    )
    validate_api_contract(api, patch_discovery=patch_discovery)
    return api


def parse_report_date(value: str, *, today: dt.date | None = None) -> dt.date:
    try:
        report_date = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise DraftError("--date 必须是有效的 YYYY-MM-DD") from exc
    if report_date.isoformat() != value:
        fail("--date 必须是有效的 YYYY-MM-DD")
    if report_date > (today or dt.date.today()):
        fail(f"未来日期被拒绝: {report_date.isoformat()}")
    return report_date


def _project_customers(project_payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for item in project_payload.get("project_customers", []):
        if not isinstance(item, dict):
            continue
        project = str(item.get("project") or "").strip()
        customer = str(item.get("customer_name") or "").strip()
        if not project or not customer:
            continue
        context = {"customer_name": customer}
        downstream = str(item.get("downstream_customer") or "").strip()
        if downstream:
            context["downstream_customer"] = downstream
        result[project] = context
    return result


def _facts_payload(report_date: dt.date, result: Any) -> dict[str, Any]:
    required = ("projects", "documents", "standalone_work", "tomorrow_plan", "evidence")
    missing = [name for name in required if not hasattr(result, name)]
    if missing:
        fail("current plugin DailyFactsResult 不兼容：缺少 " + ", ".join(missing))
    return {
        "schema": FACTS_SCHEMA,
        "report_date": report_date.isoformat(),
        "projects": result.projects,
        "documents": result.documents,
        "standalone_work": result.standalone_work,
        "tomorrow_plan": result.tomorrow_plan,
    }


def _suspicious_customer(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.casefold()
    tokens = re.findall(r"[A-Za-z0-9]+", lowered)
    return (
        lowered in SUSPICIOUS_CUSTOMER_WORDS
        or bool(HASH_RE.fullmatch(lowered))
        or any(token in SUSPICIOUS_CUSTOMER_WORDS for token in tokens)
        and any(HASH_RE.fullmatch(token) for token in tokens)
    )


def collect_unresolved(facts: dict[str, Any], evidence: dict[str, Any]) -> list[dict[str, str]]:
    unresolved: list[dict[str, str]] = []

    def add(code: str, scope: str, detail: str) -> None:
        row = {"code": code, "scope": scope, "detail": detail}
        if row not in unresolved:
            unresolved.append(row)

    for field in evidence.get("missing_fields", []) if isinstance(evidence, dict) else []:
        add("plugin_missing_field", str(field), "current plugin 标记字段缺失")
    for index, scope in enumerate(evidence.get("scope_inference", []) if isinstance(evidence, dict) else []):
        if isinstance(scope, dict) and scope.get("conflict"):
            add("scope_inference_conflict", f"scope_inference[{index}]", "current plugin 标记范围推理冲突")

    for index, row in enumerate(facts.get("projects", [])):
        scope = f"projects[{index}]"
        project = str(row.get("project") or "").strip()
        customer = str(row.get("customer") or row.get("customer_name") or "").strip()
        if not project:
            add("missing_project", scope, "项目代码缺失")
        if not customer or customer in {"需补充", "需补充客户", "unknown", "UNKNOWN"}:
            add("missing_customer", scope, "直接客户缺失")
        for field in ("customer", "customer_name", "downstream_customer"):
            if row.get(field) and _suspicious_customer(row[field]):
                add("git_prose_customer", f"{scope}.{field}", "疑似把 Git prose、branch/commit/head 或 hash 当成客户")
        work_type = str(row.get("work_type") or "").strip()
        if work_type not in {"Patch", "App", "GMS", "Doc", "Other"}:
            add("missing_or_invalid_work_type", scope, "工作类型缺失或非法")
        if work_type == "App" and not str(row.get("app_name") or "").strip():
            add("missing_app_name", scope, "App 名称缺失")
        if work_type == "GMS":
            for field in GMS_REQUIRED_CURRENT_FIELDS:
                if row.get(field) in (None, ""):
                    add("missing_gms_cycle_field", f"{scope}.{field}", "GMS 周期字段无法无损确认")

    for index, row in enumerate(facts.get("documents", [])):
        if not str(row.get("document_name") or "").strip():
            add("missing_document_name", f"documents[{index}]", "文档名称缺失")
    for index, row in enumerate(facts.get("standalone_work", [])):
        if not str(row.get("work_name") or "").strip():
            add("missing_work_name", f"standalone_work[{index}]", "独立工作名称缺失")
    if not any(facts.get(name) for name in ("projects", "documents", "standalone_work")):
        add("no_daily_work", "facts", "指定日期没有可形成日报草稿的工作范围")
    return unresolved


def _minimal_evidence(plugin_evidence: dict[str, Any], *, patch_discovery: bool, patch_count: int) -> dict[str, Any]:
    allowed_scalars = (
        "schema",
        "report_date",
        "source",
        "project_count",
        "document_count",
        "standalone_work_count",
        "work_scope_count",
        "tomorrow_plan_scope_count",
        "facts_sha256",
    )
    result = {key: plugin_evidence[key] for key in allowed_scalars if key in plugin_evidence}
    result["missing_fields"] = list(plugin_evidence.get("missing_fields", []))
    result["scope_inference"] = list(plugin_evidence.get("scope_inference", []))
    result["patch_discovery"] = {"enabled": patch_discovery, "patch_count": patch_count}
    return result


def assert_private_envelope(value: Any, *, key: str = "") -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            if child_key.casefold() in FORBIDDEN_OUTPUT_KEYS:
                fail(f"隐私门禁拒绝输出字段: {child_key}")
            assert_private_envelope(child, key=child_key)
    elif isinstance(value, list):
        for child in value:
            assert_private_envelope(child, key=key)
    elif isinstance(value, str) and ABSOLUTE_PATH_RE.search(value):
        fail(f"隐私门禁拒绝输出疑似原始路径（字段 {key or '<root>'}）")


def build_draft(
    *,
    profile: str,
    report_date: dt.date,
    session_consent: bool,
    patch_discovery: bool,
    api_loader: Callable[..., PluginApi] = load_current_plugin_api,
) -> dict[str, Any]:
    if not session_consent:
        fail("缺少本次 --session-consent；未授权时不会加载插件配置或读取会话")
    api = api_loader(patch_discovery=patch_discovery)
    validate_api_contract(api, patch_discovery=patch_discovery)
    try:
        config, _loaded = api.load_config(profile)
        dates = {report_date}
        fields = [*SESSION_FIELDS, *(["patch_discovery"] if patch_discovery else [])]
        api.configure_report_session_consent(config, dates, granted=True, fields=fields)
        sessions = list(api.parse_sessions(config, dates))
        patches = list(api.discover_patches(config, sessions, report_date, report_date)) if patch_discovery else []
        scopes = api.daily_work_scopes(sessions, patches)
        daily_items = api.daily_work_items_from_scopes(scopes)
        project_items = api.items_by_project(sessions, patches)
        overview = api.overview_text("daily", project_items, patches)
        _report_project, project_payload = api.infer_report_project(
            "daily", overview, project_items, sessions, patches
        )
        customers = _project_customers(project_payload)
        result = api.build_daily_facts(
            report_date,
            explicit_path="",
            synthetic=False,
            project_items=project_items,
            daily_work_items=daily_items,
            project_customers=customers,
            inferred_scopes=scopes,
        )
    except DraftError:
        raise
    except (Exception, SystemExit) as exc:
        fail(f"current plugin Stage A 调用安全失败: {exc}")

    facts = _facts_payload(report_date, result)
    plugin_evidence = result.evidence if isinstance(result.evidence, dict) else {}
    evidence = _minimal_evidence(plugin_evidence, patch_discovery=patch_discovery, patch_count=len(patches))
    unresolved = collect_unresolved(facts, evidence)
    raw_count = len(sessions)
    unique_ids = {str(getattr(item, "session_id", "") or "") for item in sessions}
    unique_ids.discard("")
    unique_count = len(unique_ids) + sum(1 for item in sessions if not getattr(item, "session_id", ""))
    warnings = []
    if raw_count != unique_count:
        warnings.append("parse_sessions 返回重复 session_id；计数已说明，归并仍由 current plugin 负责")
    warnings.append("未读取或学习客户 registry；Stage B customer guard 保持权威")
    envelope = {
        "schema": DRAFT_SCHEMA,
        "plugin_version": api.version,
        "report_date": report_date.isoformat(),
        "session_count": {
            "raw_parsed": raw_count,
            "unique_session_ids": unique_count,
            "deduplication_note": "仅统计唯一 session_id；全部 parse_sessions 结果原样交给 current plugin 归并",
        },
        "facts": facts,
        "evidence": evidence,
        "unresolved": unresolved,
        "warnings": warnings,
    }
    assert_private_envelope(envelope)
    return envelope


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
    parser = argparse.ArgumentParser(description="Generate a Stage A daily sheet draft from current plugin APIs")
    parser.add_argument("--profile", required=True, help="case-sensitive profile name from report config")
    parser.add_argument("--date", required=True, help="report date YYYY-MM-DD")
    parser.add_argument("--session-consent", action="store_true", help="fresh consent for this exact run/date")
    parser.add_argument("--patch-discovery", action="store_true", help="explicitly enable current plugin patch discovery")
    parser.add_argument("--output", required=True, help="new or identical draft JSON path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report_date = parse_report_date(args.date)
    draft = build_draft(
        profile=args.profile,
        report_date=report_date,
        session_consent=args.session_consent,
        patch_discovery=args.patch_discovery,
    )
    changed = write_json_idempotent(Path(args.output), draft)
    print(json.dumps({
        "status": "PASS" if not draft["unresolved"] else "UNRESOLVED",
        "output": str(Path(args.output).expanduser().resolve()),
        "written": changed,
        "plugin_version": draft["plugin_version"],
        "session_count": draft["session_count"],
        "unresolved": draft["unresolved"],
    }, ensure_ascii=False, indent=2))
    return 0 if not draft["unresolved"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DraftError as exc:
        print(f"draft_daily_from_sessions: {exc}", file=sys.stderr)
        raise SystemExit(1)
