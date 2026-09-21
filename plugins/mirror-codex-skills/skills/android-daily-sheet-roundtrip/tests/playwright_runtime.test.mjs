import test from 'node:test';
import assert from 'node:assert/strict';
import {
  BrowserPrerequisiteError,
  normalizePlaywrightApi,
  playwrightInstallRequest,
  resolveManagedCdpEndpoint,
} from '../scripts/playwright_runtime.mjs';

test('accepts named and default Playwright exports', () => {
  const chromium = { connectOverCDP() {} };
  assert.equal(normalizePlaywrightApi({ chromium }).chromium, chromium);
  assert.equal(normalizePlaywrightApi({ default: { chromium } }).chromium, chromium);
});

test('rejects a module without chromium CDP support', () => {
  assert.throws(
    () => normalizePlaywrightApi({ default: {} }),
    (error) => error instanceof BrowserPrerequisiteError && error.code === 'PLAYWRIGHT_INVALID',
  );
});

test('installation request is local, approval-gated, and skips browser download', () => {
  const request = playwrightInstallRequest();
  assert.equal(request.user_confirmation_required, true);
  assert.match(request.command, /PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1/);
  assert.match(request.command, /npm install --prefix/);
  assert.doesNotMatch(request.command, /npm install -g/);
});

test('managed CDP resolver uses the shared skill and returns its verified dynamic endpoint', () => {
  const calls = [];
  const spawn = (command, args) => {
    calls.push({ command, args });
    return {
      status: 0,
      stdout: JSON.stringify({
        ready: true,
        started: true,
        session: 'android-daily-sheet-roundtrip',
        cdp_http_endpoint: 'http://127.0.0.1:43123',
      }),
      stderr: '',
    };
  };
  const result = resolveManagedCdpEndpoint(undefined, { spawn, ensureScript: '/tmp/ensure_session.py' });
  assert.equal(result.endpoint, 'http://127.0.0.1:43123');
  assert.equal(result.source, 'managed-edge-started');
  assert.deepEqual(calls[0].args, ['/tmp/ensure_session.py', '--session', 'android-daily-sheet-roundtrip']);
});

test('explicit CDP override is local-only and still uses shared ownership validation', () => {
  const calls = [];
  const local = resolveManagedCdpEndpoint('http://127.0.0.1:53123', {
    ensureScript: '/tmp/ensure_session.py',
    spawn: (command, args) => {
      calls.push({ command, args });
      return {
        status: 0,
        stdout: JSON.stringify({
          ready: true,
          session: 'android-daily-sheet-roundtrip',
          cdp_http_endpoint: 'http://127.0.0.1:53123',
        }),
        stderr: '',
      };
    },
  });
  assert.equal(local.source, 'managed-fixed-port-override');
  assert.deepEqual(calls[0].args.slice(-2), ['--port', '53123']);
  assert.throws(
    () => resolveManagedCdpEndpoint('http://192.168.1.10:9223'),
    (error) => error instanceof BrowserPrerequisiteError && error.code === 'CDP_OVERRIDE_INVALID',
  );
});

test('managed CDP resolver rejects a mismatched session identity', () => {
  assert.throws(
    () => resolveManagedCdpEndpoint(undefined, {
      ensureScript: '/tmp/ensure_session.py',
      spawn: () => ({
        status: 0,
        stdout: JSON.stringify({ ready: true, session: 'other', cdp_http_endpoint: 'http://127.0.0.1:43123' }),
        stderr: '',
      }),
    }),
    (error) => error instanceof BrowserPrerequisiteError && error.code === 'EDGE_CDP_START_FAILED',
  );
});
