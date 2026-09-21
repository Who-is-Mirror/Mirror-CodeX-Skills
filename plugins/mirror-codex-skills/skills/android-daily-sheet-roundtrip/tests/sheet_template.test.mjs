import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { calculateRowHeights, expectedSheetCells, validateTemplate } from '../scripts/sheet_template.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const template = JSON.parse(readFileSync(resolve(root, 'assets', 'daily-sheet-template.json'), 'utf8'));

test('canonical fake template validates and contains no forbidden real marker', () => {
  validateTemplate(template);
  const text = template.values.flat().join('\n');
  for (const marker of template.forbidden_real_markers) assert.equal(text.includes(marker), false, marker);
});

test('footer content maps from E into merged D anchor and skips covered E/F cells', () => {
  const cells = expectedSheetCells(template.values, template.layout.footer_rows);
  assert.equal(cells.get('D14').startsWith('- 需要示例设备'), true);
  assert.equal(cells.has('E14'), false);
  assert.equal(cells.has('F14'), false);
  assert.equal(cells.size, 150);
  assert.equal([...cells.values()].filter(Boolean).length, 72);
});

test('row-height calculator honors explicit lines, merged width and separator', () => {
  const heights = calculateRowHeights(template, template.values, (character) => /[\u0000-\u00ff]/.test(character) ? 7 : 14);
  assert.equal(heights[16], 50);
  assert.ok(heights[14] >= 92);
  assert.ok(heights[4] >= 72);
  for (const [row, height] of Object.entries(heights)) {
    if (Number(row) === 16) continue;
    assert.ok(height >= 52 && height <= 160 && height % 2 === 0);
  }
});
