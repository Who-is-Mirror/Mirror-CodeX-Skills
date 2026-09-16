#!/usr/bin/env python3
"""Regression tests for workspace-safe performance draft delivery."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
FINALIZE = SKILL / "scripts" / "finalize_draft.py"


class FinalizeDraftTest(unittest.TestCase):
    def test_creates_real_workspace_files_and_separate_archive_copies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "task"
            work = workspace / "work"
            archive = Path(temporary) / "archive"
            source = Path(temporary) / "weekly.json"
            work.mkdir(parents=True)
            source.write_text("{}\n", encoding="utf-8")
            draft = work / "draft.json"
            draft.write_text(
                json.dumps(
                    {
                        "schema": "mirror-performance-draft-v3",
                        "month": "2026-08",
                        "source_policy": "weekly-primary-daily-secondary",
                        "weekly_sources": [str(source)],
                        "daily_sources": [],
                        "work_days_basis": "verified allocation",
                        "confirmed_work_days": 1,
                        "entries": [
                            {
                                "task_type": "计划任务",
                                "project": "TEST001",
                                "task_description": "Test delivery",
                                "start_date": "2026-08-03",
                                "end_date": "2026-08-03",
                                "work_days": 1,
                                "work_results": ["完成交付验证。"],
                                "evidence": ["weekly:test"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            run = subprocess.run(
                [
                    "python3",
                    str(FINALIZE),
                    str(draft),
                    "--archive-root",
                    str(archive),
                    "--timestamp",
                    "20260904-120000",
                ],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads(run.stdout)
            self.assertEqual(
                result["work_days_by_project"],
                [{"project": "TEST001", "work_days": 1}],
            )
            archived_draft = Path(result["draft_path"])
            archived_review = Path(result["review_path"])
            delivered_draft = Path(result["delivery_draft_path"])
            delivered_review = Path(result["delivery_review_path"])
            for path in (archived_draft, archived_review, delivered_draft, delivered_review):
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
            self.assertEqual(archived_draft.read_bytes(), delivered_draft.read_bytes())
            self.assertEqual(archived_review.read_bytes(), delivered_review.read_bytes())
            self.assertEqual(delivered_draft.parent, workspace / "outputs")

    def test_rejects_delivery_outside_the_current_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "task"
            outside = Path(temporary) / "outside"
            workspace.mkdir()
            source = workspace / "weekly.json"
            source.write_text("{}\n", encoding="utf-8")
            draft = workspace / "draft.json"
            draft.write_text(
                json.dumps(
                    {
                        "schema": "mirror-performance-draft-v3",
                        "month": "2026-08",
                        "source_policy": "weekly-primary-daily-secondary",
                        "weekly_sources": [str(source)],
                        "daily_sources": [],
                        "work_days_basis": "verified allocation",
                        "confirmed_work_days": 1,
                        "entries": [
                            {
                                "task_type": "计划任务",
                                "project": "TEST001",
                                "task_description": "Test delivery",
                                "start_date": "2026-08-03",
                                "end_date": "2026-08-03",
                                "work_days": 1,
                                "work_results": ["完成交付验证。"],
                                "evidence": ["weekly:test"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            run = subprocess.run(
                ["python3", str(FINALIZE), str(draft), "--output-dir", str(outside)],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(run.returncode, 0)
            self.assertIn("output directory must stay inside", run.stderr)


if __name__ == "__main__":
    unittest.main()
