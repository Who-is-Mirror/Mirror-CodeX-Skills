from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "draft_daily_from_sessions.py"
SPEC = importlib.util.spec_from_file_location("draft_daily_from_sessions", SCRIPT)
assert SPEC and SPEC.loader
draft = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = draft
SPEC.loader.exec_module(draft)


class Session:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.messages = ["SECRET RAW SESSION BODY"]
        self.outcomes = ["SECRET RAW OUTCOME"]
        self.commands = ["rm --raw-secret"]
        self.cwd = "/secret/source/path"
        self.thread_name = "SECRET THREAD"


def fake_api(*, customer: str = "工厂", downstream: str = "", conflict: bool = False):
    calls: list[str] = []

    def load_config(profile_override):
        calls.append(f"load_config:{profile_override}")
        return {"profile": profile_override}, []

    def configure_report_session_consent(config, dates, *, granted, fields):
        calls.append("consent")
        config["consent"] = (granted, tuple(fields), tuple(dates))

    def parse_sessions(config, dates):
        calls.append("parse_sessions")
        assert config["consent"][0] is True
        return [Session("same-session"), Session("same-session")]

    def daily_work_scopes(sessions, patches):
        calls.append("daily_work_scopes")
        return [{
            "project": "TVE1215M",
            "work_type": "Patch",
            "work_items": [],
            "inference_basis": ["explicit_project_context"],
            "inference_conflict": conflict,
        }]

    def daily_work_items_from_scopes(scopes):
        calls.append("daily_work_items_from_scopes")
        return {"TVE1215M": []}

    def items_by_project(sessions, patches):
        calls.append("items_by_project")
        return {"TVE1215M": [("实现日报闭环", "已完成")]}

    def overview_text(report_type, items, patches):
        calls.append("overview_text")
        return "今天实现日报闭环，无patch。"

    def infer_report_project(report_type, summary, items, sessions, patches):
        calls.append("infer_report_project")
        identity = {"project": "TVE1215M", "customer_name": customer}
        if downstream:
            identity["downstream_customer"] = downstream
        return "TVE1215M", {"project_customers": [identity]}

    def build_daily_facts(
        report_date,
        *,
        explicit_path="",
        synthetic=False,
        project_items=None,
        daily_work_items=None,
        project_customers=None,
        inferred_scopes=None,
    ):
        calls.append("build_daily_facts")
        context = project_customers["TVE1215M"]
        project = {
            "project": "TVE1215M",
            "customer": context["customer_name"],
            "work_type": "Patch",
            "today_topic": "固化日报闭环",
            "current_result": "两阶段门禁已实现",
            "work_items": [{
                "name": "实现转换链路",
                "did": ["实现草稿转换"],
                "how": ["调用 current plugin API"],
                "result": "离线测试可验证",
                "status": "待验证",
            }],
            "key_points": [],
            "dependencies": [],
        }
        if context.get("downstream_customer"):
            project["downstream_customer"] = context["downstream_customer"]
        evidence = {
            "schema": "akbs-daily-fact-sources-v4",
            "report_date": report_date.isoformat(),
            "source": "session_scope_inference",
            "project_count": 1,
            "document_count": 0,
            "standalone_work_count": 0,
            "work_scope_count": 1,
            "tomorrow_plan_scope_count": 0,
            "missing_fields": [],
            "scope_inference": [{
                "project": "TVE1215M",
                "work_type": "Patch",
                "basis": ["explicit_project_context"],
                "conflict": conflict,
            }],
            "facts_sha256": "abc123",
        }
        return types.SimpleNamespace(
            projects=[project],
            documents=[],
            standalone_work=[],
            tomorrow_plan={"projects": [], "documents": [], "standalone_work": []},
            evidence=evidence,
        )

    def discover_patches(config, sessions, start, end):
        calls.append("discover_patches")
        return []

    api = draft.PluginApi(
        version="9.9.9-fixture",
        root=Path("/fixture/plugin"),
        load_config=load_config,
        configure_report_session_consent=configure_report_session_consent,
        parse_sessions=parse_sessions,
        daily_work_scopes=daily_work_scopes,
        daily_work_items_from_scopes=daily_work_items_from_scopes,
        items_by_project=items_by_project,
        overview_text=overview_text,
        infer_report_project=infer_report_project,
        build_daily_facts=build_daily_facts,
        discover_patches=discover_patches,
    )
    return api, calls


