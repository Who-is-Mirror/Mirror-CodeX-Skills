#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { normalizeOfflineCells, readCellsOverCdp } from './daily_sheet_to_facts.mjs';

export const AUDIT_SCHEMA = 'android-daily-sheet-change-audit-v1';
export const DECISIONS_SCHEMA = 'android-daily-sheet-change-decisions-v2';
const COLUMNS = ['A', 'B', 'C', 'D', 'E', 'F'];
const FOOTERS = ['重点说明', '依赖 / 需协调', '依赖/需协调', '明日计划'];
const FOOTER_SEMANTICS = new Map([['重点说明', 'key_points'], ['依赖 / 需协调', 'dependencies'], ['明日计划', 'tomorrow_plan']]);
const STATUSES = new Set(['已完成', '处理中', '待验证', '阻塞']);

const text = (value) => String(value ?? '').replace(/\r\n?/g, '\n').trim();
const blank = (row) => COLUMNS.every((column) => !text(row[column]));
const scopeKey = (row) => `${text(row.A)}\u0000${text(row.B)}`;
const stripNumber = (value) => text(value).replace(/^(?:\d+\s*[.、．:：)）-]|[（(]\d+[）)])\s*/, '');
const numberOf = (value) => Number(text(value).match(/^(\d+)\s*[.、．:：)）-]/)?.[1] ?? NaN);
const sha256 = (value) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const footerKey = (value) => text(value).replace('依赖/需协调', '依赖 / 需协调');
const footerValue = (row) => [row?.E, row?.D, row?.F].map(text).find(Boolean) ?? '';

function outsideAfContent(payload) {
  const found = [];
  for (const item of payload?.outside_template_cells ?? []) {
    const value = text(item?.value);
    if (value) found.push({ address: text(item?.address), value });
  }
  for (const [index, row] of (payload?.grid_data?.values ?? []).entries()) {
    if (!Array.isArray(row)) continue;
    for (let column = COLUMNS.length; column < row.length; column += 1) {
      const value = text(row[column]);
      if (value) found.push({ address: `${String.fromCharCode(65 + column)}${index + 1}`, value });
    }
  }
  for (const [index, row] of (payload?.rows ?? []).entries()) {
    if (!row || Array.isArray(row) || typeof row !== 'object') continue;
    for (const [key, raw] of Object.entries(row)) {
      if (!/^[G-Z]+$/i.test(key) || !text(raw)) continue;
      found.push({ address: `${key.toUpperCase()}${Number(row.rowNumber) || index + 1}`, value: text(raw) });
    }
  }
  const cells = payload?.cells && typeof payload.cells === 'object' && !Array.isArray(payload.cells) ? payload.cells : payload;
  if (cells && typeof cells === 'object' && !Array.isArray(cells)) {
    for (const [address, raw] of Object.entries(cells)) {
      if (!/^[G-Z]+[1-9]\d*$/i.test(address) || !text(raw)) continue;
      found.push({ address: address.toUpperCase(), value: text(raw) });
    }
  }
  return [...new Map(found.map((item) => [`${item.address}\u0000${item.value}`, item])).values()];
}

function formulaLikeContent(rows) {
  const found = [];
  for (const row of rows) for (const column of COLUMNS) {
    const value = text(row[column]);
    if (/^[=+@]/.test(value) || /^-\d/.test(value)) found.push({ cell: `${column}${row.rowNumber}`, value });
  }
  return found;
}

