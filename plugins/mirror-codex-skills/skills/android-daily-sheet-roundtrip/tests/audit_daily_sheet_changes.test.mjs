import test from 'node:test';
import assert from 'node:assert/strict';
import { auditSheetChanges, DECISIONS_SCHEMA, validateDecisionLedger } from '../scripts/audit_daily_sheet_changes.mjs';

const rows = [
  ['项目 / 客户', '类型', '任务', '内容', '说明', '状态'],
  ['Other', '测试工作', '今日概况', '今日主题', '- 测试主题', ''],
  ['', '', '', '当前结果', '- 测试结果', ''],
  ['', '', '1. 第一项', '做了什么', '- 完成一', ''],
  ['', '', '', '怎么做的', '- 方法一', ''],
  ['', '', '', '结果', '- 结果一', '已完成'],
  ['', '', '2. 第二项', '做了什么', '- 完成二', ''],
  ['', '', '', '怎么做的', '- 方法二', ''],
  ['', '', '', '结果', '- 结果二', '处理中'],
  ['', '', '重点说明', '', '无。', ''],
  ['', '', '依赖 / 需协调', '', '无。', ''],
  ['', '', '明日计划', '', '无。', ''],
];

const baseline = () => ({ schema: 'daily-sheet-rows-v1', grid_data: { values: structuredClone(rows) } });
const current = (values) => ({ rows: values.map((row, index) => ({ rowNumber: index + 1, ...Object.fromEntries('ABCDEF'.split('').map((column, columnIndex) => [column, row[columnIndex] ?? ''])) })) });

test('unchanged valid sheet passes', () => {
  const audit = auditSheetChanges(baseline(), current(structuredClone(rows)));
  assert.equal(audit.status, 'PASS');
  assert.deepEqual(audit.summary, { scopes: 1, ordinary_modifications: 0, blockers: 0, confirmations: 0, accepted_confirmations: 0, deterministic_repairs: 0 });
});

test('ordinary content edit is preserved and recorded', () => {
  const edited = structuredClone(rows);
  edited[3][4] = '- 用户修改内容';
  const audit = auditSheetChanges(baseline(), current(edited));
  assert.equal(audit.status, 'PASS');
  assert.equal(audit.user_modifications[0].semantic, 'did');
  assert.equal(audit.user_modifications[0].after, '- 用户修改内容');
});

test('partial task clear blocks instead of guessing', () => {
  const edited = structuredClone(rows);
  edited[3][2] = '';
  const audit = auditSheetChanges(baseline(), current(edited));
  assert.equal(audit.status, 'BLOCKED');
  assert.ok(audit.findings.some((finding) => finding.type === 'missing_task_heading'));
});

test('complete task clear requests confirmation and reports blank hole plus renumbering', () => {
  const edited = structuredClone(rows);
  for (let row = 3; row <= 5; row += 1) edited[row] = ['', '', '', '', '', ''];
  const audit = auditSheetChanges(baseline(), current(edited));
  assert.equal(audit.status, 'REVIEW_REQUIRED');
  assert.ok(audit.findings.some((finding) => finding.type === 'missing_task'));
  assert.ok(audit.findings.some((finding) => finding.type === 'internal_blank_rows'));
  assert.ok(audit.findings.some((finding) => finding.type === 'task_numbering_drift'));
});

test('invalid status and unknown content row block preparation', () => {
  const edited = structuredClone(rows);
  edited[8][5] = '差不多完成';
  edited.splice(9, 0, ['', '', '意外内容', '', '无法归类', '']);
  const audit = auditSheetChanges(baseline(), current(edited));
  assert.equal(audit.status, 'BLOCKED');
  assert.ok(audit.findings.some((finding) => finding.type === 'invalid_task_status'));
  assert.ok(audit.findings.some((finding) => finding.type === 'unknown_nonblank_row'));
});

test('missing whole scope requires confirmation', () => {
  const edited = [rows[0]];
  const audit = auditSheetChanges(baseline(), current(edited));
  assert.equal(audit.status, 'BLOCKED');
  assert.ok(audit.findings.some((finding) => finding.type === 'missing_or_identity_changed_scope'));
  assert.ok(audit.findings.some((finding) => finding.type === 'no_scope'));
});

