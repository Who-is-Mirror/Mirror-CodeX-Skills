from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "facts_to_sheet_rows.py"
SPEC = importlib.util.spec_from_file_location("facts_to_sheet_rows", SCRIPT)
assert SPEC and SPEC.loader
rows_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rows_module)


def item(name: str = "实现转换") -> dict:
    return {
        "name": name,
        "did": ["实现确定性映射", "补充拒绝门禁"],
        "how": ["对照 v4 facts 合约"],
        "result": "离线测试通过",
        "status": "已完成",
    }


def empty_plan() -> dict:
    return {"projects": [], "documents": [], "standalone_work": []}


def base_facts() -> dict:
    return {
        "schema": rows_module.FACTS_SCHEMA,
        "report_date": "2026-09-01",
        "projects": [],
        "documents": [],
        "standalone_work": [],
        "tomorrow_plan": empty_plan(),
    }


def project(work_type: str = "App") -> dict:
    result = {
        "project": "TVE1215M",
        "customer": "浪潮",
        "downstream_customer": "杭研中屏",
        "work_type": work_type,
        "today_topic": "固化日报闭环",
        "current_result": "两阶段门禁已完成",
        "work_items": [item()],
        "key_points": ["Stage A 写表后停止"],
        "dependencies": ["等待用户完成表格修改"],
    }
    if work_type == "App":
        result["app_name"] = "FactoryTestNew"
    return result


class FactsToRowsTests(unittest.TestCase):
    def test_app_header_identity_three_task_rows_and_overview_without_status(self):
        facts = base_facts()
        facts["projects"] = [project("App")]
        converted = rows_module.convert_facts_to_rows(facts)
        self.assertEqual(converted["schema"], rows_module.ROWS_SCHEMA)
        self.assertEqual(converted["header"], rows_module.HEADER)
        self.assertEqual(converted["rows"][0]["A"], "TVE1215M / 浪潮 → 杭研中屏")
        self.assertEqual(converted["rows"][0]["B"], "App / FactoryTestNew")
        self.assertEqual(converted["rows"][0]["C"], "今日概况")
        self.assertEqual(converted["rows"][0]["F"], "")
        self.assertEqual(converted["rows"][1]["F"], "")
        task_rows = converted["rows"][2:5]
        self.assertEqual([entry["D"] for entry in task_rows], ["做了什么", "怎么做的", "结果"])
        self.assertEqual([entry["F"] for entry in task_rows], ["", "", "已完成"])
        self.assertTrue(task_rows[0]["E"].startswith("- "))

    def test_standalone_other_and_empty_sections_write_none(self):
        facts = base_facts()
        facts["standalone_work"] = [{
            "work_type": "Other",
            "work_name": "团队工具维护",
            "today_topic": "维护日报工具",
            "current_result": "工具可离线验证",
            "work_items": [item("维护工具")],
            "key_points": [],
            "dependencies": [],
        }]
        converted = rows_module.convert_facts_to_rows(facts)
        self.assertEqual(converted["rows"][0]["A"], "Other")
        self.assertEqual(converted["rows"][0]["B"], "团队工具维护")
        self.assertEqual([entry["E"] for entry in converted["rows"][-3:]], ["无。", "无。", "无。"])

    def test_non_project_doc_and_project_doc_other_are_distinct(self):
        facts = base_facts()
        facts["projects"] = [project("Doc"), {**project("Other"), "project": "TVE1216M"}]
        facts["documents"] = [{
            "work_type": "Doc",
            "document_name": "日报闭环设计文档",
            "today_topic": "完善说明",
            "current_result": "文档可评审",
            "work_items": [item("更新文档")],
            "key_points": [],
            "dependencies": [],
        }]
        converted = rows_module.convert_facts_to_rows(facts)
        starts = converted["format_hints"]["scope_start_rows"]
        values = converted["grid_data"]["values"]
        self.assertEqual(values[starts[0] - 1][1], "Doc")
        self.assertEqual(values[starts[1] - 1][1], "Other")
        self.assertEqual(values[starts[2] - 1][0:2], ["Other", "Doc / 日报闭环设计文档"])
        self.assertEqual(len(converted["format_hints"]["separator_rows"]), 2)

    def test_key_points_dependencies_and_matching_tomorrow_plan(self):
        facts = base_facts()
        scope = project("Patch")
        facts["projects"] = [scope]
        facts["tomorrow_plan"]["projects"] = [{
            "project": scope["project"],
            "customer": scope["customer"],
            "downstream_customer": scope["downstream_customer"],
            "work_type": "Patch",
            "plan_items": ["执行 Stage B", "校验 exact package"],
        }]
        converted = rows_module.convert_facts_to_rows(facts)
        specials = {entry["C"]: entry["E"] for entry in converted["rows"] if entry["C"] in {"重点说明", "依赖 / 需协调", "明日计划"}}
        self.assertEqual(specials["重点说明"], "- Stage A 写表后停止")
        self.assertEqual(specials["依赖 / 需协调"], "- 等待用户完成表格修改")
        self.assertEqual(specials["明日计划"], "- 执行 Stage B\n- 校验 exact package")

    def test_gms_fails_closed(self):
        facts = base_facts()
        gms = project("GMS")
        gms.update({
            "gms_release_type": "IR",
            "gms_target": "Android 14",
            "gms_cycle_status": "active",
            "gms_current_stage": "self_test",
            "gms_self_test_round": 1,
            "gms_self_test_result": "passed",
            "gms_submission_count": 0,
            "gms_submission_result": "not_submitted",
        })
        facts["projects"] = [gms]
        with self.assertRaisesRegex(rows_module.RowsError, "GMS.*不能无损映射"):
            rows_module.convert_facts_to_rows(facts)

    def test_automatic_draft_or_candidate_always_refuses_rows(self):
        facts = base_facts()
        facts["projects"] = [project("Patch")]
        for schema in (rows_module.DRAFT_SCHEMA, rows_module.CANDIDATE_SCHEMA, rows_module.TASK_EVIDENCE_SCHEMA):
            envelope = {
                "schema": schema,
                "plugin_version": "1.0.168",
                "facts": facts,
                "unresolved": [],
            }
            with self.assertRaisesRegex(rows_module.RowsError, "自动会话候选"):
                rows_module.convert_facts_to_rows(envelope)

    def test_path_placeholders_and_absolute_paths_refuse_rows(self):
        for unsafe in ("核对 [PATH] 下的实现", "检查 <workspace_root> 内容", "读取 /home/mirror/work/file"):
            facts = base_facts()
            scope = project("Patch")
            scope["work_items"][0]["did"] = [unsafe]
            facts["projects"] = [scope]
            with self.assertRaisesRegex(rows_module.RowsError, "路径"):
                rows_module.convert_facts_to_rows(facts)

    def test_generic_fallback_prose_refuses_rows(self):
        facts = base_facts()
        scope = project("Patch")
        scope["work_items"][0]["how"] = ["修改或适配相关实现"]
        facts["projects"] = [scope]
        with self.assertRaisesRegex(rows_module.RowsError, "通用兜底"):
            rows_module.convert_facts_to_rows(facts)

    def test_does_not_mutate_input_and_is_deterministic(self):
        facts = base_facts()
        facts["projects"] = [project("Patch")]
        original = copy.deepcopy(facts)
        first = rows_module.convert_facts_to_rows(facts)
        second = rows_module.convert_facts_to_rows(facts)
        self.assertEqual(facts, original)
        self.assertEqual(first, second)
        self.assertEqual(len(first["source_facts_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
