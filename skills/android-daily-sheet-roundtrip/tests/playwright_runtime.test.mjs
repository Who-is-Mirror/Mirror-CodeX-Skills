import test from 'node:test';
import assert from 'node:assert/strict';
import {
  BrowserPrerequisiteError,
  normalizePlaywrightApi,
  playwrightInstallRequest,
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