function identityFindings(scope) {
  const findings = [];
  const a = text(scope.A);
  const b = text(scope.B);
  if (a === 'Other') {
    if (!b) findings.push(issue('malformed_project_customer_type', 'blocker', `范围 ${scope.identity} 缺少 Other 工作名称`));
    if (/^GMS(?:\s*[/／]|$)/i.test(b)) findings.push(issue('lossy_gms_identity', 'blocker', `范围 ${scope.identity} 使用了当前模板不能无损表达的 GMS 类型`));
    return findings;
  }
  const identityParts = a.split(/[／/]/).map((part) => part.trim());
  if (identityParts.length !== 2 || identityParts.some((part) => !part) || !/^[A-Za-z][A-Za-z0-9._-]*$/.test(identityParts[0])) {
    findings.push(issue('malformed_project_customer_type', 'blocker', `项目/客户身份“${a}”格式非法`, { scope: scope.identity }));
  }
  if (/^GMS(?:\s*[/／]|$)/i.test(b)) findings.push(issue('lossy_gms_identity', 'blocker', `范围 ${scope.identity} 使用了当前转换器拒绝的 GMS 类型`, { scope: scope.identity }));
  else if (/^App$/i.test(b) || (/^App\s*[/／]/i.test(b) && !text(b.split(/[／/]/)[1]))) findings.push(issue('malformed_project_customer_type', 'blocker', `App 类型必须带具体 App 名称`, { scope: scope.identity }));
  else if (!/^(?:Patch|App\s*[/／]\s*.+|Doc|Other)$/i.test(b)) findings.push(issue('malformed_project_customer_type', 'blocker', `项目类型“${b}”不受支持或格式非法`, { scope: scope.identity }));
  return findings;
}

function toRows(payload) {
  const rows = normalizeOfflineCells(payload).map((row) => Object.fromEntries([
    ['rowNumber', row.rowNumber], ...COLUMNS.map((column) => [column, text(row[column])]),
  ]));
  while (rows.length && blank(rows.at(-1))) rows.pop();
  return rows;
}

function issue(type, severity, message, details = {}) {
  return { type, severity, message, ...details };
}

