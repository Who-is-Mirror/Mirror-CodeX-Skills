#!/usr/bin/env python3
"""Delegate dedicated Edge lifecycle to the shared edge-cdp-session skill."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from runtime_paths import resolve_runtime_paths


SKILL_DIR = Path(__file__).resolve().parent.parent


def shared_ensure_script() -> Path:
    candidates: list[Path] = []
    explicit = os.environ.get("PERFORMANCE_EDGE_CDP_SKILL_ROOT", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend(
        [
            SKILL_DIR.parent / "edge-cdp-session",
            SKILL_DIR.parent.parent / "skills" / "edge-cdp-session",
        ]
    )
    for root in candidates:
        script = root.resolve() / "scripts" / "ensure_session.py"
        if script.is_file():
            return script
    raise FileNotFoundError(
        "edge-cdp-session is not installed; place it beside performance-task-entry "
        "or set PERFORMANCE_EDGE_CDP_SKILL_ROOT"
    )


def main() -> int:
    try:
        config = resolve_runtime_paths()
        script = shared_ensure_script()
    except (ValueError, FileNotFoundError) as error:
        print(json.dumps({"ready": False, "error": str(error)}))
        return 1
    command = [
        "python3",
        str(script),
        "--session",
        "performance-task-entry",
        "--edge-profile",
        config.edge_profile,
        "--edge-executable",
        config.edge_executable,
        "--windows-profile",
        config.windows_profile,
    ]
    if config.cdp_port_is_override:
        command.extend(["--port", str(config.cdp_port)])
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.stdout:
        print(completed.stdout.rstrip())
    elif completed.stderr:
        print(json.dumps({"ready": False, "error": completed.stderr.strip()}))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
