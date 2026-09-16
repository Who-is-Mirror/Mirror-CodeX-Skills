#!/usr/bin/env python3
"""Regression tests for unambiguous project work-day revisions."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import revise_work_days


def draft(source: Path) -> dict:
    projects = [("TVA10A2R", 4), ("TVE1091U", 7), ("TVE1215M", 7), ("TVI2346M", 3)]
    return {
        "schema": "mirror-performance-draft-v3",
        "month": "2026-08",
        "source_policy": "weekly-primary-daily-secondary",
        "weekly_sources": [str(source)],
        "daily_sources": [],
        "work_days_basis": "initial allocation",
        "confirmed_work_days": 21,
        "entries": [
            {
                "task_type": "计划任务",
                "project": project,
                "task_description": "Test allocation",
                "start_date": "2026-08-03",
                "end_date": "2026-08-03",
                "work_days": days,
                "work_results": ["完成工时映射验证。"],
                "evidence": ["weekly:test"],
            }
            for project, days in projects
        ],
    }


class ReviseWorkDaysTest(unittest.TestCase):
    def test_ordered_values_preserve_project_order_when_values_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "weekly.json"
            source.write_text("{}\n", encoding="utf-8")
            data = draft(source)

            result = revise_work_days.revise(data, [5, 7, 6, 3], None)

            self.assertEqual(
                result["work_days_by_project"],
                [
                    {"project": "TVA10A2R", "work_days": 5},
                    {"project": "TVE1091U", "work_days": 7},
                    {"project": "TVE1215M", "work_days": 6},
                    {"project": "TVI2346M", "work_days": 3},
                ],
            )
            self.assertEqual([entry["work_days"] for entry in data["entries"]], [5, 7, 6, 3])
            self.assertEqual(data["confirmed_work_days"], 21)

    def test_explicit_mapping_must_cover_every_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "weekly.json"
            source.write_text("{}\n", encoding="utf-8")
            data = draft(source)

            with self.assertRaisesRegex(ValueError, "项目映射不完整"):
                revise_work_days.revise(data, None, ["TVA10A2R=5"])


if __name__ == "__main__":
    unittest.main()
