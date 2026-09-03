from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SCRIPT = SCRIPT_DIR / "session_review_cache.py"
SPEC = importlib.util.spec_from_file_location("session_review_cache", SCRIPT)
assert SPEC and SPEC.loader
cache = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cache)


def make_plugin(root: Path, version: str = "1.2.3", marker: str = "v1") -> Path:
    plugin = root / version
    files = (
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
    for relative in files:
        path = plugin / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{relative}:{marker}\n", encoding="utf-8")
    return plugin


def index(*tasks: tuple[str, int, str]) -> dict:
    return {
        "schema": cache.INDEX_SCHEMA,
        "report_date": "2026-09-02",
        "listing_crossed_lower_boundary": True,
        "archived_checked": True,
        "tasks": [
            {"thread_id": thread_id, "host_id": "local", "kind": "codex", "updated_at": updated_at, "status": status, "latest_turn_id": f"turn-{updated_at}"}
            for thread_id, updated_at, status in tasks
        ],
    }


def compact(name: str) -> dict:
    return {
        "include": True,
        "session_fields": [{
            "work_summary": [f"完成{name}功能"],
            "command_summary": ["执行定向回归测试"],
            "project_hint": "TVA10A0R",
            "work_scope_hint": "Patch",
            "outcome_summary": [f"{name}已验证"],
            "status_evidence": ["已完成"],
        }],
        "key_points": [],
        "dependencies": [],
        "tomorrow_plan_evidence": [],
        "source_turn_ids": [f"turn-{name}"],
    }


def evidence(fingerprint: dict, task_index: dict, thread_ids: set[str] | None = None) -> dict:
    normalized = cache.normalize_index(task_index, "2026-09-02")
    selected = thread_ids if thread_ids is not None else {item["thread_id"] for item in normalized["tasks"]}
    return {
        "schema": cache.EVIDENCE_SCHEMA,
        "report_date": "2026-09-02",
        "plugin_fingerprint": fingerprint,
        "tasks": [
            {"thread_id": item["thread_id"], "host_id": item["host_id"], "revision": item["revision"], "compact_evidence": compact(item["thread_id"])}
            for item in normalized["tasks"] if item["thread_id"] in selected
        ],
    }


def facts() -> dict:
    return {
        "schema": cache.FACTS_SCHEMA,
        "report_date": "2026-09-02",
        "projects": [],
        "documents": [],
        "standalone_work": [{"work_type": "Other", "work_name": "工具建设", "work_items": []}],
        "tomorrow_plan": {"projects": [], "documents": [], "standalone_work": []},
    }


class SessionReviewCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.temporary.name)
        make_plugin(self.root)
        self.fingerprint = cache.plugin_fingerprint(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def manifest(self, task_index: dict, task_evidence: dict) -> dict:
        normalized = cache.normalize_index(task_index, "2026-09-02")
        return cache.finalize_manifest(normalized, task_evidence, facts(), self.fingerprint)

    def test_first_run_reviews_all_and_unchanged_run_reuses_all(self):
        raw_index = index(("a", 1, "completed"), ("b", 2, "active"))
        normalized = cache.normalize_index(raw_index, "2026-09-02")
        first = cache.build_plan(normalized, self.fingerprint, None)
        self.assertTrue(first["full_review"])
        self.assertEqual(first["review_thread_ids"], ["a", "b"])
        old_evidence = evidence(self.fingerprint, raw_index)
        second = cache.build_plan(normalized, self.fingerprint, self.manifest(raw_index, old_evidence))
        self.assertFalse(second["full_review"])
        self.assertEqual(second["review_thread_ids"], [])
        self.assertEqual(second["reuse_thread_ids"], ["a", "b"])

    def test_only_changed_and_new_tasks_are_reviewed(self):
        old_index = index(("a", 1, "completed"), ("b", 2, "active"))
        old_evidence = evidence(self.fingerprint, old_index)
        previous = self.manifest(old_index, old_evidence)
        new_index = index(("a", 1, "completed"), ("b", 3, "active"), ("c", 4, "completed"))
        plan = cache.build_plan(cache.normalize_index(new_index, "2026-09-02"), self.fingerprint, previous)
        self.assertEqual(plan["reuse_thread_ids"], ["a"])
        self.assertEqual(plan["review_thread_ids"], ["b", "c"])

    def test_plugin_content_change_forces_full_review(self):
        raw_index = index(("a", 1, "completed"))
        old_evidence = evidence(self.fingerprint, raw_index)
        previous = self.manifest(raw_index, old_evidence)
        make_plugin(self.root, marker="changed")
        changed = cache.plugin_fingerprint(self.root)
        plan = cache.build_plan(cache.normalize_index(raw_index, "2026-09-02"), changed, previous)
        self.assertTrue(plan["full_review"])
        self.assertEqual(plan["reason_codes"], ["daily_plugin_fingerprint_changed"])

    def test_any_plugin_tree_file_change_forces_full_review(self):
        raw_index = index(("a", 1, "completed"))
        previous = self.manifest(raw_index, evidence(self.fingerprint, raw_index))
        extra = self.root / "1.2.3" / "references" / "new-rule.md"
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("new plugin rule\n", encoding="utf-8")
        changed = cache.plugin_fingerprint(self.root)
        plan = cache.build_plan(cache.normalize_index(raw_index, "2026-09-02"), changed, previous)
        self.assertTrue(plan["full_review"])
        self.assertEqual(plan["review_thread_ids"], ["a"])

    def test_assemble_reuses_unchanged_and_accepts_exact_delta(self):
        old_index = index(("a", 1, "completed"), ("b", 2, "active"))
        old_evidence = evidence(self.fingerprint, old_index)
        previous = self.manifest(old_index, old_evidence)
        new_index = index(("a", 1, "completed"), ("b", 3, "completed"))
        normalized = cache.normalize_index(new_index, "2026-09-02")
        plan = cache.build_plan(normalized, self.fingerprint, previous)
        delta = evidence(self.fingerprint, new_index, {"b"})
        assembled = cache.assemble_evidence(plan, normalized, delta, old_evidence)
        self.assertEqual([item["thread_id"] for item in assembled["tasks"]], ["a", "b"])
        manifest = cache.finalize_manifest(normalized, assembled, facts(), self.fingerprint)
        self.assertEqual(len(manifest["tasks"]), 2)

    def test_assemble_rejects_missing_changed_task_and_revision_drift(self):
        raw_index = index(("a", 1, "active"))
        normalized = cache.normalize_index(raw_index, "2026-09-02")
        plan = cache.build_plan(normalized, self.fingerprint, None)
        empty_delta = {"schema": cache.EVIDENCE_SCHEMA, "report_date": "2026-09-02", "plugin_fingerprint": self.fingerprint, "tasks": []}
        with self.assertRaisesRegex(cache.CacheError, "review_thread_ids"):
            cache.assemble_evidence(plan, normalized, empty_delta, None)
        drifted = evidence(self.fingerprint, index(("a", 2, "active")))
        with self.assertRaisesRegex(cache.CacheError, "revision"):
            cache.assemble_evidence(plan, normalized, drifted, None)

    def test_compact_evidence_rejects_raw_paths_and_generic_fallback(self):
        unsafe = compact("a")
        unsafe["session_fields"][0]["work_summary"] = ["读取 /home/mirror/work/file"]
        with self.assertRaisesRegex(cache.CacheError, "路径"):
            cache.validate_compact_evidence(unsafe, thread_id="a")
        generic = compact("a")
        generic["session_fields"][0]["command_summary"] = ["修改或适配相关实现"]
        with self.assertRaisesRegex(cache.CacheError, "通用兜底"):
            cache.validate_compact_evidence(generic, thread_id="a")


if __name__ == "__main__":
    unittest.main()
