---
name: edge-cdp-session
description: "Launch or reuse an isolated Microsoft Edge CDP session with a dynamically allocated port and fail-closed process, listener-PID, profile, and endpoint validation. Use as shared browser infrastructure for skills that attach with Playwright; do not perform business-page actions or manage credentials."
---

# Edge CDP Session

## Plugin task-start gate

When this skill is supplied by the `mirror-codex-skills` plugin, choose one stable task ID for the coherent user request and run this before inspecting or starting Edge:

```bash
python3 "$PLUGIN_ROOT/lib/mirror_codex_skills/task_start.py" --task-id "<stable-task-id>"
```

Reuse that ID across nested skills and after an update restart; do not make the user manage it. Continue only on `PASS`. On `UPDATED_RESTART_REQUIRED`, stop before browser work, ask the user to exit and restart Codex, then repeat the gate with the same ID. Report every other blocking status verbatim, and add `--retry` only after its cause is corrected. If `$PLUGIN_ROOT`, the entry point, or the active cache identity is missing, stop instead of guessing another updater path. Do not update while recovering an already-running browser command; first reach a safe boundary.

Provide only the browser connection/lifecycle layer. Do not navigate business pages, fill forms, read reports, manage credentials, or click submission actions.

Use `scripts/ensure_session.py --session <stable-name>` before a dependent skill connects with Playwright. Consume only a successful JSON result from stdout:

```json
{
  "ready": true,
  "started": false,
  "session": "example-skill",
  "edge_profile": "C:\\Users\\name\\AppData\\Local\\Codex\\EdgeSessions\\example-skill",
  "cdp_http_endpoint": "http://127.0.0.1:43123",
  "cdp_port": 43123,
  "webSocketDebuggerUrl": "ws://127.0.0.1:43123/devtools/browser/id"
}
```

The default path is fail closed:

1. Resolve one isolated, persistent Windows Edge profile for the stable session name.
2. Reuse only one exact root Edge process whose command line owns that profile and uses the expected debugging mode.
3. Otherwise start Edge with `--remote-debugging-port=0`; never scan for a free port.
4. Read the exact profile's two-line `DevToolsActivePort`, first through WSL and then through PowerShell for Windows package-path redirection compatibility.
5. Require the IPv4 loopback listener owner PID to equal the validated root Edge PID.
6. Require `/json/version` to return a loopback WebSocket endpoint with the exact dynamic port and browser path from `DevToolsActivePort`.

Do not attach to a user-owned daily Edge window, an endpoint discovered by port scanning, an IPv6-only listener, or an endpoint whose process ownership is ambiguous. Do not ask the user to enable `edge://inspect/#remote-debugging`; this skill starts its own isolated Edge.

An explicit `--port` is a troubleshooting override only. Before launch it must be unused on every address, and after launch it receives the same root-process, listener-PID, and endpoint validation. An explicit `--edge-profile` is allowed so a migrated consumer can preserve its existing isolated login state. Never share one profile between unrelated business skills.

If Edge or Windows PowerShell is unavailable, return a concise JSON error and stop. Never install dependencies, modify daily Edge settings, clear cookies/storage, kill unrelated Edge processes, or delete the profile.