test('confirmed physical task deletion converges to PASS only after compaction and renumbering', () => {
  const edited = structuredClone(rows);
  edited.splice(3, 3);
  edited[3][2] = '1. 第二项';
  const first = auditSheetChanges(baseline(), current(edited));
  assert.equal(first.status, 'REVIEW_REQUIRED');
  const deletion = first.findings.find((finding) => finding.type === 'missing_task');
  const consistency = first.findings.find((finding) => finding.type === 'dependent_consistency_review');
  assert.ok(deletion?.finding_id);
  assert.equal(consistency.cells.length, 5);
  const confirmed = auditSheetChanges(baseline(), current(edited), { acceptedFindingIds: [deletion.finding_id, consistency.finding_id] });
  assert.equal(confirmed.status, 'PASS');
  assert.equal(confirmed.summary.accepted_confirmations, 2);
});

test('task movement and duplicate paste require explicit confirmation', () => {
  const moved = structuredClone(rows);
  const first = moved.splice(3, 3);
  moved.splice(6, 0, ...first);
  moved[3][2] = '1. 第二项';
  moved[6][2] = '2. 第一项';
  const movedAudit = auditSheetChanges(baseline(), current(moved));
  assert.ok(movedAudit.findings.some((finding) => finding.type === 'task_order_changed'));

  const duplicated = structuredClone(rows);
  duplicated.splice(9, 0, ...structuredClone(rows.slice(3, 6)));
  duplicated[9][2] = '3. 第一项';
  const duplicateAudit = auditSheetChanges(baseline(), current(duplicated));
  assert.ok(duplicateAudit.findings.some((finding) => finding.type === 'duplicate_task'));
});

test('duplicate confirmation findings receive distinct IDs and are accepted independently', () => {
  const duplicated = structuredClone(rows);
  const newTask = [['', '', '3. 重复新增', '做了什么', '- 做', ''], ['', '', '', '怎么做的', '- 方法', ''], ['', '', '', '结果', '- 结果', '已完成']];
  duplicated.splice(9, 0, ...structuredClone(newTask), ...structuredClone(newTask));
  duplicated[12][2] = '4. 重复新增';
  const first = auditSheetChanges(baseline(), current(duplicated));
  const additions = first.findings.filter((finding) => finding.type === 'added_or_renamed_task' && finding.task === '重复新增');
  assert.equal(additions.length, 2);
  assert.equal(new Set(additions.map((finding) => finding.finding_id)).size, 2);
  const accepted = auditSheetChanges(baseline(), current(duplicated), { acceptedFindingIds: [additions[0].finding_id] });
  assert.equal(accepted.findings.filter((finding) => finding.type === 'added_or_renamed_task' && finding.accepted).length, 1);
});

test('a decision ledger cannot suppress blockers', () => {
  const edited = structuredClone(rows);
  edited[8][5] = '非法状态';
  const first = auditSheetChanges(baseline(), current(edited));
  const blocker = first.findings.find((finding) => finding.type === 'invalid_task_status');
  const second = auditSheetChanges(baseline(), current(edited), { acceptedFindingIds: [blocker.finding_id] });
  assert.equal(second.status, 'BLOCKED');
  assert.equal(second.findings.find((finding) => finding.finding_id === blocker.finding_id).accepted, false);
});

test('cleared required prose blocks and is not mislabeled as an ordinary edit', () => {
  for (const [rowIndex, semantic] of [[1, 'today_topic'], [2, 'current_result'], [3, 'did'], [4, 'how'], [5, 'result']]) {
    const edited = structuredClone(rows);
    edited[rowIndex][4] = '';
    const audit = auditSheetChanges(baseline(), current(edited));
    assert.equal(audit.status, 'BLOCKED', semantic);
    assert.ok(audit.findings.some((finding) => finding.type === 'missing_required_content' && finding.semantic === semantic));
    assert.ok(!audit.user_modifications.some((item) => item.semantic === semantic));
    const repair = audit.suggested_repairs.find((item) => item.semantic === semantic);
    assert.equal(repair.cell, `E${rowIndex + 1}`);
    assert.equal(repair.before, '');
    assert.equal(repair.after, rows[rowIndex][4]);
    assert.ok(repair.finding_id);
  }
});

