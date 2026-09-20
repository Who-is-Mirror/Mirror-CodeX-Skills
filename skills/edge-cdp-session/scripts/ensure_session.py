#!/usr/bin/env python3
"""CLI adapter for the owned Edge CDP session implementation."""

from __future__ import annotations

import argparse
import json
import os

import edge_session


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True)
    parser.add_argument("--edge-profile")
    parser.add_argument("--edge-executable")
    parser.add_argument("--windows-profile")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    overrides = {
        "EDGE_CDP_SESSION": args.session,
        "EDGE_CDP_PROFILE": args.edge_profile,
        "EDGE_CDP_EXECUTABLE": args.edge_executable,
        "EDGE_CDP_WINDOWS_PROFILE": args.windows_profile,
        "EDGE_CDP_PORT": str(args.port) if args.port is not None else None,
    }
    for key, value in overrides.items():
        if value is not None:
            os.environ[key] = value
    try:
        return edge_session.main()
    except ValueError as error:
        print(json.dumps({"ready": False, "error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
