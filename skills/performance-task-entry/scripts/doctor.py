#!/usr/bin/env python3
"""Run the two mandatory performance-workflow gates in strict order."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

from discover_sources import DiscoveryError, configured_member_alias
from runtime_paths import resolve_runtime_paths


def version(command: str, argument: str = "--version") -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    try:
        run = subprocess.run(
            [executable, argument], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "available"
    lines = (run.stdout + "\n" + run.stderr).strip().splitlines()
    return next((line.strip() for line in lines if line.strip()), "available")


def cdp_status(endpoint: str) -> dict:
    try:
        with urllib.request.urlopen(endpoint, timeout=3) as response:
            data = json.load(response)
        websocket = data.get("webSocketDebuggerUrl")
        return {"ready": bool(websocket), "webSocketDebuggerUrl": websocket}
    except Exception as error:
        return {"ready": False, "error": type(error).__name__}


def background_edge_status(config) -> dict:
    """Provide a non-launching, non-authoritative CDP diagnostic.

    Automatic ports are only knowable after the dedicated Edge is started and
    ownership-validated, which doctor intentionally does not do.  A fixed
    override can be probed for reachability, but that probe never claims the
    service belongs to the dedicated profile.
    """
    if not config.cdp_port_is_override:
        return {
            "mode": "automatic",
            "ready": False,
            "status": "not_probed",
            "reason": "automatic_endpoint_requires_dedicated_profile_validation",
        }
    return {
        "mode": "fixed_override",
        "ownership": "not_checked",
        **cdp_status(config.cdp_endpoint),
    }


def credential_status(vault_dir: Path) -> dict:
    """Validate the encrypted vault without exposing its contents."""
    result = {"checked": True, "ready": False, "path": str(vault_dir)}
    try:
        from credential_vault import load_and_validate

        metadata = load_and_validate(vault_dir)
        result.update({"ready": True, **metadata})
    except Exception as error:
        result["error"] = type(error).__name__
    return result


def gate_exit_code(missing_dependencies: list[str], credentials_ready: bool) -> int:
    if missing_dependencies:
        return 2
    if not credentials_ready:
        return 3
    return 0


def main() -> int:
    try:
        config = resolve_runtime_paths()
    except ValueError as error:
        print(json.dumps({"runtime_configuration": {"ready": False, "error": str(error)}}))
        return 2
    dependencies = {
        "node": version("node"),
        "npm": version("npm"),
        "playwright-cli": version("playwright-cli"),
        "python3": version("python3"),
        "cryptography": "available" if importlib.util.find_spec("cryptography") else None,
    }
    missing = [name for name, value in dependencies.items() if value is None]
    dependencies_ready = not missing
    credentials = (
        credential_status(config.credential_vault)
        if dependencies_ready
        else {
            "checked": False,
            "ready": False,
            "path": str(config.credential_vault),
            "status": "not_run",
            "reason": "dependencies_not_ready",
        }
    )
    ready_for_draft = dependencies_ready and credentials["ready"]
    result = {
        "prerequisite_steps": [
            {
                "step": 1,
                "name": "environment_dependencies",
                "status": "ready" if dependencies_ready else "blocked",
            },
            {
                "step": 2,
                "name": "encrypted_credentials",
                "status": (
                    "ready"
                    if credentials["ready"]
                    else "blocked"
                    if dependencies_ready
                    else "not_run"
                ),
            },
        ],
        "dependencies": dependencies,
        "missing_dependencies": missing,
        "dependencies_ready": dependencies_ready,
        "credential_vault": credentials,
        "ready_for_draft": ready_for_draft,
        "runtime_configuration": config.public_dict(),
    }
    if not dependencies_ready:
        result["next_action"] = "install_missing_dependencies_with_user_approval"
    elif not credentials["ready"]:
        result["next_action"] = "request_account_and_password_in_chat"
    else:
        try:
            member_identity = configured_member_alias(config.codex_home)
            result["source_archive"] = {
                "primary_root": str(config.source_root),
                "legacy_root": str(config.legacy_source_root),
                "member_identity": {
                    "ready": member_identity is not None,
                    "member_alias": member_identity[0] if member_identity else None,
                    "source": member_identity[1] if member_identity else None,
                    "note": (
                        None
                        if member_identity
                        else "target-month archive inference occurs in discover_sources.py YYYY-MM"
                    ),
                },
            }
        except DiscoveryError as error:
            result["source_archive"] = {
                "primary_root": str(config.source_root),
                "legacy_root": str(config.legacy_source_root),
                "member_identity": {"ready": False, "error": str(error)},
            }
        result["background_edge"] = background_edge_status(config)
        result["next_action"] = "probe_browser"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return gate_exit_code(missing, credentials["ready"])


if __name__ == "__main__":
    raise SystemExit(main())
