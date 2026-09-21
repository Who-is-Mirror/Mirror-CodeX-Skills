#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { isDeepStrictEqual } from 'node:util';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { loadPlaywrightRuntime, resolveManagedCdpEndpoint } from './playwright_runtime.mjs';

export const FACTS_SCHEMA = 'akbs-daily-work-facts-v4';
export const ALLOWED_STATUSES = new Set(['已完成', '处理中', '待验证', '阻塞']);

const COLUMNS = ['A', 'B', 'C', 'D', 'E', 'F'];
const SPECIAL_SECTIONS = new Map([
  ['重点说明', 'key_points'],
  ['依赖 / 需协调', 'dependencies'],
  ['依赖/需协调', 'dependencies'],
  ['明日计划', 'tomorrow_plan'],
]);
const OVERVIEW_FIELDS = new Map([
  ['今日主题', 'today_topic'],
  ['当前结果', 'current_result'],
]);
const TASK_FIELDS = new Map([
  ['做了什么', 'did'],
  ['怎么做的', 'how'],
  ['结果', 'result'],
]);
const PROJECT_WORK_TYPES = new Map([
  ['patch', 'Patch'],
  ['app', 'App'],
  ['gms', 'GMS'],
  ['doc', 'Doc'],
  ['other', 'Other'],
]);

export class DailySheetError extends Error {
  constructor(message) {
    super(message);
    this.name = 'DailySheetError';
  }
}

function fail(message) {
  throw new DailySheetError(message);
}

function normalizeText(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') {
    for (const key of ['innerText', 'text', 'value']) {
      if (value[key] !== undefined) return normalizeText(value[key]);
    }
    fail('离线单元格对象必须包含 innerText、text 或 value');
  }
  return String(value).replace(/\r\n?/g, '\n').trim();
}

function normalizeLabel(value) {
  return normalizeText(value).replace(/[ \t]+/g, ' ');
}

function isNone(value) {
  return /^(?:无|无[。.])$/.test(normalizeText(value));
}

function splitList(value) {
  const text = normalizeText(value);
  if (!text || isNone(text)) return [];

  const lines = text.split('\n');
  if (!lines.some((line) => /^\s*-/.test(line))) return [text];

  const items = [];
  for (const line of lines) {
    if (!line.trim()) continue;
    const bullet = line.match(/^\s*-\s*(.*)$/);
    if (bullet) {
      if (bullet[1].trim()) items.push(bullet[1].trim());
      continue;
    }
    if (items.length === 0) items.push(line.trim());
    else items[items.length - 1] += `\n${line.trim()}`;
  }
  return items.filter(Boolean);
}

function scalarContent(value) {
  const text = normalizeText(value);
  if (!text) return '';
  const lines = text.split('\n').filter((line) => line.trim());
  if (lines.length > 0 && lines.every((line) => /^\s*-/.test(line))) {
    return splitList(text).join('\n');
  }
  return text;
}

function validIsoDate(value, label) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value ?? '')) fail(`${label}必须是 YYYY-MM-DD`);
  const [year, month, day] = value.split('-').map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    fail(`${label}不是有效日期: ${value}`);
  }
  return value;
}

