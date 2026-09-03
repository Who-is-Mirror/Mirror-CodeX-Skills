import test from 'node:test';
import assert from 'node:assert/strict';
import { REPAIR_SCHEMA, validateRepairPlan } from '../scripts/apply_daily_sheet_repairs.mjs';

test('normalizes a valid A:F repair plan', () => {
  assert.deepEqual(validateRepairPlan({
    schema: REPAIR_SCHEMA,
    repairs: [{ cell: 'c25', before: '2. 任务', after: '1. 任务', kind: 'renumber', reason: '前项已删除' }],
  }).repairs[0], { cell: 'C25', before: '2. 任务', after: '1. 任务', kind: 'renumber', reason: '前项已删除' });
});

test('rejects duplicate, out-of-range, and no-op repairs', () => {
  assert.throws(() => validateRepairPlan({ schema: REPAIR_SCHEMA, repairs: [{ cell: 'G1', before: '', after: 'x', kind: 'restore', reason: 'x' }] }), /A:F/);
  assert.throws(() => validateRepairPlan({ schema: REPAIR_SCHEMA, repairs: [{ cell: 'A1', before: 'x', after: 'x', kind: 'rewrite', reason: 'x' }] }), /相同/);
  assert.throws(() => validateRepairPlan({ schema: REPAIR_SCHEMA, repairs: [
    { cell: 'A1', before: '', after: 'x', kind: 'restore', reason: 'x' },
    { cell: 'a1', before: '', after: 'y', kind: 'restore', reason: 'y' },
  ] }), /重复/);
  assert.throws(() => validateRepairPlan({ schema: REPAIR_SCHEMA, repairs: [{ cell: 'A1', before: '', after: 'x', kind: 'anything', reason: 'x' }] }), /kind 不受支持/);
});

test('preserves exact whitespace preconditions and rejects formula-like replacement', () => {
  const plan = validateRepairPlan({ schema: REPAIR_SCHEMA, repairs: [{ cell: 'E3', before: '  原值  ', after: '  新值  ', kind: 'restore', reason: '用户确认' }] });
  assert.equal(plan.repairs[0].before, '  原值  ');
  assert.equal(plan.repairs[0].after, '  新值  ');
  assert.throws(() => validateRepairPlan({ schema: REPAIR_SCHEMA, repairs: [{ cell: 'E3', before: '原值', after: '=1+1', kind: 'restore', reason: '用户确认' }] }), /疑似公式/);
});
