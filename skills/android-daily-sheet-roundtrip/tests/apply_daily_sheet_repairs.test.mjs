import test from 'node:test';
import assert from 'node:assert/strict';
import { REPAIR_SCHEMA, validateRepairPlan } from '../scripts/apply_daily_sheet_repairs.mjs';
import { auditSheetChanges } from '../scripts/audit_daily_sheet_changes.mjs';

const audit = (repair = { cell: 'C25', before: '2. 任务', after: '1. 任务', kind: 'renumber', reason: '按当前存续任务顺序恢复连续编号', finding_id: 'task_numbering_drift:test' }) => ({
  schema: 'android-daily-sheet-change-audit-v1', baseline_sha256: 'baseline', current_sha256: 'current',
  findings: [{ type: 'task_numbering_drift', finding_id: repair.finding_id, repairs: [repair] }],
  suggested_repairs: [repair],
});

const plan = (repairs, overrides = {}) => ({
  schema: REPAIR_SCHEMA, baseline_sha256: 'baseline', current_sha256: 'current', repairs, ...overrides,
});

test('normalizes an audit-bound A:F repair plan', () => {
  const repair = { cell: 'C25', before: '2. 任务', after: '1. 任务', kind: 'renumber', reason: '按当前存续任务顺序恢复连续编号', finding_id: 'task_numbering_drift:test' };
  assert.deepEqual(validateRepairPlan(plan([{ ...repair, cell: 'c25' }]), audit(repair)).repairs[0], repair);
});

test('rejects stale, invented, collateral, duplicate, out-of-range, and no-op repairs', () => {
  const repair = { cell: 'C25', before: '2. 任务', after: '1. 任务', kind: 'renumber', reason: '按当前存续任务顺序恢复连续编号', finding_id: 'task_numbering_drift:test' };
  const evidence = audit(repair);
  assert.throws(() => validateRepairPlan(plan([repair], { current_sha256: 'stale' }), evidence), /STALE_REPAIR_PLAN/);
  assert.throws(() => validateRepairPlan(plan([{ ...repair, after: '编造值' }]), evidence), /精确修复不一致/);
  assert.throws(() => validateRepairPlan(plan([{ ...repair, cell: 'E8' }]), evidence), /精确修复不一致/);
  assert.throws(() => validateRepairPlan(plan([{ ...repair, cell: 'G1' }]), evidence), /A:F/);
  assert.throws(() => validateRepairPlan(plan([{ ...repair, before: repair.after }]), evidence), /相同/);
  assert.throws(() => validateRepairPlan(plan([repair, { ...repair, cell: 'c25' }]), evidence), /重复/);
  assert.throws(() => validateRepairPlan(plan([{ ...repair, kind: 'format' }]), evidence), /kind 不受支持/);
});

test('preserves exact whitespace and rejects formula-like replacement', () => {
  const repair = { cell: 'E3', before: '  原值  ', after: '  新值  ', kind: 'restore', reason: '从本次 Stage A 精确基线恢复局部误删内容', finding_id: 'partial:test' };
  const checked = validateRepairPlan(plan([repair]), audit(repair));
  assert.equal(checked.repairs[0].before, '  原值  ');
  assert.equal(checked.repairs[0].after, '  新值  ');
  const formula = { ...repair, after: '=1+1' };
  assert.throws(() => validateRepairPlan(plan([formula]), audit(formula)), /疑似公式/);
});

test('consistency rewrite is limited to cells named by a stale-summary finding', () => {
  const findingId = 'stale:test';
  const evidence = {
    schema: 'android-daily-sheet-change-audit-v1', baseline_sha256: 'baseline', current_sha256: 'current', suggested_repairs: [],
    findings: [{ type: 'stale_summary_after_task_deletion', finding_id: findingId, cells: [{ cell: 'E2' }] }],
  };
  const repair = { cell: 'E2', before: '- 旧摘要', after: '- 新摘要', kind: 'consistency-rewrite', reason: '删除任务后同步概况', finding_id: findingId };
  assert.equal(validateRepairPlan(plan([repair]), evidence).repairs[0].cell, 'E2');
  assert.throws(() => validateRepairPlan(plan([{ ...repair, cell: 'E9' }]), evidence), /不在该 finding 允许范围/);
});

test('audit-generated partial-clear suggestion is directly executable as a bound plan', () => {
  const values = [
    ['项目 / 客户', '类型', '任务', '内容', '说明', '状态'],
    ['Other', '测试', '今日概况', '今日主题', '- 主题', ''], ['', '', '', '当前结果', '- 结果', ''],
    ['', '', '1. 任务', '做了什么', '- 做', ''], ['', '', '', '怎么做的', '- 方法', ''], ['', '', '', '结果', '- 完成', '已完成'],
    ['', '', '重点说明', '', '无。', ''], ['', '', '依赖 / 需协调', '', '无。', ''], ['', '', '明日计划', '', '无。', ''],
  ];
  const baseline = { schema: 'daily-sheet-rows-v1', grid_data: { values } };
  const edited = structuredClone(values); edited[4][3] = '';
  const current = { rows: edited.map((row, index) => ({ rowNumber: index + 1, ...Object.fromEntries('ABCDEF'.split('').map((column, columnIndex) => [column, row[columnIndex]])) })) };
  const evidence = auditSheetChanges(baseline, current);
  const repair = evidence.suggested_repairs.find((item) => item.cell === 'D5');
  const checked = validateRepairPlan({ schema: REPAIR_SCHEMA, baseline_sha256: evidence.baseline_sha256, current_sha256: evidence.current_sha256, repairs: [repair] }, evidence);
  assert.equal(checked.repairs[0].after, '怎么做的');
});
