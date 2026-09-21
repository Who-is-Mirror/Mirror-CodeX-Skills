#!/usr/bin/env python3
"""Tests for the per-task updater gate and restart boundary."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

from mirror_codex_skills import task_start


def install(home: Path, version: str) -> Path:
    root = (
        home
        / "plugins"
        / "cache"
        / "mirror-codex-marketplace"
        / "mirror-codex-skills"
        / version
    )
    manifest = root / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"name": "mirror-codex-skills", "version": version}),
        encoding="utf-8",
    )
    return root


def inventory(version: str) -> dict[str, object]:
    return {
        "installed": [
            {
                "name": "mirror-codex-skills",
                "marketplaceName": "mirror-codex-marketplace",
                "version": version,
                "installed": True,
                "enabled": True,
                "marketplaceSource": {
                    "sourceType": "git",
                    "source": "https://github.com/Who-is-Mirror/Mirror-CodeX-Skills.git",
                },
            }
        ],
        "available": [],
    }


class TaskStartTest(unittest.TestCase):
    def test_equal_version_passes_once_and_reuses_task_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = install(home, "0.2.0")
            fetch_count = 0

            def fetch():
                nonlocal fetch_count
                fetch_count += 1
                return {"name": "mirror-codex-skills", "version": "0.2.0"}

            first = task_start.ensure_task_started(
                root,
                "task-equal",
                codex_home=home,
                fetch_remote=fetch,
                inventory_reader=lambda: inventory("0.2.0"),
            )
            second = task_start.ensure_task_started(
                root,
                "task-equal",
                codex_home=home,
                fetch_remote=fetch,
                inventory_reader=lambda: inventory("0.2.0"),
            )
            self.assertEqual(first["status"], "PASS")
            self.assertFalse(first["reused"])
            self.assertTrue(second["reused"])
            self.assertEqual(fetch_count, 1)

    def test_newer_release_updates_then_requires_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            old_root = install(home, "0.1.0")
            new_root = install(home, "0.2.0")
            inventories = iter(
                [inventory("0.1.0"), inventory("0.1.0"), inventory("0.2.0")]
            )
            commands: list[list[str]] = []

            def run(command: list[str], _timeout: int):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

            updated = task_start.ensure_task_started(
                old_root,
                "task-update",
                codex_home=home,
                fetch_remote=lambda: {
                    "name": "mirror-codex-skills",
                    "version": "0.2.0",
                },
                run_command=run,
                inventory_reader=lambda: next(inventories),
            )
            self.assertEqual(updated["status"], "UPDATED_RESTART_REQUIRED")
            self.assertTrue(updated["blocking"])
            self.assertEqual(updated["installed_version"], "0.2.0")
            self.assertEqual(len(commands), 2)

            restarted = task_start.ensure_task_started(
                new_root,
                "task-update",
                codex_home=home,
                fetch_remote=lambda: {
                    "name": "mirror-codex-skills",
                    "version": "0.2.0",
                },
                inventory_reader=lambda: inventory("0.2.0"),
            )
            self.assertEqual(restarted["status"], "PASS")
            self.assertEqual(restarted["local_version"], "0.2.0")

    def test_failed_check_is_retained_until_explicit_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = install(home, "0.2.0")
            calls = 0

            def fail():
                nonlocal calls
                calls += 1
                raise TimeoutError("offline")

            failed = task_start.ensure_task_started(
                root,
                "task-retry",
                codex_home=home,
                fetch_remote=fail,
                inventory_reader=lambda: inventory("0.2.0"),
            )
            retained = task_start.ensure_task_started(
                root,
                "task-retry",
                codex_home=home,
                fetch_remote=fail,
                inventory_reader=lambda: inventory("0.2.0"),
            )
            retried = task_start.ensure_task_started(
                root,
                "task-retry",
                retry=True,
                codex_home=home,
                fetch_remote=lambda: {
                    "name": "mirror-codex-skills",
                    "version": "0.2.0",
                },
                inventory_reader=lambda: inventory("0.2.0"),
            )
            self.assertEqual(failed["status"], "CHECK_FAILED")
            self.assertTrue(retained["reused"])
            self.assertEqual(calls, 1)
            self.assertEqual(retried["status"], "PASS")

    @unittest.skipIf(sys.platform == "win32", "fcntl probe is POSIX-specific")
    def test_busy_lock_fails_without_fetching_or_updating(self) -> None:
        import fcntl

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = install(home, "0.2.0")
            state_dir = home / "artifacts" / "mirror-codex-skills" / "startup"
            state_dir.mkdir(parents=True)
            lock_path = state_dir / ".update.lock"
            with lock_path.open("a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = task_start.ensure_task_started(
                    root,
                    "task-busy",
                    codex_home=home,
                    fetch_remote=lambda: self.fail("fetch must not run"),
                    inventory_reader=lambda: inventory("0.2.0"),
                )
            self.assertEqual(result["status"], "STARTUP_BUSY")

    @unittest.skipIf(sys.platform == "win32", "symlink creation may require elevation")
    def test_symlinked_lock_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = install(home, "0.2.0")
            state_dir = home / "artifacts" / "mirror-codex-skills" / "startup"
            state_dir.mkdir(parents=True)
            target = home / "outside-lock"
            target.touch()
            (state_dir / ".update.lock").symlink_to(target)
            result = task_start.ensure_task_started(
                root,
                "task-symlink",
                codex_home=home,
                fetch_remote=lambda: self.fail("fetch must not run"),
                inventory_reader=lambda: inventory("0.2.0"),
            )
            self.assertEqual(result["status"], "STARTUP_STATE_INVALID")

    def test_wrong_or_disabled_install_is_rejected_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = install(home, "0.2.0")
            disabled = inventory("0.2.0")
            disabled["installed"][0]["enabled"] = False  # type: ignore[index]
            result = task_start.ensure_task_started(
                root,
                "task-disabled",
                codex_home=home,
                fetch_remote=lambda: self.fail("fetch must not run"),
                inventory_reader=lambda: disabled,
            )
            self.assertEqual(result["status"], "INSTALL_FAMILY_INVALID")

            local_source = inventory("0.2.0")
            local_source["installed"][0]["marketplaceSource"] = {  # type: ignore[index]
                "sourceType": "local",
                "source": "/tmp/spoofed-marketplace",
            }
            spoofed = task_start.ensure_task_started(
                root,
                "task-spoofed",
                codex_home=home,
                fetch_remote=lambda: self.fail("fetch must not run"),
                inventory_reader=lambda: local_source,
            )
            self.assertEqual(spoofed["status"], "INSTALL_FAMILY_INVALID")


if __name__ == "__main__":
    unittest.main()