function parseScopes(rows) {
  const starts = [];
  for (let index = 1; index < rows.length; index += 1) {
    const row = rows[index];
    if (row.C === '今日概况' && row.D === '今日主题') starts.push(index);
  }
  const scopes = [];
  const globalFindings = [];
  for (let position = 0; position < starts.length; position += 1) {
    const start = starts[position];
    let end = (starts[position + 1] ?? rows.length) - 1;
    while (end > start && blank(rows[end])) end -= 1;
    const source = rows.slice(start, end + 1);
    const identity = scopeKey(rows[start]);
    const findings = [];
    if (!rows[start].A || !rows[start].B) findings.push(issue('missing_scope_identity', 'blocker', `第 ${rows[start].rowNumber} 行缺少范围身份`));
    if (source[1]?.D !== '当前结果') findings.push(issue('missing_current_result_row', 'blocker', `范围 ${identity} 缺少“当前结果”行`));
    if (!text(rows[start].E)) findings.push(issue('missing_required_content', 'blocker', `范围 ${identity} 的“今日主题”内容为空`, { semantic: 'today_topic', cell: `E${rows[start].rowNumber}` }));
    if (source[1]?.D === '当前结果' && !text(source[1]?.E)) findings.push(issue('missing_required_content', 'blocker', `范围 ${identity} 的“当前结果”内容为空`, { semantic: 'current_result', cell: `E${rows[start].rowNumber + 1}` }));
    if (text(rows[start].F) || text(source[1]?.F)) findings.push(issue('invalid_overview_status', 'blocker', `范围 ${identity} 的今日概况不允许填写状态`));

    const consumed = new Set([start, start + 1]);
    const tasks = [];
    const footers = [];
    for (let index = start; index <= end; index += 1) {
      const row = rows[index];
      if (row.D === '做了什么') {
        const next = rows[index + 1];
        const result = rows[index + 2];
        const task = { rowNumber: row.rowNumber, heading: row.C, name: stripNumber(row.C), number: numberOf(row.C), rows: [row, next, result] };
        tasks.push(task);
        consumed.add(index); consumed.add(index + 1); consumed.add(index + 2);
        if (!row.C || !task.name) findings.push(issue('missing_task_heading', 'blocker', `第 ${row.rowNumber} 行缺少任务标题`, { row: row.rowNumber }));
        if (next?.D !== '怎么做的') findings.push(issue('missing_task_how', 'blocker', `任务“${task.name || '未知'}”缺少“怎么做的”`, { row: row.rowNumber + 1 }));
        if (result?.D !== '结果') findings.push(issue('missing_task_result', 'blocker', `任务“${task.name || '未知'}”缺少“结果”`, { row: row.rowNumber + 2 }));
        if (!text(row.E)) findings.push(issue('missing_required_content', 'blocker', `任务“${task.name || '未知'}”的“做了什么”内容为空`, { semantic: 'did', cell: `E${row.rowNumber}` }));
        if (next?.D === '怎么做的' && !text(next?.E)) findings.push(issue('missing_required_content', 'blocker', `任务“${task.name || '未知'}”的“怎么做的”内容为空`, { semantic: 'how', cell: `E${row.rowNumber + 1}` }));
        if (result?.D === '结果' && !text(result?.E)) findings.push(issue('missing_required_content', 'blocker', `任务“${task.name || '未知'}”的“结果”内容为空`, { semantic: 'result', cell: `E${row.rowNumber + 2}` }));
        if (text(row.F) || (next?.D === '怎么做的' && text(next?.F))) findings.push(issue('invalid_task_field_status', 'blocker', `任务“${task.name || '未知'}”只有“结果”行允许填写状态`));
        if (result?.D === '结果' && !STATUSES.has(text(result?.F))) findings.push(issue('invalid_task_status', 'blocker', `任务“${task.name || '未知'}”状态缺失或非法`, { row: row.rowNumber + 2, actual: text(result?.F) }));
        task.valid = row.D === '做了什么' && next?.D === '怎么做的' && result?.D === '结果' && Boolean(text(row.E) && text(next?.E) && text(result?.E)) && STATUSES.has(text(result?.F));
      }
      if (FOOTERS.includes(row.C)) {
        footers.push({ rowNumber: row.rowNumber, label: row.C, row }); consumed.add(index);
        if (!footerValue(row)) findings.push(issue('missing_required_content', 'blocker', `“${row.C}”内容为空`, { semantic: footerKey(row.C), cell: `E${row.rowNumber}` }));
        if ([row.D, row.E, row.F].map(text).filter(Boolean).length > 1) findings.push(issue('ambiguous_footer_content', 'blocker', `“${row.C}”在 D:F 多个位置同时有内容，无法无损判断`, { row: row.rowNumber }));
        if (row.C !== footerKey(row.C)) findings.push(issue('canonical_label_drift', 'repair', `第 ${row.rowNumber} 行标签应为“${footerKey(row.C)}”`, { repairs: [{ cell: `C${row.rowNumber}`, before: row.C, after: footerKey(row.C), kind: 'canonicalize-label' }] }));
      }
    }
    const lastSemantic = Math.max(start, ...tasks.flatMap((task) => task.rows.map((row) => row?.rowNumber ? row.rowNumber - 1 : start)), ...footers.map((footer) => footer.rowNumber - 1));
    const blankRows = [];
    for (let index = start + 2; index <= lastSemantic; index += 1) if (rows[index] && blank(rows[index])) blankRows.push(rows[index].rowNumber);
    if (blankRows.length) findings.push(issue('internal_blank_rows', 'repair', `范围内存在空白断层: ${blankRows.join(', ')}`, { rows: blankRows }));

    for (let index = start + 2; index <= end; index += 1) {
      const row = rows[index];
      if (consumed.has(index) || blank(row)) continue;
      if (row.D === '怎么做的' || row.D === '结果') findings.push(issue('orphan_task_row', 'blocker', `第 ${row.rowNumber} 行是失去任务首行的“${row.D}”`, { row: row.rowNumber }));
      else findings.push(issue('unknown_nonblank_row', 'blocker', `第 ${row.rowNumber} 行无法归入已知日报结构`, { row: row.rowNumber, values: COLUMNS.map((column) => row[column]) }));
    }
    const canonicalFooters = footers.map((footer) => footerKey(footer.label));
    if (JSON.stringify(canonicalFooters) !== JSON.stringify(['重点说明', '依赖 / 需协调', '明日计划'])) {
      findings.push(issue('footer_structure_drift', 'blocker', '重点说明、依赖 / 需协调、明日计划缺失、重复或顺序错误', { actual: canonicalFooters }));
    }
    const names = tasks.map((task) => task.name);
    const duplicates = names.filter((name, index) => name && names.indexOf(name) !== index);
    if (duplicates.length) findings.push(issue('duplicate_task', 'confirm', `范围内存在重复任务: ${[...new Set(duplicates)].join('、')}`, { scope: identity, tasks: [...new Set(duplicates)] }));
    const numberRepairs = tasks.flatMap((task, index) => task.number === index + 1 ? [] : [{ cell: `C${task.rowNumber}`, before: task.heading, after: `${index + 1}. ${task.name}`, kind: 'renumber' }]);
    if (numberRepairs.length) findings.push(issue('task_numbering_drift', 'repair', '任务编号不连续', { repairs: numberRepairs }));
    const scope = { identity, A: rows[start].A, B: rows[start].B, startRow: rows[start].rowNumber, endRow: rows[end].rowNumber, rows: source, tasks, footers, findings };
    findings.push(...identityFindings(scope));
    scopes.push(scope);
  }
  if (!starts.length) globalFindings.push(issue('no_scope', 'blocker', '未找到任何“今日概况 / 今日主题”范围起始行'));
  if (starts.length) {
    for (let index = 1; index < starts[0]; index += 1) {
      if (!blank(rows[index])) globalFindings.push(issue('unknown_nonblank_row', 'blocker', `第 ${rows[index].rowNumber} 行位于首个范围之前且无法归类`, { row: rows[index].rowNumber }));
    }
    const leadingBlankCount = rows.slice(1, starts[0]).filter(blank).length;
    if (leadingBlankCount) globalFindings.push(issue('separator_count_drift', 'repair', `表头与首个范围之间存在 ${leadingBlankCount} 个空白行`, { rows: rows.slice(1, starts[0]).filter(blank).map((row) => row.rowNumber) }));
  }
  const identities = scopes.map((scope) => scope.identity);
  for (const identity of new Set(identities.filter((value, index) => identities.indexOf(value) !== index))) globalFindings.push(issue('duplicate_scope', 'confirm', `存在重复范围身份: ${identity.replace('\u0000', ' / ')}`, { scope: identity }));
  for (let index = 0; index + 1 < scopes.length; index += 1) {
    const count = scopes[index + 1].startRow - scopes[index].endRow - 1;
    if (count !== 1) globalFindings.push(issue('separator_count_drift', 'repair', `范围“${scopes[index].A} / ${scopes[index].B}”与下一范围之间应恰有 1 个空白行，实际 ${count}`, { scope: scopes[index].identity, actual: count }));
  }
  return { scopes, findings: globalFindings };
}