function localIsoDate(now = new Date()) {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function rowContext(rowNumber, detail) {
  return `第 ${rowNumber} 行${detail ? `（${detail}）` : ''}`;
}

function emptyRow(rowNumber) {
  return Object.fromEntries([['rowNumber', rowNumber], ...COLUMNS.map((column) => [column, ''])]);
}

function rowFromValue(value, rowNumber) {
  const row = emptyRow(rowNumber);
  if (Array.isArray(value)) {
    COLUMNS.forEach((column, index) => {
      row[column] = normalizeText(value[index]);
    });
    return row;
  }
  if (!value || typeof value !== 'object') fail(`离线 rows[${rowNumber - 1}] 必须是数组或对象`);
  for (const column of COLUMNS) row[column] = normalizeText(value[column] ?? value[column.toLowerCase()]);
  return row;
}

function rowsFromAddressEntries(entries) {
  const byRow = new Map();
  for (const [addressValue, cellValue] of entries) {
    const address = String(addressValue).toUpperCase();
    const match = address.match(/^([A-F])(\d+)$/);
    if (!match) continue;
    const rowNumber = Number(match[2]);
    if (rowNumber < 1) fail(`非法单元格地址: ${addressValue}`);
    if (!byRow.has(rowNumber)) byRow.set(rowNumber, emptyRow(rowNumber));
    byRow.get(rowNumber)[match[1]] = normalizeText(cellValue);
  }
  if (byRow.size === 0) fail('离线单元格 JSON 中没有 A:F 地址或 rows 数据');
  return [...byRow.values()].sort((left, right) => left.rowNumber - right.rowNumber);
}

export function normalizeOfflineCells(payload) {
  if (Array.isArray(payload)) {
    if (payload.every((item) => item && typeof item === 'object' && !Array.isArray(item) && item.address)) {
      return rowsFromAddressEntries(payload.map((item) => [item.address, item]));
    }
    return payload.map((row, index) => rowFromValue(row, index + 1));
  }
  if (!payload || typeof payload !== 'object') fail('离线输入必须是 JSON 对象或行数组');
  if (payload.schema === 'daily-sheet-rows-v1') {
    const values = payload.grid_data?.values;
    if (!Array.isArray(values)) fail('daily-sheet-rows-v1.grid_data.values 必须是数组');
    return values.map((row, index) => rowFromValue(row, index + 1));
  }
  if (payload.rows !== undefined) {
    if (!Array.isArray(payload.rows)) fail('rows 必须是数组');
    return payload.rows.map((row, index) => rowFromValue(row, Number(row?.rowNumber) || index + 1));
  }
  if (payload.cells !== undefined) {
    if (Array.isArray(payload.cells)) {
      return rowsFromAddressEntries(payload.cells.map((item) => [item?.address, item]));
    }
    if (!payload.cells || typeof payload.cells !== 'object') fail('cells 必须是地址映射或单元格数组');
    return rowsFromAddressEntries(Object.entries(payload.cells));
  }
  return rowsFromAddressEntries(Object.entries(payload));
}

function splitOnceOnSlash(value, description) {
  const parts = normalizeText(value).split(/[／/]/).map((part) => part.trim());
  if (parts.length !== 2 || parts.some((part) => !part)) {
    fail(`${description}必须且只能包含一个“/”分隔符`);
  }
  return parts;
}

function parseProjectIdentity(columnA) {
  const [project, customerChain] = splitOnceOnSlash(columnA, '项目/客户身份');
  if (!/^[A-Za-z][A-Za-z0-9._-]*$/.test(project)) {
    fail(`项目名“${project}”含糊；必须提供明确的项目代码`);
  }
  if (/->|=>/.test(customerChain)) fail('客户链必须使用“→”，不能猜测 ASCII 箭头含义');
  const customerParts = customerChain.split('→').map((part) => part.trim());
  if (customerParts.length > 2 || customerParts.some((part) => !part)) {
    fail(`客户链“${customerChain}”含糊；只允许“直接客户 → 下游客户”`);
  }
  const identity = { project, customer: customerParts[0] };
  if (customerParts[1]) identity.downstream_customer = customerParts[1];
  return identity;
}

function parseProjectType(columnB) {
  const parts = normalizeText(columnB).split(/[／/]/).map((part) => part.trim());
  if (parts.length > 2 || !parts[0]) fail(`项目类型“${columnB}”含糊`);
  const workType = PROJECT_WORK_TYPES.get(parts[0].toLowerCase());
  if (!workType) fail(`不支持或无法判定的项目类型: ${parts[0]}`);
  if (workType === 'GMS') fail('GMS 需要专属周期事实，当前转换器拒绝猜测');
  if (workType === 'App') {
    if (parts.length !== 2 || !parts[1]) fail('App 项目必须使用“App / 具体 App 名称”');
    return { work_type: workType, app_name: parts[1] };
  }
  if (parts.length !== 1) fail(`${workType} 项目类型不允许附带无法解释的“/”后缀`);
  return { work_type: workType };
}

function parseScopeIdentity(columnA, columnB) {
  const a = normalizeText(columnA);
  const b = normalizeText(columnB);
  if (!a || !b) fail('每个工作范围首行都必须同时提供 A 项目/客户和 B 类型/内容');
  if (a.toLowerCase() === 'other') {
    const document = b.match(/^doc\s*[／/]\s*(.+)$/i);
    if (document) {
      const documentName = normalizeText(document[1]);
      if (!documentName) fail('非项目 Doc 必须使用“Doc / 具体文档名称”');
      return { kind: 'document', work_type: 'Doc', document_name: documentName };
    }
    if (PROJECT_WORK_TYPES.has(b.toLowerCase()) || /[／/]/.test(b)) {
      fail('A=Other 时，B 必须是明确的具体工作名称，不能是类型或含糊组合');
    }
    return { kind: 'standalone', work_type: 'Other', work_name: b };
  }
  return { kind: 'project', ...parseProjectIdentity(a), ...parseProjectType(b) };
}

function taskNameFromHeading(heading, rowNumber) {
  const match = normalizeText(heading).match(/^(?:\d+\s*[.、．:：)）-]|[（(]\d+[）)])\s*(.+)$/);
  if (!match || !match[1].trim()) {
    fail(`${rowContext(rowNumber, `C=${heading}`)}：具体任务必须使用编号标题`);
  }
  return match[1].trim();
}

