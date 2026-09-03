import test from 'node:test';
import assert from 'node:assert/strict';

import { buildSnapshot, SNAPSHOT_SCHEMA } from '../scripts/capture_daily_sheet_snapshot.mjs';
import { normalizeOfflineCells } from '../scripts/daily_sheet_to_facts.mjs';

test('snapshot trims trailing blank rows and remains converter-compatible', () => {
  const snapshot = buildSnapshot({
    documentId: 'doc-1',
    sheetName: '2026-09-03',
    sheetId: 'sheet-1',
    grid: [
      ['项目 / 客户', '类型', '任务', '分项', '内容', '状态'],
      ['Other', '测试', '今日概况', '今日主题', '主题', ''],
      ['', '', '', '', '', ''],
      ['', '', '', '', '', ''],
    ],
    heights: { 1: 30, 2: 44 },
    widths: { A: 200 },
    merges: ['D10:F10'],
  });
  assert.equal(snapshot.schema, SNAPSHOT_SCHEMA);
  assert.equal(snapshot.row_count, 2);
  assert.equal(snapshot.cells.A2, 'Other');
  assert.equal(normalizeOfflineCells(snapshot).length, 2);
  assert.equal(snapshot.layout.merges[0], 'D10:F10');
  assert.match(snapshot.snapshot_sha256, /^[a-f0-9]{64}$/);
});

test('snapshot hash is stable for identical values', () => {
  const input = { documentId: 'd', sheetName: 's', sheetId: 'i', grid: [['a', 'b', 'c', 'd', 'e', 'f']] };
  assert.equal(buildSnapshot(input).value_sha256, buildSnapshot(input).value_sha256);
});

test('snapshot preserves repeated newlines for accidental spacing detection', () => {
  const snapshot = buildSnapshot({
    documentId: 'd', sheetName: 's', sheetId: 'i',
    grid: [['a', 'b', 'c', 'd', '- 第一条\n\n- 第二条', 'f']],
  });
  assert.equal(snapshot.cells.E1, '- 第一条\n\n- 第二条');
});

test('snapshot rejects malformed or empty grids', () => {
  assert.throws(() => buildSnapshot({ grid: [['a']] }), /六列/);
  assert.throws(() => buildSnapshot({ grid: [['', '', '', '', '', '']] }), /快照为空/);
});
