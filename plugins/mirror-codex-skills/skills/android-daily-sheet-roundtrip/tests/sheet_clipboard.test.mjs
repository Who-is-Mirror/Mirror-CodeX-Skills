import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { buildDualClipboardPayload, deriveSheetLayout, refInRange } from '../scripts/sheet_clipboard.mjs';
import { calculateRowHeights, validateTemplate } from '../scripts/sheet_template.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const template = validateTemplate(JSON.parse(readFileSync(resolve(root, 'assets', 'daily-sheet-template.json'), 'utf8')));

test('dynamic layout derives footer merges, separator, wrap and bold ranges from values', () => {
  const layout = deriveSheetLayout(template, template.values);
  assert.deepEqual(layout.footer_rows, [13, 14, 15, 25, 26, 27]);
  assert.deepEqual(layout.separator_rows, [16]);
  assert.deepEqual(layout.merge_ranges, ['D13:F13', 'D14:F14', 'D15:F15', 'D25:F25', 'D26:F26', 'D27:F27']);
  assert.equal(layout.bold_ranges.includes('C14'), true);
  assert.equal(layout.bold_ranges.includes('C26'), true);
  assert.equal(layout.wrap_ranges.includes('B2:B27'), true);
  assert.equal(layout.wrap_ranges.includes('C2:C27'), true);
  assert.equal(layout.wrap_ranges.includes('E2:E27'), true);
});

test('range matching supports exact cells and rectangular ranges', () => {
  assert.equal(refInRange('C14', 'C13:C15'), true);
  assert.equal(refInRange('D14', 'D13:F15'), true);
  assert.equal(refInRange('A14', 'D13:F15'), false);
});

test('dual clipboard payload carries TSV compatibility and HTML structure', () => {
  const layout = deriveSheetLayout(template, template.values);
  const renderTemplate = { ...template, layout };
  const heights = calculateRowHeights(renderTemplate, template.values, (character) => /[\u0000-\u00ff]/.test(character) ? 7 : 14);
  const payload = buildDualClipboardPayload(template, template.values, heights, layout);
  assert.equal(payload.plain.startsWith('项目 / 客户\t类型\t任务\t分项\t内容\t状态'), true);
  assert.equal(payload.plain.includes('重点说明\t- 本页全部内容'), true);
  assert.equal((payload.html.match(/<tr\b/g) || []).length, 27);
  assert.equal((payload.html.match(/\bcolspan="3"/g) || []).length, 6);
  assert.equal(payload.html.includes('background-color:#8CDDFA'), true);
  assert.equal(payload.html.includes('width:650px'), true);
  assert.match(payload.html, /<td[^>]*white-space:normal[^>]*>App \/ SampleFactory<\/td>/);
  assert.match(payload.html, /<td[^>]*font-weight:700[^>]*>依赖 \/ 需协调<\/td>/);
  assert.equal(payload.html.includes('<br>'), true);
});