function sameScopeAnchor(scope, a, b) {
  return scope && normalizeText(a) === scope.rawA && (!normalizeText(b) || normalizeText(b) === scope.rawB);
}

function newScopeBuilder(a, b, rowNumber) {
  return {
    rawA: normalizeText(a),
    rawB: normalizeText(b),
    identity: parseScopeIdentity(a, b),
    startRow: rowNumber,
    currentSection: null,
    today_topic: '',
    current_result: '',
    work_items: [],
    key_points: [],
    dependencies: [],
    tomorrow_plan: [],
  };
}

function classifySection(columnC, rowNumber) {
  const label = normalizeLabel(columnC);
  if (label === '今日概况') return { kind: 'overview', label };
  const special = SPECIAL_SECTIONS.get(label);
  if (special) return { kind: 'special', label, target: special };
  return { kind: 'task', label, name: taskNameFromHeading(label, rowNumber) };
}

function startSection(scope, columnC, rowNumber) {
  const next = classifySection(columnC, rowNumber);
  if (scope.currentSection?.label === next.label) return;
  if (next.kind === 'task') {
    if (scope.work_items.some((item) => item.name === next.name)) {
      fail(`${rowContext(rowNumber)}：重复任务标题“${next.name}”`);
    }
    next.item = { name: next.name, did: [], how: [], result: '', status: '' };
    scope.work_items.push(next.item);
  }
  scope.currentSection = next;
}

function assignOverview(scope, row, section) {
  if (row.F) fail(`${rowContext(row.rowNumber, section.label)}：今日概况不允许填写状态`);
  const fieldLabel = normalizeLabel(row.D);
  const target = OVERVIEW_FIELDS.get(fieldLabel);
  if (!target) fail(`${rowContext(row.rowNumber, section.label)}：D 必须是“今日主题”或“当前结果”`);
  const content = scalarContent(row.E);
  if (!content) fail(`${rowContext(row.rowNumber, fieldLabel)}：E 内容不能为空`);
  if (scope[target]) fail(`${rowContext(row.rowNumber, fieldLabel)}：字段重复`);
  scope[target] = content;
}

