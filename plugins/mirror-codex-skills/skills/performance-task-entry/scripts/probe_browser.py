#!/usr/bin/env python3
"""Start and read-only probe the dedicated Edge, leaving it running and detached."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from runtime_paths import resolve_runtime_paths

SCRIPTS = Path(__file__).resolve().parent


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update({"TMPDIR": "/tmp", "TEMP": "/tmp", "TMP": "/tmp"})
    return subprocess.run(args, cwd=cwd, env=environment, capture_output=True, text=True, timeout=60, check=False)


def main() -> int:
    try:
        config = resolve_runtime_paths()
    except ValueError as error:
        print(json.dumps({"ready": False, "error": str(error)}))
        return 1
    edge = run(["python3", str(SCRIPTS / "ensure_background_edge.py")], Path.cwd())
    if edge.returncode:
        print(edge.stdout.strip() or edge.stderr.strip())
        return 1
    edge_result = json.loads(edge.stdout)
    endpoint = edge_result["webSocketDebuggerUrl"]
    with tempfile.TemporaryDirectory(prefix="performance-browser-probe-") as temporary:
        cwd = Path(temporary)
        attach = run(["playwright-cli", "-s=edge-performance", "attach", f"--cdp={endpoint}"], cwd)
        if attach.returncode:
            print(json.dumps({"ready": False, "error": "Playwright attach failed"}))
            return 1
        detach = run(["playwright-cli", "-s=edge-performance", "detach"], cwd)
        if detach.returncode:
            print(json.dumps({"ready": False, "error": "Playwright detach failed"}))
            return 1
    print(json.dumps({
        "ready": True,
        "detached": True,
        "session": "edge-performance",
        "cdp_port": edge_result["cdp_port"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
