import test from 'node:test';
import assert from 'node:assert/strict';
import { COMPACTION_SCHEMA, validateCompactionPlan } from '../scripts/compact_daily_sheet_rows.mjs';

test('validates one guarded contiguous row compaction', () => {
  const plan = validateCompactionPlan({ schema: COMPACTION_SCHEMA, delete_rows: [{
    start_row: 22, end_row: 24,
    anchors_before: [{ cell: 'c25', value: '1. 任务' }],
    anchors_after: [{ cell: 'C22', value: '1. 任务' }],
  }] });
  assert.deepEqual(plan.delete_rows[0], {
    start_row: 22, end_row: 24,
    anchors_before: [{ cell: 'C25', value: '1. 任务' }],
    anchors_after: [{ cell: 'C22', value: '1. 任务' }],
  });
});

test('rejects header deletion and missing anchors', () => {
  assert.throws(() => validateCompactionPlan({ schema: COMPACTION_SCHEMA, delete_rows: [{ start_row: 1, end_row: 1, anchors_before: [{}], anchors_after: [{}] }] }), /第 2 至 500 行/);
  assert.throws(() => validateCompactionPlan({ schema: COMPACTION_SCHEMA, delete_rows: [{ start_row: 2, end_row: 3, anchors_before: [], anchors_after: [] }] }), /不能为空/);
});

test('rejects an unbounded range or a before anchor inside the deletion block', () => {
  assert.throws(() => validateCompactionPlan({ schema: COMPACTION_SCHEMA, delete_rows: [{ start_row: 2, end_row: 999, anchors_before: [{ cell: 'A1000', value: 'x' }], anchors_after: [{ cell: 'A2', value: 'x' }] }] }), /第 2 至 500 行/);
  assert.throws(() => validateCompactionPlan({ schema: COMPACTION_SCHEMA, delete_rows: [{ start_row: 2, end_row: 3, anchors_before: [{ cell: 'A2', value: '' }], anchors_after: [{ cell: 'A2', value: 'x' }] }] }), /不得位于待删除范围/);
});