function assignTask(row, section) {
  const fieldLabel = normalizeLabel(row.D);
  const target = TASK_FIELDS.get(fieldLabel);
  if (!target) fail(`${rowContext(row.rowNumber, section.label)}：D 必须是“做了什么”“怎么做的”或“结果”`);
  const content = row.E;
  if (!normalizeText(content)) fail(`${rowContext(row.rowNumber, fieldLabel)}：E 内容不能为空`);

  if (target === 'result') {
    if (!row.F) fail(`${rowContext(row.rowNumber, fieldLabel)}：F 状态必填`);
    if (!ALLOWED_STATUSES.has(row.F)) fail(`${rowContext(row.rowNumber, fieldLabel)}：非法状态“${row.F}”`);
    if (section.item.result || section.item.status) fail(`${rowContext(row.rowNumber, fieldLabel)}：结果字段重复`);
    section.item.result = scalarContent(content);
    section.item.status = row.F;
    return;
  }

  if (row.F) fail(`${rowContext(row.rowNumber, fieldLabel)}：只有“结果”行可以填写状态`);
  const items = splitList(content);
  if (items.length === 0) fail(`${rowContext(row.rowNumber, fieldLabel)}：内容不能为空`);
  if (section.item[target].length > 0) fail(`${rowContext(row.rowNumber, fieldLabel)}：字段重复`);
  section.item[target] = items;
}

function assignSpecial(scope, row, section) {
  // In unmerged rows D is often a short sub-label (for example “验证环境”)
  // while E contains the report text. A D:F merged cell exposes its value in D,
  // so prefer E and keep D/F as safe fallbacks for both layouts.
  const value = [row.E, row.D, row.F].find((cell) => normalizeText(cell));
  if (!value || isNone(value)) return;
  scope[section.target].push(...splitList(value));
}

function processDataRow(scope, row) {
  if (row.C) startSection(scope, row.C, row.rowNumber);
  const section = scope.currentSection;
  if (!section) {
    if ([row.D, row.E, row.F].some(Boolean)) fail(`${rowContext(row.rowNumber)}：D:F 有内容但缺少 C 区段`);
    return;
  }
  if (section.kind === 'overview') return assignOverview(scope, row, section);
  if (section.kind === 'task') return assignTask(row, section);
  return assignSpecial(scope, row, section);
}

function requireNonEmptyString(value, description) {
  if (typeof value !== 'string' || !value.trim()) fail(`${description}不能为空`);
}

function validateWorkItem(item, description) {
  requireNonEmptyString(item.name, `${description}.name`);
  for (const field of ['did', 'how']) {
    if (!Array.isArray(item[field]) || item[field].length === 0) fail(`${description}.${field} 必须是非空数组`);
    item[field].forEach((value, index) => requireNonEmptyString(value, `${description}.${field}[${index}]`));
  }
  requireNonEmptyString(item.result, `${description}.result`);
  if (!ALLOWED_STATUSES.has(item.status)) fail(`${description}.status 非法或缺失`);
}

function finalizeScope(scope, facts) {
  if (!scope) return;
  const scopeLabel = `从第 ${scope.startRow} 行开始的范围`;
  requireNonEmptyString(scope.today_topic, `${scopeLabel}.today_topic`);
  requireNonEmptyString(scope.current_result, `${scopeLabel}.current_result`);
  if (scope.work_items.length === 0) fail(`${scopeLabel}至少需要一个编号任务`);
  scope.work_items.forEach((item, index) => validateWorkItem(item, `${scopeLabel}.work_items[${index}]`));

  const common = {
    today_topic: scope.today_topic,
    current_result: scope.current_result,
    work_items: scope.work_items,
    key_points: scope.key_points,
    dependencies: scope.dependencies,
  };
  if (scope.identity.kind === 'project') {
    const { kind: _kind, ...identity } = scope.identity;
    facts.projects.push({ ...identity, ...common });
    if (scope.tomorrow_plan.length > 0) {
      facts.tomorrow_plan.projects.push({ ...identity, plan_items: scope.tomorrow_plan });
    }
  } else if (scope.identity.kind === 'document') {
    const { kind: _kind, ...identity } = scope.identity;
    facts.documents.push({ ...identity, ...common });
    if (scope.tomorrow_plan.length > 0) {
      facts.tomorrow_plan.documents.push({ ...identity, plan_items: scope.tomorrow_plan });
    }
  } else {
    const { kind: _kind, ...identity } = scope.identity;
    facts.standalone_work.push({ ...identity, ...common });
    if (scope.tomorrow_plan.length > 0) {
      facts.tomorrow_plan.standalone_work.push({ ...identity, plan_items: scope.tomorrow_plan });
    }
  }
}

