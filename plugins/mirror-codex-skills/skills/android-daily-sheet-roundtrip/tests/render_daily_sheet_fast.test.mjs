import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../scripts/render_daily_sheet_fast.mjs', import.meta.url), 'utf8');

test('capability-gated paste loader falls through to the same module bottom batch entry', () => {
  const loaderCall = source.indexOf('await action.paste();');
  const blankRecheck = source.indexOf('firstCell = normalizeCell(await readFirstCell());', loaderCall);
  const coreCall = source.indexOf('await apiDoPaste({ workbook: action._Rj, view: action._ix, behaviorApi: action._yv });', blankRecheck);
  assert.ok(loaderCall >= 0);
  assert.ok(blankRecheck > loaderCall);
  assert.ok(coreCall > blankRecheck);
  assert.match(source, /pasteCoreModule:\s*829513/);
  assert.match(source, /webpackRequire\(modules\.pasteCoreModule\)\?\.tZ/);
  assert.match(source, /direct_core_fallback:\s*true/);
});

test('network error prompt is dismissed before verification screenshots', () => {
  const close = source.indexOf("page.locator('#errorDialogClose')");
  const screenshots = source.indexOf("const screenshotRefs = [['A1', 'top-left']");
  assert.ok(close >= 0);
  assert.ok(screenshots > close);
});