function compareScope(baseline, current) {
  const findings = [];
  const modifications = [];
  const baselineOverview = baseline.rows.slice(0, 2);
  const currentOverview = current.rows.slice(0, 2);
  for (const [offset, semantic] of [[0, 'today_topic'], [1, 'current_result']]) {
    const after = currentOverview[offset]?.E ?? '';
    if (text(after) && !/^[=+@]/.test(text(after)) && baselineOverview[offset]?.E !== after) modifications.push({ kind: 'text_edit', scope: current.identity, semantic, before: baselineOverview[offset]?.E ?? '', after });
  }
  const baselineByName = new Map(baseline.tasks.map((task) => [task.name, task]));
  const currentByName = new Map(current.tasks.map((task) => [task.name, task]));
  for (const task of baseline.tasks) if (!currentByName.has(task.name)) findings.push(issue('missing_task', 'confirm', `基线任务“${task.name}”在当前表格中缺失`, { scope: current.identity, task: task.name }));
  for (const task of current.tasks) if (!baselineByName.has(task.name)) findings.push(issue('added_or_renamed_task', 'confirm', `当前出现新增或改名任务“${task.name}”`, { scope: current.identity, task: task.name }));
  const hasDuplicateTasks = new Set(current.tasks.map((task) => task.name)).size !== current.tasks.length;
  const baselineOrder = baseline.tasks.map((task) => task.name).filter((name) => currentByName.has(name));
  const currentOrder = current.tasks.map((task) => task.name).filter((name) => baselineByName.has(name));
  if (!hasDuplicateTasks && JSON.stringify(baselineOrder) !== JSON.stringify(currentOrder)) findings.push(issue('task_order_changed', 'confirm', '存续任务顺序发生变化', { scope: current.identity, before: baselineOrder, after: currentOrder }));
  for (const [name, before] of baselineByName) {
    const after = currentByName.get(name);
    if (!after) continue;
    const fields = [['did', 0, 'E'], ['how', 1, 'E'], ['result', 2, 'E'], ['status', 2, 'F']];
    if (!after.valid) continue;
    for (const [semantic, offset, column] of fields) if (before.rows[offset]?.[column] !== after.rows[offset]?.[column]) modifications.push({ kind: 'text_edit', scope: current.identity, task: name, semantic, before: before.rows[offset]?.[column] ?? '', after: after.rows[offset]?.[column] ?? '' });
  }
  const missingNames = baseline.tasks.filter((task) => !currentByName.has(task.name));
  const addedNames = current.tasks.filter((task) => !baselineByName.has(task.name));
  if (baseline.tasks.length === current.tasks.length && missingNames.length === 1 && addedNames.length === 1 && JSON.stringify(baselineOrder) === JSON.stringify(currentOrder)) {
    for (let index = 0; index < baseline.tasks.length; index += 1) {
      const before = baseline.tasks[index];
      const after = current.tasks[index];
      if (before.name === after.name || !after.valid) continue;
      for (const [semantic, offset, column] of [['did', 0, 'E'], ['how', 1, 'E'], ['result', 2, 'E'], ['status', 2, 'F']]) {
        if (before.rows[offset]?.[column] !== after.rows[offset]?.[column]) modifications.push({ kind: 'text_edit', scope: current.identity, task: after.name, semantic, before: before.rows[offset]?.[column] ?? '', after: after.rows[offset]?.[column] ?? '' });
      }
    }
  }
  const baselineFooters = new Map(baseline.footers.map((footer) => [footerKey(footer.label), footerValue(footer.row)]));
  const currentFooters = new Map(current.footers.map((footer) => [footerKey(footer.label), footerValue(footer.row)]));
  for (const [semantic, before] of baselineFooters) {
    const after = currentFooters.get(semantic);
    if (after !== undefined && after && before !== after) modifications.push({ kind: 'text_edit', scope: current.identity, semantic: FOOTER_SEMANTICS.get(semantic) ?? semantic, before, after });
  }
  return { findings, modifications };
}