function isHeaderRow(row) {
  const columnA = normalizeLabel(row.A).replace(/\s/g, '');
  const columnB = normalizeLabel(row.B).replace(/\s/g, '');
  return columnA === '项目/客户' && (columnB === '类型' || columnB === '类型/内容');
}

function isCompletelyEmpty(row) {
  return COLUMNS.every((column) => !row[column]);
}

export function convertDailySheet(payload, { reportDate, today = localIsoDate() } = {}) {
  validIsoDate(reportDate, '--date');
  validIsoDate(today, '当前日期');
  if (reportDate > today) fail(`未来日期被拒绝: ${reportDate} > ${today}`);

  const rows = normalizeOfflineCells(payload);
  const facts = {
    schema: FACTS_SCHEMA,
    report_date: reportDate,
    projects: [],
    documents: [],
    standalone_work: [],
    tomorrow_plan: { projects: [], documents: [], standalone_work: [] },
  };

  let scope = null;
  for (const row of rows) {
    if (isHeaderRow(row)) continue;
    if (isCompletelyEmpty(row)) {
      if (scope) scope.currentSection = null;
      continue;
    }
    if (row.A) {
      if (!sameScopeAnchor(scope, row.A, row.B)) {
        finalizeScope(scope, facts);
        scope = newScopeBuilder(row.A, row.B, row.rowNumber);
      }
    } else if (row.B) {
      fail(`${rowContext(row.rowNumber)}：B 有内容但 A 为空，工作身份含糊`);
    }
    if (!scope) fail(`${rowContext(row.rowNumber)}：缺少 A/B 工作身份`);
    processDataRow(scope, row);
  }
  finalizeScope(scope, facts);
  validateFacts(facts, { today });
  return facts;
}

function assertStringArray(value, description, { nonEmpty = false } = {}) {
  if (!Array.isArray(value) || (nonEmpty && value.length === 0)) {
    fail(`${description}必须是${nonEmpty ? '非空' : ''}数组`);
  }
  value.forEach((item, index) => requireNonEmptyString(item, `${description}[${index}]`));
}

function validateProjectIdentity(scope, description, { plan = false } = {}) {
  requireNonEmptyString(scope.project, `${description}.project`);
  requireNonEmptyString(scope.customer, `${description}.customer`);
  if (scope.downstream_customer !== undefined) requireNonEmptyString(scope.downstream_customer, `${description}.downstream_customer`);
  if (!['Patch', 'App', 'Doc', 'Other'].includes(scope.work_type)) fail(`${description}.work_type 不受支持`);
  if (scope.work_type === 'App') requireNonEmptyString(scope.app_name, `${description}.app_name`);
  else if (scope.app_name !== undefined) fail(`${description}：非 App 不允许 app_name`);
  if (plan && scope.status !== undefined) fail(`${description}：明日计划不允许状态`);
}

