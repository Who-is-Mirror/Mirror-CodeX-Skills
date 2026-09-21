#!/usr/bin/env python3
"""Isolated regression tests for AKBS performance-source discovery."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import builtins
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import discover_sources


def write_package(
    root: Path,
    *,
    date: str,
    alias: str,
    run_id: str,
    report_type: str,
    week_range: str = "",
    replacement_for_run_id: str = "",
    display: bool = True,
) -> Path:
    package = root / "submitted" / date.replace("-", "") / alias / run_id
    package.mkdir(parents=True)
    report_name = f"{report_type}.md"
    report = package / "reports" / report_name
    report.parent.mkdir()
    report.write_text(f"# {report_type} {run_id}\n", encoding="utf-8")
    files: dict[str, object] = {}
    if display:
        view = package / "materials" / "display" / "report_view.json"
        view.parent.mkdir(parents=True)
        view.write_text(
            json.dumps(
                {
                    "kind": "report_view",
                    "payload": {
                        "report_type": report_type,
                        "member_alias": alias,
                        "completed_items": ["completed fact"],
                        "next_week_plan": ["must not become a result"],
                        "remaining": ["must not become a result"],
                    },
                }
            ),
            encoding="utf-8",
        )
        files["display"] = ["materials/display/report_view.json"]
    manifest: dict[str, object] = {
        "member_alias": alias,
        "package_kind": f"{report_type}_trace",
        "report_type": report_type,
        "report_path": f"reports/{report_name}",
        "date": date,
        "run_id": run_id,
        "files": files,
    }
    if week_range:
        manifest["week_range"] = week_range
    if replacement_for_run_id:
        manifest["replacement_for_run_id"] = replacement_for_run_id
        manifest["supersedes"] = {"run_id": replacement_for_run_id}
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return package


class DiscoverSourcesTest(unittest.TestCase):
    def test_primary_root_replacement_and_report_view_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            primary, legacy = workspace / "primary", workspace / "legacy"
            replaced = write_package(
                primary,
                date="2026-08-28",
                alias="mirror",
                run_id="shared-run",
                report_type="weekly",
                week_range="20260824-20260830",
            )
            current = write_package(
                primary,
                date="2026-08-28",
                alias="mirror",
                run_id="20260828-120000",
                report_type="weekly",
                week_range="20260824-20260830",
                replacement_for_run_id="shared-run",
            )
            legacy_weekly = write_package(
                legacy,
                date="2026-08-28",
                alias="mirror",
                run_id="20260828-130000",
                report_type="weekly",
                week_range="20260824-20260830",
            )
            legacy_only = write_package(
                legacy,
                date="2026-08-21",
                alias="mirror",
                run_id="20260821-130000",
                report_type="weekly",
                week_range="20260817-20260823",
            )
            primary_daily = write_package(
                primary,
                date="2026-08-30",
                alias="mirror",
                run_id="shared-run",
                report_type="daily",
                display=False,
            )
            write_package(
                primary,
                date="2026-08-30",
                alias="other-member",
                run_id="20260830-130000",
                report_type="daily",
            )
            pending = primary / "pending" / "20260831" / "mirror" / "pending-run"
            pending.mkdir(parents=True)
            (pending / "manifest.json").write_text("{}", encoding="utf-8")
            facts = primary / "weekly-facts"
            facts.mkdir()
            fact = facts / "20260824-20260830.json"
            fact.write_text('{"member_alias": "mirror"}', encoding="utf-8")

            with patch.dict(os.environ, {"PERFORMANCE_MEMBER_ALIAS": "mirror"}, clear=False):
                result = discover_sources.discover("2026-08", primary, legacy)

            self.assertEqual(result["member_alias"], "mirror")
            self.assertEqual(result["member_alias_source"], "PERFORMANCE_MEMBER_ALIAS")
            self.assertEqual(
                result["weekly_report_sources"],
                [
                    str((legacy_only / "materials/display/report_view.json").resolve()),
                    str((current / "materials/display/report_view.json").resolve()),
                ],
            )
            self.assertNotIn(str((replaced / "materials/display/report_view.json").resolve()), result["weekly_sources"])
            self.assertNotIn(str((legacy_weekly / "materials/display/report_view.json").resolve()), result["weekly_sources"])
            self.assertEqual(result["weekly_fact_sources"], [str(fact.resolve())])
            self.assertEqual(result["daily_sources"], [str((primary_daily / "reports/daily.md").resolve())])
            self.assertEqual(result["weekly_count"], 3)
            self.assertEqual(result["daily_count"], 1)

    def test_custom_root_facts_require_matching_member_and_can_be_the_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "shared"
            facts = root / "weekly-facts"
            facts.mkdir(parents=True)
            matching = facts / "20260803-20260809.json"
            matching.write_text('{"member_alias": "mirror"}', encoding="utf-8")
            unscoped = facts / "20260810-20260816.json"
            unscoped.write_text("{}", encoding="utf-8")
            other = facts / "20260817-20260823.json"
            other.write_text('{"member_alias": "other-member"}', encoding="utf-8")

            with patch.dict(os.environ, {"PERFORMANCE_MEMBER_ALIAS": "mirror"}, clear=False):
                result = discover_sources.discover("2026-08", root, Path(temporary) / "legacy")

            self.assertTrue(result["weekly_ready"])
            self.assertEqual(result["weekly_report_count"], 0)
            self.assertEqual(result["weekly_fact_sources"], [str(matching.resolve())])
            self.assertEqual(result["weekly_fact_omitted_count"], 2)
            self.assertEqual(len(result["weekly_fact_warnings"]), 2)

    def test_missing_tomllib_fails_as_a_discovery_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "akbs-member-ops.toml"
            config.write_text('default_profile = "mirror"\n', encoding="utf-8")
            original_import = builtins.__import__

            def no_tomllib(name: str, *args: object, **kwargs: object) -> object:
                if name == "tomllib":
                    raise ImportError("simulated missing tomllib")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=no_tomllib):
                with self.assertRaisesRegex(discover_sources.DiscoveryError, "cannot read authoritative"):
                    discover_sources._load_toml(config)

    def test_profile_selection_and_ambiguous_inference_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            config = workspace / "akbs-member-ops.toml"
            config.write_text(
                'default_profile = "one"\n[profiles.one]\nmember_alias = "member-one"\n'
                '[profiles.two]\nmember_alias = "member-two"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_REPORT_PROFILE": "two"}, clear=True):
                self.assertEqual(
                    discover_sources.configured_member_alias(workspace),
                    ("member-two", "akbs-member-ops.toml:two"),
                )

            primary, legacy = workspace / "primary", workspace / "legacy"
            write_package(
                primary,
                date="2026-08-01",
                alias="member-one",
                run_id="20260801-100000",
                report_type="daily",
            )
            write_package(
                legacy,
                date="2026-08-02",
                alias="member-two",
                run_id="20260802-100000",
                report_type="daily",
            )
            with patch.object(discover_sources, "configured_member_alias", return_value=None):
                with self.assertRaisesRegex(discover_sources.DiscoveryError, "multiple member aliases"):
                    discover_sources.discover("2026-08", primary, legacy)

    def test_single_archive_alias_is_inferred_without_real_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            package = write_package(
                root,
                date="2026-08-04",
                alias="sole-member",
                run_id="20260804-100000",
                report_type="daily",
            )
            with patch.object(discover_sources, "configured_member_alias", return_value=None):
                result = discover_sources.discover("2026-08", root, Path(temporary) / "legacy")
            self.assertEqual(result["member_alias"], "sole-member")
            self.assertEqual(result["member_alias_source"], "target-month submitted archive")
            self.assertEqual(result["daily_sources"], [str((package / "materials/display/report_view.json").resolve())])


if __name__ == "__main__":
    unittest.main()
