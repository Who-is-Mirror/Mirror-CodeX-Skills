import test from 'node:test';
import assert from 'node:assert/strict';
import { COMPACTION_SCHEMA, validateCompactionPlan } from '../scripts/compact_daily_sheet_rows.mjs';

const audit = (findings = [{ finding_id: 'missing:one', type: 'missing_task', accepted: true, baseline_rows: { start: 22, end: 24 } }]) => ({
  schema: 'android-daily-sheet-change-audit-v1', baseline_sha256: 'baseline', current_sha256: 'current', findings,
});

const plan = (operations, overrides = {}) => ({
  schema: COMPACTION_SCHEMA, baseline_sha256: 'baseline', current_sha256: 'current', operations, ...overrides,
});

test('validates one guarded audit-bound contiguous row compaction', () => {
  const checked = validateCompactionPlan(plan([{
    action: 'delete', start_row: 22, end_row: 24, reason: '删除已确认任务的空白行',
    finding_id: 'missing:one',
    anchors_before: [{ cell: 'c25', value: '1. 任务' }],
    anchors_after: [{ cell: 'C22', value: '1. 任务' }],
  }]), audit());
  assert.deepEqual(checked.operations[0], {
    action: 'delete', start_row: 22, end_row: 24, reason: '删除已确认任务的空白行',
    finding_id: 'missing:one',
    anchors_before: [{ cell: 'C25', value: '1. 任务' }],
    anchors_after: [{ cell: 'C22', value: '1. 任务' }],
  });
});

test('rejects stale, unconfirmed, oversized, header, and anchorless deletion', () => {
  const operation = { action: 'delete', start_row: 22, end_row: 24, finding_id: 'missing:one', reason: '删除已确认任务的空白行', anchors_before: [{ cell: 'C25', value: 'x' }], anchors_after: [{ cell: 'C22', value: 'x' }] };
  assert.throws(() => validateCompactionPlan(plan([operation], { current_sha256: 'stale' }), audit()), /STALE_COMPACTION_PLAN/);
  assert.throws(() => validateCompactionPlan(plan([{ ...operation, end_row: 25 }]), audit()), /精确行范围|不得位于/);
  assert.throws(() => validateCompactionPlan(plan([{ ...operation, start_row: 1, end_row: 1 }]), audit()), /第 2 至 500 行/);
  assert.throws(() => validateCompactionPlan(plan([{ ...operation, anchors_before: [], anchors_after: [] }]), audit()), /不能为空/);
  assert.throws(() => validateCompactionPlan(plan([operation]), audit([{ ...audit().findings[0], accepted: false }])), /审计建议|精确行范围/);
});

test('rejects an unbounded range or a before anchor inside the deletion block', () => {
  const finding = { finding_id: 'missing:top', type: 'missing_task', accepted: true, baseline_rows: { start: 2, end: 3 } };
  assert.throws(() => validateCompactionPlan(plan([{ action: 'delete', start_row: 2, end_row: 999, finding_id: 'missing:top', reason: '删除', anchors_before: [{ cell: 'A1000', value: 'x' }], anchors_after: [{ cell: 'A2', value: 'x' }] }]), audit([finding])), /第 2 至 500 行/);
  assert.throws(() => validateCompactionPlan(plan([{ action: 'delete', start_row: 2, end_row: 3, finding_id: 'missing:top', reason: '删除', anchors_before: [{ cell: 'A2', value: '' }], anchors_after: [{ cell: 'A2', value: 'x' }] }]), audit([finding])), /不得位于待删除范围/);
});

test('accepts multiple confirmed disjoint blocks and sorts them bottom to top', () => {
  const findings = [
    { finding_id: 'missing:top', type: 'missing_task', accepted: true, baseline_rows: { start: 4, end: 6 } },
    { finding_id: 'missing:bottom', type: 'missing_task', accepted: true, baseline_rows: { start: 10, end: 12 } },
  ];
  const operation = (findingId, start, end) => ({ action: 'delete', finding_id: findingId, start_row: start, end_row: end, reason: '删除已确认任务的空白行', anchors_before: [{ cell: 'A1', value: 'header' }], anchors_after: [{ cell: 'A1', value: 'header' }] });
  const checked = validateCompactionPlan(plan([operation('missing:top', 4, 6), operation('missing:bottom', 10, 12)]), audit(findings));
  assert.deepEqual(checked.operations.map((item) => item.finding_id), ['missing:bottom', 'missing:top']);
});

test('accepts an exact audit-generated separator insertion and rejects expansion', () => {
  const operation = {
    action: 'insert', start_row: 10, end_row: 10, finding_id: 'separator:one', reason: '在两个范围之间插入一个空白分隔行',
    anchors_before: [{ cell: 'C9', value: '明日计划' }, { cell: 'C10', value: '今日概况' }],
    anchors_after: [{ cell: 'C9', value: '明日计划' }, { cell: 'C11', value: '今日概况' }],
  };
  const evidence = { schema: 'android-daily-sheet-change-audit-v1', baseline_sha256: 'baseline', current_sha256: 'current', findings: [], suggested_row_repairs: [operation] };
  assert.equal(validateCompactionPlan(plan([operation]), evidence).operations[0].action, 'insert');
  assert.throws(() => validateCompactionPlan(plan([{ ...operation, end_row: 11 }]), evidence), /审计建议/);
});