function validateTodayScope(scope, description, kind) {
  if (kind === 'project') validateProjectIdentity(scope, description);
  else if (kind === 'document') {
    if (!['Doc', 'Document'].includes(scope.work_type)) fail(`${description}.work_type 必须是 Doc`);
    requireNonEmptyString(scope.document_name, `${description}.document_name`);
  }
  else {
    if (scope.work_type !== 'Other') fail(`${description}.work_type 必须是 Other`);
    requireNonEmptyString(scope.work_name, `${description}.work_name`);
  }
  requireNonEmptyString(scope.today_topic, `${description}.today_topic`);
  requireNonEmptyString(scope.current_result, `${description}.current_result`);
  if (scope.status !== undefined) fail(`${description}：today_topic/current_result 范围不允许 status`);
  if (!Array.isArray(scope.work_items) || scope.work_items.length === 0) fail(`${description}.work_items 必须是非空数组`);
  scope.work_items.forEach((item, index) => validateWorkItem(item, `${description}.work_items[${index}]`));
  assertStringArray(scope.key_points, `${description}.key_points`);
  assertStringArray(scope.dependencies, `${description}.dependencies`);
}

export function validateFacts(facts, { today = localIsoDate() } = {}) {
  if (!facts || typeof facts !== 'object' || Array.isArray(facts)) fail('facts 必须是对象');
  if (facts.schema !== FACTS_SCHEMA) fail(`schema 必须是 ${FACTS_SCHEMA}`);
  validIsoDate(facts.report_date, 'report_date');
  validIsoDate(today, '当前日期');
  if (facts.report_date > today) fail(`未来日期被拒绝: ${facts.report_date} > ${today}`);
  for (const field of ['projects', 'documents', 'standalone_work']) {
    if (!Array.isArray(facts[field])) fail(`${field} 必须是数组`);
  }
  if (facts.projects.length + facts.documents.length + facts.standalone_work.length === 0) {
    fail('今天的 projects/documents/standalone_work 至少一项非空');
  }
  facts.projects.forEach((scope, index) => validateTodayScope(scope, `projects[${index}]`, 'project'));
  facts.documents.forEach((scope, index) => validateTodayScope(scope, `documents[${index}]`, 'document'));
  facts.standalone_work.forEach((scope, index) => validateTodayScope(scope, `standalone_work[${index}]`, 'standalone'));

  const plan = facts.tomorrow_plan;
  if (!plan || typeof plan !== 'object' || Array.isArray(plan)) fail('tomorrow_plan 必须是对象');
  for (const field of ['projects', 'documents', 'standalone_work']) {
    if (!Array.isArray(plan[field])) fail(`tomorrow_plan.${field} 必须是数组`);
  }
  plan.projects.forEach((scope, index) => {
    const description = `tomorrow_plan.projects[${index}]`;
    validateProjectIdentity(scope, description, { plan: true });
    assertStringArray(scope.plan_items, `${description}.plan_items`, { nonEmpty: true });
  });
  plan.documents.forEach((scope, index) => {
    const description = `tomorrow_plan.documents[${index}]`;
    if (!['Doc', 'Document'].includes(scope.work_type)) fail(`${description}.work_type 必须是 Doc`);
    requireNonEmptyString(scope.document_name, `${description}.document_name`);
    assertStringArray(scope.plan_items, `${description}.plan_items`, { nonEmpty: true });
    if (scope.status !== undefined) fail(`${description}：明日计划不允许状态`);
  });
  plan.standalone_work.forEach((scope, index) => {
    const description = `tomorrow_plan.standalone_work[${index}]`;
    if (scope.work_type !== 'Other') fail(`${description}.work_type 必须是 Other`);
    requireNonEmptyString(scope.work_name, `${description}.work_name`);
    assertStringArray(scope.plan_items, `${description}.plan_items`, { nonEmpty: true });
    if (scope.status !== undefined) fail(`${description}：明日计划不允许状态`);
  });
  return facts;
}

async function findExactVisible(locator, text) {
  const count = Math.min(await locator.count(), 100);
  const matches = [];
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if (!(await candidate.isVisible().catch(() => false))) continue;
    if (normalizeText(await candidate.innerText().catch(() => '')) === text) matches.push(candidate);
  }
  return matches;
}