function stableFindingId(finding) {
  const identity = Object.fromEntries(Object.entries(finding).filter(([key]) => !['severity', 'message', 'accepted', 'finding_id'].includes(key)));
  return `${finding.type}:${sha256(identity).slice(0, 16)}`;
}

export function auditSheetChanges(baselinePayload, currentPayload, { acceptedFindingIds = [] } = {}) {
  if (!['daily-sheet-rows-v1', 'android-daily-sheet-snapshot-v1', 'android-daily-sheet-snapshot-v2'].includes(baselinePayload?.schema)) throw new Error('baseline 必须是 daily-sheet-rows-v1 或 Android 日报表格快照');
  const baselineRows = toRows(baselinePayload);
  const currentRows = toRows(currentPayload);
  const findings = [];
  const modifications = [];
  const expectedHeader = baselineRows[0];
  const actualHeader = currentRows[0];
  if (!actualHeader || COLUMNS.some((column) => expectedHeader[column] !== actualHeader[column])) findings.push(issue('header_drift', 'blocker', '表头与 Stage A 基线不一致'));
  const baseline = parseScopes(baselineRows);
  const current = parseScopes(currentRows);
  findings.push(...current.findings);
  for (const scope of current.scopes) findings.push(...scope.findings);
  const baselineProblems = [...baseline.findings, ...baseline.scopes.flatMap((scope) => scope.findings)];
  if (baselineProblems.length) findings.push(issue('invalid_baseline', 'blocker', 'Stage A 基线自身不满足日报结构，拒绝据此自动恢复', { baseline_findings: baselineProblems }));
  const outside = outsideAfContent(currentPayload);
  if (outside.length) findings.push(issue('outside_af_content', 'blocker', 'A:F 模板范围之外存在内容', { cells: outside.slice(0, 20) }));
  const formulas = formulaLikeContent(currentRows);
  if (formulas.length) findings.push(issue('formula_or_rich_content', 'blocker', '检测到公式式内容；当前模板不能无损处理', { cells: formulas.slice(0, 20) }));
  const richMetadata = (currentPayload?.raw_cell_metadata ?? []).filter((item) => item?.has_formula || item?.has_link || item?.rich_text);
  if (richMetadata.length) findings.push(issue('formula_or_rich_content', 'blocker', '检测到公式、链接或富文本对象；当前模板不能无损处理', { cells: richMetadata.slice(0, 20) }));
  if (baselinePayload?.format_hints && currentPayload?.format_hints && JSON.stringify(baselinePayload.format_hints) !== JSON.stringify(currentPayload.format_hints)) {
    findings.push(issue('formatting_drift', 'repair', '表格格式提示与 Stage A 基线不一致', { before: baselinePayload.format_hints, after: currentPayload.format_hints }));
  }
  if (baselinePayload?.layout && currentPayload?.layout && JSON.stringify(baselinePayload.layout) !== JSON.stringify(currentPayload.layout)) {
    findings.push(issue('formatting_drift', 'repair', '表格样式、合并或尺寸与 Stage A 快照不一致'));
  }
  const baselineMap = new Map(baseline.scopes.map((scope) => [scope.identity, scope]));
  const currentMap = new Map(current.scopes.map((scope) => [scope.identity, scope]));
  for (const scope of baseline.scopes) if (!currentMap.has(scope.identity)) findings.push(issue('missing_or_identity_changed_scope', 'confirm', `基线范围“${scope.A} / ${scope.B}”缺失或身份已改变`, { scope: scope.identity }));
  for (const scope of current.scopes) {
    const before = baselineMap.get(scope.identity);
    if (!before) findings.push(issue('added_or_identity_changed_scope', 'confirm', `当前范围“${scope.A} / ${scope.B}”为新增或身份已改变`, { scope: scope.identity }));
    else {
      const compared = compareScope(before, scope);
      findings.push(...compared.findings);
      modifications.push(...compared.modifications);
    }
  }
  const hasDuplicateScopes = new Set(current.scopes.map((scope) => scope.identity)).size !== current.scopes.length;
  const baselineOrder = baseline.scopes.map((scope) => scope.identity).filter((identity) => currentMap.has(identity));
  const currentOrder = current.scopes.map((scope) => scope.identity).filter((identity) => baselineMap.has(identity));
  if (!hasDuplicateScopes && JSON.stringify(baselineOrder) !== JSON.stringify(currentOrder)) findings.push(issue('scope_order_changed', 'confirm', '存续范围顺序发生变化', { before: baselineOrder, after: currentOrder }));
  const accepted = new Set(acceptedFindingIds);
  const finalizedFindings = findings.map((finding) => {
    const findingId = stableFindingId(finding);
    return { ...finding, finding_id: findingId, accepted: finding.severity === 'confirm' && accepted.has(findingId) };
  });
  const blockers = finalizedFindings.filter((finding) => finding.severity === 'blocker');
  const confirmations = finalizedFindings.filter((finding) => finding.severity === 'confirm' && !finding.accepted);
  const acceptedConfirmations = finalizedFindings.filter((finding) => finding.severity === 'confirm' && finding.accepted);
  const repairs = finalizedFindings.filter((finding) => finding.severity === 'repair');
  return {
    schema: AUDIT_SCHEMA,
    status: blockers.length ? 'BLOCKED' : confirmations.length ? 'REVIEW_REQUIRED' : repairs.length ? 'REPAIR_REQUIRED' : 'PASS',
    baseline_sha256: baselinePayload.snapshot_sha256 || sha256(baselineRows),
    current_sha256: currentPayload.snapshot_sha256 || sha256(currentRows),
    summary: { scopes: current.scopes.length, ordinary_modifications: modifications.length, blockers: blockers.length, confirmations: confirmations.length, accepted_confirmations: acceptedConfirmations.length, deterministic_repairs: repairs.length },
    user_modifications: modifications, accepted_confirmations: acceptedConfirmations, findings: finalizedFindings,
    next_action: blockers.length ? 'restore-or-decide-before-prepare' : confirmations.length ? 'confirm-intent-before-repair' : repairs.length ? 'apply-deterministic-repairs-to-sheet' : 'safe-to-run-converter',
  };
}

