import test from 'node:test';
import assert from 'node:assert/strict';

import { evaluateStageBSnapshot } from '../scripts/run_stage_b_fast.mjs';

const rows = [
  ['项目 / 客户', '类型', '任务', '分项', '内容', '状态'],
  ['Other', '测试工作', '今日概况', '今日主题', '测试主题', ''],
  ['', '', '', '当前结果', '测试结果', ''],
  ['', '', '1. 第一项', '做了什么', '- 完成一', ''],
  ['', '', '', '怎么做的', '- 方法一', ''],
  ['', '', '', '结果', '结果一', '已完成'],
  ['', '', '重点说明', '', '无。', ''],
  ['', '', '依赖 / 需协调', '', '无。', ''],
  ['', '', '明日计划', '', '无。', ''],
];

const baseline = { schema: 'daily-sheet-rows-v1', grid_data: { values: rows } };
const snapshot = { schema: 'android-daily-sheet-snapshot-v2', rows: rows.map((row, index) => ({ rowNumber: index + 1, ...Object.fromEntries('ABCDEF'.split('').map((column, columnIndex) => [column, row[columnIndex]])) })) };

test('one captured snapshot is both audited and converted when it passes', () => {
  const result = evaluateStageBSnapshot(baseline, snapshot, { reportDate: '2026-09-03' });
  assert.equal(result.audit.status, 'PASS');
  assert.equal(result.facts.report_date, '2026-09-03');
  assert.equal(result.facts.standalone_work[0].work_name, '测试工作');
});

test('a blocked snapshot never reaches conversion', () => {
  const broken = structuredClone(snapshot);
  broken.rows[3].E = '';
  const result = evaluateStageBSnapshot(baseline, broken, { reportDate: '2026-09-03' });
  assert.equal(result.audit.status, 'BLOCKED');
  assert.equal(result.facts, null);
});