test('footer edits are disclosed and footer clears block', () => {
  const edited = structuredClone(rows);
  edited[9][3] = '';
  edited[9][4] = '- 用户修改重点';
  const audit = auditSheetChanges(baseline(), current(edited));
  assert.equal(audit.status, 'PASS');
  assert.ok(audit.user_modifications.some((item) => item.semantic === 'key_points' && item.after === '- 用户修改重点'));

  edited[9][3] = '';
  edited[9][4] = '';
  edited[9][5] = '';
  assert.equal(auditSheetChanges(baseline(), current(edited)).status, 'BLOCKED');
});

test('scope separator count and scope order are audited', () => {
  const secondScope = structuredClone(rows.slice(1));
  secondScope[0][0] = 'Other';
  secondScope[0][1] = '第二范围';
  const baseValues = [...structuredClone(rows), ['', '', '', '', '', ''], ...secondScope];
  const base = { schema: 'daily-sheet-rows-v1', grid_data: { values: baseValues } };

  const noSeparator = [...structuredClone(rows), ...structuredClone(secondScope)];
  const missingAudit = auditSheetChanges(base, current(noSeparator));
  assert.equal(missingAudit.status, 'REPAIR_REQUIRED');
  assert.deepEqual(missingAudit.suggested_row_repairs.map((operation) => operation.action), ['insert']);

  const duplicateSeparator = [...structuredClone(rows), ['', '', '', '', '', ''], ['', '', '', '', '', ''], ...structuredClone(secondScope)];
  const duplicateAudit = auditSheetChanges(base, current(duplicateSeparator));
  assert.equal(duplicateAudit.status, 'REPAIR_REQUIRED');
  assert.deepEqual(duplicateAudit.suggested_row_repairs.map((operation) => operation.action), ['delete']);

  const moved = [structuredClone(rows[0]), ...structuredClone(secondScope), ['', '', '', '', '', ''], ...structuredClone(rows.slice(1))];
  const movedAudit = auditSheetChanges(base, current(moved));
  assert.equal(movedAudit.status, 'REVIEW_REQUIRED');
  assert.ok(movedAudit.findings.some((finding) => finding.type === 'scope_order_changed'));
});

test('formula-like text, A:F exterior content, and format drift never pass silently', () => {
  const formula = structuredClone(rows);
  formula[2][4] = '=1+1';
  assert.ok(auditSheetChanges(baseline(), current(formula)).findings.some((finding) => finding.type === 'formula_or_rich_content'));

  const exterior = baseline();
  exterior.grid_data.values[2][6] = 'G 列误粘贴';
  assert.ok(auditSheetChanges(baseline(), exterior).findings.some((finding) => finding.type === 'outside_af_content'));

  const before = { ...baseline(), format_hints: { header_fill: '#8CDDFA' } };
  const after = { ...baseline(), format_hints: { header_fill: '#FFFFFF' } };
  const formatAudit = auditSheetChanges(before, after);
  assert.equal(formatAudit.status, 'REPAIR_REQUIRED');
  assert.ok(formatAudit.findings.some((finding) => finding.type === 'formatting_drift'));
});

test('malformed baseline and blocker inside an added scope cannot be confirmed away', () => {
  const malformedBaseline = baseline();
  malformedBaseline.grid_data.values[3][4] = '';
  assert.ok(auditSheetChanges(malformedBaseline, current(malformedBaseline.grid_data.values)).findings.some((finding) => finding.type === 'invalid_baseline'));

  const added = structuredClone(rows.slice(1));
  added[0][1] = '新增范围';
  added[4][5] = '非法状态';
  const edited = [...structuredClone(rows), ['', '', '', '', '', ''], ...added];
  const first = auditSheetChanges(baseline(), current(edited));
  const confirmation = first.findings.find((finding) => finding.type === 'added_or_identity_changed_scope');
  const accepted = auditSheetChanges(baseline(), current(edited), { acceptedFindingIds: [confirmation.finding_id] });
  assert.equal(accepted.status, 'BLOCKED');
  assert.ok(accepted.findings.some((finding) => finding.type === 'invalid_task_status'));
});