export function validateDecisionLedger(payload, audit) {
  if (!payload || payload.schema !== DECISIONS_SCHEMA || !Array.isArray(payload.accepted_finding_ids)) throw new Error(`decisions 必须是 ${DECISIONS_SCHEMA} 且包含 accepted_finding_ids[]`);
  if (payload.baseline_sha256 !== audit.baseline_sha256 || payload.current_sha256 !== audit.current_sha256) throw new Error('STALE_DECISION_LEDGER：确认记录与当前基线/表格快照哈希不一致');
  const confirmIds = new Set(audit.findings.filter((finding) => finding.severity === 'confirm').map((finding) => finding.finding_id));
  const accepted = payload.accepted_finding_ids.map(String);
  const invalid = accepted.filter((findingId) => !confirmIds.has(findingId));
  if (invalid.length) throw new Error(`确认记录包含不存在或不可确认的 finding_id: ${invalid.join(', ')}`);
  return accepted;
}

function parseArgs(argv) {
  const options = {};
  const allowed = new Set(['--baseline', '--current', '--cdp', '--sheet', '--decisions', '--output']);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!allowed.has(key)) throw new Error(`未知参数: ${key}`);
    const value = argv[++index];
    if (!value || value.startsWith('--')) throw new Error(`${key} 缺少值`);
    options[key.slice(2).replaceAll('-', '_')] = value;
  }
  if (!options.baseline || !options.output) throw new Error('缺少 --baseline 或 --output');
  if (Boolean(options.current) === Boolean(options.sheet)) throw new Error('--current 与 --sheet 必须且只能提供一个');
  return options;
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  const baseline = JSON.parse(await readFile(resolve(options.baseline), 'utf8'));
  const current = options.current
    ? JSON.parse(await readFile(resolve(options.current), 'utf8'))
    : await readCellsOverCdp({ cdpUrl: options.cdp || 'http://127.0.0.1:9223', sheetName: options.sheet });
  let acceptedFindingIds = [];
  if (options.decisions) {
    const decisions = JSON.parse(await readFile(resolve(options.decisions), 'utf8'));
    acceptedFindingIds = validateDecisionLedger(decisions, auditSheetChanges(baseline, current));
  }
  const audit = auditSheetChanges(baseline, current, { acceptedFindingIds });
  const target = resolve(options.output);
  await mkdir(dirname(target), { recursive: true });
  await writeFile(target, `${JSON.stringify(audit, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
  process.stdout.write(`${JSON.stringify(audit, null, 2)}\n`);
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (isMain) main().then(() => process.exit(0)).catch((error) => { process.stderr.write(`audit_daily_sheet_changes: ${error.message}\n`); process.exit(1); });
