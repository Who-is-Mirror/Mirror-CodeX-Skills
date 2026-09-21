import assert from 'node:assert/strict';
import test from 'node:test';
import { isValidDateTab, planDateTabRetention } from '../scripts/date_tab_retention.mjs';

test('validates real calendar dates', () => {
  assert.equal(isValidDateTab('2026-09-02'), true);
  assert.equal(isValidDateTab('2026-02-30'), false);
  assert.equal(isValidDateTab('工作表1'), false);
});

test('bootstraps one blank non-date worksheet', () => {
  assert.deepEqual(planDateTabRetention(['工作表1'], '2026-09-02', { allowBlankBootstrap: true }), {
    action: 'bootstrap', bootstrapTab: '工作表1', deleteTabs: [], createTab: false,
    targetDate: '2026-09-02', desiredOrder: ['2026-09-02'],
  });
});

test('appends below five and sorts newest first', () => {
  const plan = planDateTabRetention(['2026-09-01', '2026-08-31'], '2026-09-02');
  assert.equal(plan.action, 'append');
  assert.deepEqual(plan.deleteTabs, []);
  assert.deepEqual(plan.desiredOrder, ['2026-09-02', '2026-09-01', '2026-08-31']);
});

test('rotates the oldest date when five are retained', () => {
  const plan = planDateTabRetention([
    '2026-09-01', '2026-08-31', '2026-08-30', '2026-08-29', '2026-08-28',
  ], '2026-09-02');
  assert.equal(plan.action, 'rotate-oldest');
  assert.deepEqual(plan.deleteTabs, ['2026-08-28']);
  assert.deepEqual(plan.desiredOrder, [
    '2026-09-02', '2026-09-01', '2026-08-31', '2026-08-30', '2026-08-29',
  ]);
});

test('replaces an existing date only with explicit authorization', () => {
  assert.throws(() => planDateTabRetention(['2026-09-02'], '2026-09-02'), /显式允许/);
  const plan = planDateTabRetention(['2026-09-02', '2026-09-01'], '2026-09-02', { replaceExisting: true });
  assert.equal(plan.action, 'replace-existing');
  assert.deepEqual(plan.deleteTabs, ['2026-09-02']);
  assert.deepEqual(plan.desiredOrder, ['2026-09-02', '2026-09-01']);
});

test('fails closed on mixed or duplicate tabs', () => {
  assert.throws(() => planDateTabRetention(['2026-09-01', '备注'], '2026-09-02'), /非日期页签/);
  assert.throws(() => planDateTabRetention(['2026-09-01', '2026-09-01'], '2026-09-02'), /重复页签/);
});