async function locateSheetTab(page, sheetName) {
  const selectors = [
    '[role="tab"]',
    '.sheet-tab',
    '[class*="sheet-tab"]',
    '[class*="sheetbar"] [class*="item"]',
    '[class*="tab-item"]',
  ];
  for (const selector of selectors) {
    const matches = await findExactVisible(page.locator(selector), sheetName);
    if (matches.length === 1) return matches[0];
    if (matches.length > 1) fail(`工作表名称“${sheetName}”匹配到多个可见 tab`);
  }
  const fallback = await findExactVisible(page.getByText(sheetName, { exact: true }), sheetName);
  if (fallback.length === 1) return fallback[0];
  if (fallback.length > 1) fail(`工作表名称“${sheetName}”匹配到多个可见元素，无法安全选择 tab`);
  return null;
}

async function disconnectWithoutClosingBrowser(browser) {
  // connectOverCDP attaches to the managed dedicated browser. Closing Browser would close it;
  // close only this Playwright transport when the pinned runtime exposes the connection.
  const connection = browser?._connection;
  if (connection && typeof connection.close === 'function') {
    try {
      await connection.close();
    } catch {
      // The conversion result is already local; a transport shutdown error must not close the user browser.
    }
  }
}

export async function readCellsOverCdp({
  cdpUrl,
  sheetName,
  timeoutMs = 15_000,
  cellTimeoutMs = 3_000,
  maxRows = 500,
  emptyRowLimit = 5,
} = {}) {
  if (!normalizeText(sheetName)) fail('CDP 模式必须提供 --sheet');
  for (const [name, value] of Object.entries({ timeoutMs, cellTimeoutMs, maxRows, emptyRowLimit })) {
    if (!Number.isInteger(value) || value <= 0) fail(`${name} 必须是正整数`);
  }

  const { chromium } = await loadPlaywrightRuntime();
  const browser = await chromium.connectOverCDP(resolveManagedCdpEndpoint(cdpUrl).endpoint, { timeout: timeoutMs });
  try {
    let selectedPage = null;
    let selectedTab = null;
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        const hasNameBox = await page.locator('input.bar-label').first().isVisible().catch(() => false);
        if (!hasNameBox) continue;
        const tab = await locateSheetTab(page, normalizeText(sheetName));
        if (!tab) continue;
        if (selectedPage) fail(`多个浏览器页面包含工作表 tab“${sheetName}”，无法安全选择`);
        selectedPage = page;
        selectedTab = tab;
      }
    }
    if (!selectedPage || !selectedTab) fail(`未在 CDP 页面中找到工作表 tab“${sheetName}”和 input.bar-label`);

    selectedPage.setDefaultTimeout(timeoutMs);
    await selectedTab.click({ timeout: timeoutMs });
    const nameBox = selectedPage.locator('input.bar-label').filter({ visible: true }).first();
    const formulaInput = selectedPage.locator('.formula-input').filter({ visible: true }).first();
    await nameBox.waitFor({ state: 'visible', timeout: timeoutMs });
    await formulaInput.waitFor({ state: 'visible', timeout: timeoutMs });

    const cells = {};
    let consecutiveEmptyRows = 0;
    for (let rowNumber = 1; rowNumber <= maxRows; rowNumber += 1) {
      let rowEmpty = true;
      for (const column of COLUMNS) {
        const address = `${column}${rowNumber}`;
        await nameBox.fill(address, { timeout: cellTimeoutMs });
        await nameBox.press('Enter', { timeout: cellTimeoutMs });
        await selectedPage.waitForTimeout(25);
        const value = normalizeText(await formulaInput.evaluate(
          (element) => element.innerText,
          undefined,
          { timeout: cellTimeoutMs },
        ));
        cells[address] = value;
        if (value) rowEmpty = false;
      }
      consecutiveEmptyRows = rowEmpty ? consecutiveEmptyRows + 1 : 0;
      if (consecutiveEmptyRows >= emptyRowLimit) return { cells };
    }
    fail(`读取达到最大行数 ${maxRows}，仍未出现连续 ${emptyRowLimit} 个空行`);
  } finally {
    await disconnectWithoutClosingBrowser(browser);
  }
}

