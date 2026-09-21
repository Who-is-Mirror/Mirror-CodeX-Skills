"""Bounded release lookup and exact Codex plugin update primitives."""

from __future__ import annotations

import json
import math
import re
import subprocess
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any


MARKETPLACE = "mirror-codex-marketplace"
TARGET_PLUGIN = "mirror-codex-skills"
PLUGIN_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MAX_MANIFEST_BYTES = 1024 * 1024
REMOTE_MANIFEST_TIMEOUT = 6
UPDATE_COMMAND_TIMEOUT = 60
MAX_COMMAND_OUTPUT_CHARS = 16000


def version_parts(value: str) -> tuple[int, int, int]:
    text = str(value or "")
    if not PLUGIN_VERSION_RE.fullmatch(text):
        raise ValueError(f"malformed stable plugin version: {text!r}")
    major, minor, patch = text.split(".")
    return int(major), int(minor), int(patch)


def compare_versions(left: str, right: str) -> int:
    left_parts = version_parts(left)
    right_parts = version_parts(right)
    return (left_parts > right_parts) - (left_parts < right_parts)


def fetch_manifest(
    url: str,
    timeout: float = REMOTE_MANIFEST_TIMEOUT,
    *,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Fetch one bounded, duplicate-key-free JSON object."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("manifest timeout must be finite and positive")
    if not re.fullmatch(
        r"https://raw\.githubusercontent\.com/Who-is-Mirror/"
        r"Mirror-CodeX-Skills/main/plugins/mirror-codex-skills/"
        r"\.codex-plugin/plugin\.json",
        url,
    ):
        raise ValueError("manifest URL is not the pinned Mirror plugin manifest")
    open_url = opener if opener is not None else urllib.request.urlopen
    with open_url(url, timeout=timeout) as response:
        raw = response.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError(f"remote plugin manifest exceeds {MAX_MANIFEST_BYTES} bytes")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"remote plugin manifest repeats key: {key}")
            value[key] = item
        return value

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"remote plugin manifest contains non-finite number: {value}")

    payload = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_non_finite,
    )
    if not isinstance(payload, dict):
        raise ValueError("remote plugin manifest must be a JSON object")
    return payload


def _output(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value or "").strip()[:MAX_COMMAND_OUTPUT_CHARS]


def run_codex(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def update_plugin(
    *,
    run_command: Callable[[list[str], int], Any],
    verify_install: Callable[[], Mapping[str, Any]],
    expected_version: str,
) -> dict[str, Any]:
    """Refresh the configured marketplace, reinstall this plugin, then verify it."""
    version_parts(expected_version)
    result: dict[str, Any] = {
        "attempted": True,
        "status": "FAIL",
        "restart_required": False,
    }
    commands = (
        (
            "marketplace",
            ["codex", "plugin", "marketplace", "upgrade", MARKETPLACE, "--json"],
        ),
        (
            "install",
            ["codex", "plugin", "add", f"{TARGET_PLUGIN}@{MARKETPLACE}", "--json"],
        ),
    )
    for stage, command in commands:
        result[f"{stage}_command"] = command
        try:
            completed = run_command(command, UPDATE_COMMAND_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result.update(
                {
                    "reason": f"{stage}_command_failed",
                    "message": str(exc),
                    "stdout": _output(getattr(exc, "stdout", "")),
                    "stderr": _output(getattr(exc, "stderr", "")),
                }
            )
            return result
        if completed.returncode != 0:
            result.update(
                {
                    "reason": f"{stage}_command_failed",
                    "stdout": _output(completed.stdout),
                    "stderr": _output(completed.stderr),
                }
            )
            return result
        result[f"{stage}_stdout"] = _output(completed.stdout)

    try:
        installation = verify_install()
    except Exception as exc:
        return {
            **result,
            "reason": "install_verification_failed",
            "message": str(exc),
        }
    if not isinstance(installation, Mapping):
        return {
            **result,
            "reason": "install_verification_failed",
            "message": "installation verifier returned no metadata",
        }
    result.update(
        {
            key: value
            for key, value in installation.items()
            if key.startswith("installed_plugin_")
        }
    )
    installed_version = installation.get("installed_plugin_version")
    try:
        if installation.get("installed_plugin_active") is not True:
            raise ValueError("updated plugin is not uniquely installed and enabled")
        if not installation.get("installed_plugin_path"):
            raise ValueError("updated plugin cache path was not verified")
        if not isinstance(installed_version, str):
            raise ValueError("updated plugin has no installed version")
        if compare_versions(installed_version, expected_version) < 0:
            raise ValueError(
                f"installed plugin {installed_version} is older than {expected_version}"
            )
    except ValueError as exc:
        return {
            **result,
            "reason": "install_verification_failed",
            "message": str(exc),
        }
    result.update(
        {
            "status": "PASS",
            "restart_required": True,
            "message": "Plugin files were updated and verified; restart Codex to load them.",
        }
    )
    return result