test('decision ledger is bound to exact baseline and current hashes', () => {
  const edited = structuredClone(rows);
  edited.splice(3, 3);
  edited[3][2] = '1. 第二项';
  const audit = auditSheetChanges(baseline(), current(edited));
  const findingId = audit.findings.find((finding) => finding.type === 'missing_task').finding_id;
  const ledger = { schema: DECISIONS_SCHEMA, baseline_sha256: audit.baseline_sha256, current_sha256: audit.current_sha256, accepted_finding_ids: [findingId] };
  assert.deepEqual(validateDecisionLedger(ledger, audit), [findingId]);
  assert.throws(() => validateDecisionLedger({ ...ledger, current_sha256: 'stale' }, audit), /STALE_DECISION_LEDGER/);
});

test('declared snapshot hash cannot hide changed row content', () => {
  const before = { schema: 'android-daily-sheet-snapshot-v2', snapshot_sha256: 'same-declared-value', ...current(structuredClone(rows)) };
  const changedRows = structuredClone(rows);
  changedRows[1][4] = '- 修改后的主题';
  const after = { schema: 'android-daily-sheet-snapshot-v2', snapshot_sha256: 'same-declared-value', ...current(changedRows) };
  const first = auditSheetChanges(before, before);
  const second = auditSheetChanges(before, after);
  assert.notEqual(first.current_sha256, second.current_sha256);
});

test('same rows on a different dated sheet cannot reuse a decision ledger', () => {
  const before = { schema: 'android-daily-sheet-snapshot-v2', document_id: 'doc-1', sheet_id: 'sheet-a', sheet_name: '2026-09-03', ...current(structuredClone(rows)) };
  const after = { ...before, sheet_id: 'sheet-b', sheet_name: '2026-09-04' };
  const first = auditSheetChanges(before, before);
  const second = auditSheetChanges(after, after);
  assert.notEqual(first.current_sha256, second.current_sha256);
  assert.equal(auditSheetChanges(before, after).status, 'BLOCKED');
});

test('partial structural label clear carries exact baseline restoration but a complete task clear does not', () => {
  const partial = structuredClone(rows);
  partial[4][3] = '';
  const partialAudit = auditSheetChanges(baseline(), current(partial));
  assert.ok(partialAudit.suggested_repairs.some((repair) => repair.cell === 'D5' && repair.after === '怎么做的'));

  const complete = structuredClone(rows);
  for (let index = 3; index <= 5; index += 1) complete[index] = ['', '', '', '', '', ''];
  const completeAudit = auditSheetChanges(baseline(), current(complete));
  assert.ok(!completeAudit.suggested_repairs.some((repair) => ['C4', 'D4', 'E4', 'D5', 'E5', 'D6', 'E6', 'F6'].includes(repair.cell)));
});

test('confirmed task deletion still blocks when unchanged summary text names the deleted task', () => {
  const named = structuredClone(rows);
  named[1][4] = '- 第一项与第二项联调';
  const base = { schema: 'daily-sheet-rows-v1', grid_data: { values: structuredClone(named) } };
  named.splice(3, 3);
  named[3][2] = '1. 第二项';
  const first = auditSheetChanges(base, current(named));
  const deletion = first.findings.find((finding) => finding.type === 'missing_task');
  const accepted = auditSheetChanges(base, current(named), { acceptedFindingIds: [deletion.finding_id] });
  assert.equal(accepted.status, 'BLOCKED');
  assert.ok(accepted.findings.some((finding) => finding.type === 'stale_summary_after_task_deletion' && finding.cells.some((cell) => cell.cell === 'E2')));
});

test('damaged later scope start cannot misattribute its footer as an earlier scope edit', () => {
  const second = structuredClone(rows.slice(1));
  second[0][1] = '第二范围';
  second[8][4] = '- 第二范围重点';
  second[9][4] = '- 第二范围依赖';
  const values = [...structuredClone(rows), ['', '', '', '', '', ''], ...second];
  const base = { schema: 'daily-sheet-rows-v1', grid_data: { values } };

  for (const columnIndex of [2, 3]) {
    const edited = structuredClone(values);
    edited[13][columnIndex] = '';
    const audit = auditSheetChanges(base, current(edited));
    assert.equal(audit.status, 'BLOCKED');
    assert.deepEqual(audit.user_modifications, []);
  }
});