class DraftDailyTests(unittest.TestCase):
    report_date = dt.date(2026, 9, 1)

    def test_discovers_latest_version_and_checks_contract(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary:
            root = Path(temporary)
            for version in ("1.0.9", "1.0.10"):
                (root / version / "skills" / "android-knowledge-intake" / "scripts" / "akbs_intake").mkdir(parents=True)
            version, path = draft.discover_latest_plugin(root)
            self.assertEqual(version, "1.0.10")
            self.assertEqual(path.name, "1.0.10")

        api, _ = fake_api()
        draft.validate_api_contract(api)
        incompatible = dataclasses.replace(api, overview_text=lambda report_type: "bad")
        with self.assertRaisesRegex(draft.DraftError, "overview_text 缺少参数"):
            draft.validate_api_contract(incompatible)

    def test_requires_consent_before_loader_or_session_read(self):
        called = False

        def forbidden_loader(**_kwargs):
            nonlocal called
            called = True
            raise AssertionError("loader must not run")

        with self.assertRaisesRegex(draft.DraftError, "session-consent"):
            draft.build_draft(
                profile="Mirror",
                report_date=self.report_date,
                session_consent=False,
                patch_discovery=False,
                api_loader=forbidden_loader,
            )
        self.assertFalse(called)

    def test_uses_current_api_chain_and_patch_discovery_is_opt_in(self):
        api, calls = fake_api()
        envelope = draft.build_draft(
            profile="Mirror",
            report_date=self.report_date,
            session_consent=True,
            patch_discovery=False,
            api_loader=lambda **_kwargs: api,
        )
        self.assertEqual(envelope["plugin_version"], "9.9.9-fixture")
        self.assertEqual(envelope["session_count"]["raw_parsed"], 2)
        self.assertEqual(envelope["session_count"]["unique_session_ids"], 1)
        self.assertNotIn("discover_patches", calls)
        self.assertEqual(calls[:3], ["load_config:Mirror", "consent", "parse_sessions"])
        self.assertEqual(envelope["unresolved"], [])

    def test_git_prose_customer_is_unresolved_and_blocks_rows(self):
        api, _ = fake_api(customer="at", downstream="4782637")
        envelope = draft.build_draft(
            profile="Mirror",
            report_date=self.report_date,
            session_consent=True,
            patch_discovery=False,
            api_loader=lambda **_kwargs: api,
        )
        codes = {item["code"] for item in envelope["unresolved"]}
        self.assertIn("git_prose_customer", codes)

    def test_envelope_omits_raw_session_body_commands_thread_and_paths(self):
        api, _ = fake_api()
        envelope = draft.build_draft(
            profile="Mirror",
            report_date=self.report_date,
            session_consent=True,
            patch_discovery=False,
            api_loader=lambda **_kwargs: api,
        )
        serialized = json.dumps(envelope, ensure_ascii=False)
        for secret in ("SECRET RAW SESSION BODY", "SECRET RAW OUTCOME", "rm --raw-secret", "/secret/source/path", "SECRET THREAD"):
            self.assertNotIn(secret, serialized)
        for forbidden_key in draft.FORBIDDEN_OUTPUT_KEYS:
            self.assertNotIn(f'"{forbidden_key}"', serialized)

    def test_idempotent_output_and_refuses_different_overwrite(self):
        api, _ = fake_api()
        envelope = draft.build_draft(
            profile="Mirror",
            report_date=self.report_date,
            session_consent=True,
            patch_discovery=False,
            api_loader=lambda **_kwargs: api,
        )
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary:
            target = Path(temporary) / "draft.json"
            self.assertTrue(draft.write_json_idempotent(target, envelope))
            self.assertFalse(draft.write_json_idempotent(target, envelope))
            changed = json.loads(json.dumps(envelope))
            changed["plugin_version"] = "different"
            with self.assertRaisesRegex(draft.DraftError, "内容不同，拒绝覆盖"):
                draft.write_json_idempotent(target, changed)


if __name__ == "__main__":
    unittest.main()
