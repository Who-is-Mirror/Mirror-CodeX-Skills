#!/usr/bin/env python3
"""Tests for bounded manifest lookup and exact updater commands."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path


LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

import codex_plugin_update as update


COMMIT = "a" * 40


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class VersionTest(unittest.TestCase):
    def test_stable_versions_compare_numerically(self) -> None:
        self.assertEqual(update.compare_versions("0.2.0", "0.1.9"), 1)
        self.assertEqual(update.compare_versions("1.0.0", "1.0.0"), 0)
        self.assertEqual(update.compare_versions("1.0.0", "2.0.0"), -1)

    def test_non_stable_versions_are_rejected(self) -> None:
        for value in ("1", "1.2", "1.2.3-beta", "1.2.3+cache", "v1.2.3"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                update.version_parts(value)


class ManifestFetchTest(unittest.TestCase):
    def test_resolves_main_then_builds_an_immutable_manifest_url(self) -> None:
        seen: list[list[str]] = []

        def run(command: list[str], _timeout: int):
            seen.append(command)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"{COMMIT}\trefs/heads/main\n",
                stderr="",
            )

        commit = update.resolve_main_commit(run)
        self.assertEqual(commit, COMMIT)
        self.assertEqual(
            seen[0],
            [
                "git",
                "ls-remote",
                "--exit-code",
                "--refs",
                "https://github.com/Who-is-Mirror/Mirror-CodeX-Skills.git",
                "refs/heads/main",
            ],
        )
        self.assertEqual(
            update.manifest_url(commit),
            "https://raw.githubusercontent.com/Who-is-Mirror/Mirror-CodeX-Skills/"
            f"{COMMIT}/plugins/mirror-codex-skills/.codex-plugin/plugin.json",
        )

    def test_rejects_ambiguous_or_malformed_main_refs(self) -> None:
        for output in ("", "not-a-sha\trefs/heads/main\n", f"{COMMIT}\tother\n"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                update.resolve_main_commit(
                    lambda command, _timeout, output=output: subprocess.CompletedProcess(
                        command, 0, stdout=output, stderr=""
                    )
                )

    def test_accepts_only_the_pinned_bounded_manifest(self) -> None:
        payload = json.dumps({"name": update.TARGET_PLUGIN, "version": "0.2.0"}).encode()
        seen: list[tuple[str, float]] = []

        def opener(url: str, *, timeout: float):
            seen.append((url, timeout))
            return Response(payload)

        result = update.fetch_manifest(
            "https://raw.githubusercontent.com/Who-is-Mirror/Mirror-CodeX-Skills/"
            f"{COMMIT}/plugins/mirror-codex-skills/.codex-plugin/plugin.json",
            timeout=3,
            opener=opener,
        )
        self.assertEqual(result["version"], "0.2.0")
        self.assertEqual(seen[0][1], 3)

    def test_rejects_wrong_url_duplicate_keys_and_oversize(self) -> None:
        with self.assertRaises(ValueError):
            update.fetch_manifest(
                "https://example.com/plugin.json",
                opener=lambda *_args, **_kwargs: Response(b"{}"),
            )
        with self.assertRaises(ValueError):
            update.fetch_manifest(
                "https://raw.githubusercontent.com/Who-is-Mirror/Mirror-CodeX-Skills/"
                f"{COMMIT}/plugins/mirror-codex-skills/.codex-plugin/plugin.json",
                opener=lambda *_args, **_kwargs: Response(b'{"name":"a","name":"b"}'),
            )
        with self.assertRaises(ValueError):
            update.fetch_manifest(
                "https://raw.githubusercontent.com/Who-is-Mirror/Mirror-CodeX-Skills/"
                f"{COMMIT}/plugins/mirror-codex-skills/.codex-plugin/plugin.json",
                opener=lambda *_args, **_kwargs: Response(
                    b"{" + b" " * update.MAX_MANIFEST_BYTES + b"}"
                ),
            )


class UpdateExecutionTest(unittest.TestCase):
    def test_runs_only_exact_marketplace_and_plugin_commands_then_verifies(self) -> None:
        commands: list[list[str]] = []

        def run(command: list[str], _timeout: int):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

        result = update.update_plugin(
            run_command=run,
            expected_version="0.2.0",
            verify_install=lambda: {
                "installed_plugin_active": True,
                "installed_plugin_version": "0.2.0",
                "installed_plugin_path": "/verified/cache",
            },
        )
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["restart_required"])
        self.assertEqual(
            commands,
            [
                [
                    "codex",
                    "plugin",
                    "marketplace",
                    "upgrade",
                    "mirror-codex-marketplace",
                    "--json",
                ],
                [
                    "codex",
                    "plugin",
                    "add",
                    "mirror-codex-skills@mirror-codex-marketplace",
                    "--json",
                ],
            ],
        )

    def test_command_or_verification_failure_never_reports_success(self) -> None:
        def fail_install(command: list[str], _timeout: int):
            code = 0 if "upgrade" in command else 4
            return subprocess.CompletedProcess(command, code, stdout="", stderr="failed")

        command_failure = update.update_plugin(
            run_command=fail_install,
            expected_version="0.2.0",
            verify_install=lambda: self.fail("verification must not run"),
        )
        self.assertEqual(command_failure["reason"], "install_command_failed")

        verification_failure = update.update_plugin(
            run_command=lambda command, _timeout: subprocess.CompletedProcess(
                command, 0, stdout="{}", stderr=""
            ),
            expected_version="0.2.0",
            verify_install=lambda: {
                "installed_plugin_active": True,
                "installed_plugin_version": "0.1.0",
                "installed_plugin_path": "/old/cache",
            },
        )
        self.assertEqual(verification_failure["reason"], "install_verification_failed")


if __name__ == "__main__":
    unittest.main()