export async function writeFactsFile(outputPath, facts) {
  validateFacts(facts);
  const target = resolve(outputPath);
  let existing;
  try {
    existing = JSON.parse(await readFile(target, 'utf8'));
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      if (error instanceof SyntaxError) fail(`输出文件已存在但不是有效 JSON，拒绝覆盖: ${target}`);
      throw error;
    }
  }
  if (existing !== undefined) {
    if (!isDeepStrictEqual(existing, facts)) fail(`输出文件已存在且内容不同，拒绝覆盖: ${target}`);
    return { path: target, changed: false };
  }
  await mkdir(dirname(target), { recursive: true });
  await writeFile(target, `${JSON.stringify(facts, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
  return { path: target, changed: true };
}

function parsePositiveInteger(value, option) {
  if (!/^\d+$/.test(value ?? '') || Number(value) <= 0) fail(`${option} 必须是正整数`);
  return Number(value);
}

function parseArgs(argv) {
  const options = {};
  const valueOptions = new Set([
    '--input', '--cdp', '--sheet', '--date', '--output', '--timeout-ms',
    '--cell-timeout-ms', '--max-rows', '--empty-row-limit',
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--help' || argument === '-h') {
      options.help = true;
      continue;
    }
    if (!valueOptions.has(argument)) fail(`未知参数: ${argument}`);
    const value = argv[index + 1];
    if (value === undefined || value.startsWith('--')) fail(`${argument} 缺少值`);
    options[argument.slice(2).replaceAll('-', '_')] = value;
    index += 1;
  }
  return options;
}

function usage() {
  return `用法:
  node scripts/daily_sheet_to_facts.mjs --input <cells.json> --date YYYY-MM-DD --output <facts.json>
  node scripts/daily_sheet_to_facts.mjs [--cdp <url>] --sheet <name> --date YYYY-MM-DD --output <facts.json>

CDP 限制参数:
  --cdp <url>            受控排障覆盖；默认由 edge-cdp-session 启动并校验专用 Edge
  --timeout-ms <n>       页面/连接超时，默认 15000
  --cell-timeout-ms <n>  单元格操作超时，默认 3000
  --max-rows <n>         最大读取行数，默认 500
  --empty-row-limit <n>  连续空行停止阈值，默认 5`;
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  if (options.help) {
    process.stdout.write(`${usage()}\n`);
    return;
  }
  if (options.input && options.cdp) fail('--input 离线模式不能同时指定 --cdp');
  if (!options.date) fail('缺少 --date');
  if (!options.output) fail('缺少 --output');
  if (!options.input && !options.sheet) fail('CDP 模式缺少 --sheet');

  let payload;
  if (options.input) {
    try {
      payload = JSON.parse(await readFile(resolve(options.input), 'utf8'));
    } catch (error) {
      if (error instanceof SyntaxError) fail(`输入不是有效 JSON: ${options.input}`);
      throw error;
    }
  } else {
    payload = await readCellsOverCdp({
      cdpUrl: options.cdp,
      sheetName: options.sheet,
      timeoutMs: options.timeout_ms ? parsePositiveInteger(options.timeout_ms, '--timeout-ms') : undefined,
      cellTimeoutMs: options.cell_timeout_ms ? parsePositiveInteger(options.cell_timeout_ms, '--cell-timeout-ms') : undefined,
      maxRows: options.max_rows ? parsePositiveInteger(options.max_rows, '--max-rows') : undefined,
      emptyRowLimit: options.empty_row_limit ? parsePositiveInteger(options.empty_row_limit, '--empty-row-limit') : undefined,
    });
  }
  const facts = convertDailySheet(payload, { reportDate: options.date });
  const result = await writeFactsFile(options.output, facts);
  process.stdout.write(`${result.changed ? '已写入' : '内容相同，保持不变'}: ${result.path}\n`);
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (isMain) {
  main().then(
    () => process.exit(0),
    (error) => {
      process.stderr.write(`daily_sheet_to_facts: ${error.message}\n`);
      process.exit(1);
    },
  );
}
