#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { disconnectPlaywrightTransport, loadPlaywrightRuntime } from './playwright_runtime.mjs';

export const COMPACTION_SCHEMA = 'android-daily-sheet-row-repair-plan-v3';
const AUDIT_SCHEMA = 'android-daily-sheet-change-audit-v1';
const DEFAULT_CDP = 'http://127.0.0.1:9223';
const COLUMNS = ['A', 'B', 'C', 'D', 'E', 'F'];

const normalize = (value) => String(value ?? '').replace(/\r\n?/g, '\n').trim();

function validateAnchor(anchor, label) {
  const cell = String(anchor?.cell ?? '').toUpperCase();
  if (!/^[A-F][1-9]\d*$/.test(cell)) throw new Error(`${label}.cell 必须是 A:F 单元格地址`);
  return { cell, value: normalize(anchor.value) };
}

export function validateCompactionPlan(payload, audit) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('行压缩计划必须是 JSON 对象');
  if (payload.schema !== COMPACTION_SCHEMA) throw new Error(`schema 必须是 ${COMPACTION_SCHEMA}`);
  if (!audit || audit.schema !== AUDIT_SCHEMA || !Array.isArray(audit.findings)) throw new Error(`必须提供 ${AUDIT_SCHEMA} 审计结果`);
  if (payload.baseline_sha256 !== audit.baseline_sha256 || payload.current_sha256 !== audit.current_sha256) throw new Error('STALE_COMPACTION_PLAN：行压缩计划与审计快照哈希不一致');
  if (!Array.isArray(payload.operations) || payload.operations.length === 0) throw new Error('operations 必须包含至少一个行修复操作');
  const accepted = new Map(audit.findings.filter((finding) => finding.type === 'missing_task' && finding.accepted).map((finding) => [finding.finding_id, finding]));
  const rowSuggestions = new Map((audit.suggested_row_repairs ?? []).map((operation) => [operation.finding_id, operation]));
  const operations = payload.operations.map((raw, operationIndex) => {
    const action = normalize(raw?.action);
    if (!['delete', 'insert'].includes(action)) throw new Error(`operations[${operationIndex}].action 必须是 delete 或 insert`);
    const startRow = Number(raw?.start_row);
    const endRow = Number(raw?.end_row);
    if (!Number.isInteger(startRow) || !Number.isInteger(endRow) || startRow < 2 || endRow < startRow || endRow > 500) throw new Error('start_row/end_row 必须是第 2 至 500 行内的有效连续范围');
    const findingId = normalize(raw?.finding_id);
    const finding = accepted.get(findingId);
    const before = Array.isArray(raw.anchors_before) ? raw.anchors_before.map((v, i) => validateAnchor(v, `operations[${operationIndex}].anchors_before[${i}]`)) : [];
    const after = Array.isArray(raw.anchors_after) ? raw.anchors_after.map((v, i) => validateAnchor(v, `operations[${operationIndex}].anchors_after[${i}]`)) : [];
    if (!before.length || !after.length) throw new Error('anchors_before 和 anchors_after 均不能为空');
    const insideBefore = before.filter((anchor) => {
      const row = Number(anchor.cell.match(/\d+$/)[0]);
      return row >= startRow && row <= endRow;
    });
    if (action === 'delete' && insideBefore.length) throw new Error(`anchors_before 不得位于待删除范围内: ${insideBefore.map((anchor) => anchor.cell).join(', ')}`);
    const reason = normalize(raw?.reason);
    if (!reason) throw new Error(`operations[${operationIndex}] 缺少 reason`);
    if (finding) {
      if (action !== 'delete' || finding.baseline_rows?.start !== startRow || finding.baseline_rows?.end !== endRow) throw new Error(`operations[${operationIndex}] 未绑定已确认 missing_task 的精确行范围`);
    } else {
      const suggested = rowSuggestions.get(findingId);
      const normalized = { action, start_row: startRow, end_row: endRow, finding_id: findingId, anchors_before: before, anchors_after: after, reason };
      const expected = suggested && {
        action: suggested.action, start_row: Number(suggested.start_row), end_row: Number(suggested.end_row), finding_id: suggested.finding_id,
        anchors_before: suggested.anchors_before.map((v, i) => validateAnchor(v, `suggested.anchors_before[${i}]`)),
        anchors_after: suggested.anchors_after.map((v, i) => validateAnchor(v, `suggested.anchors_after[${i}]`)), reason: normalize(suggested.reason),
      };
      if (!expected || JSON.stringify(normalized) !== JSON.stringify(expected)) throw new Error(`operations[${operationIndex}] 与审计建议的行修复不一致`);
    }
    return { action, start_row: startRow, end_row: endRow, finding_id: findingId, anchors_before: before, anchors_after: after, reason };
  }).sort((left, right) => right.start_row - left.start_row);
  for (let index = 1; index < operations.length; index += 1) {
    if (operations[index - 1].start_row <= operations[index].end_row) throw new Error('delete_rows 存在重叠范围');
  }
  return { schema: COMPACTION_SCHEMA, baseline_sha256: payload.baseline_sha256, current_sha256: payload.current_sha256, operations };
}

function parseArgs(argv) {
  const options = {};
  const allowed = new Set(['--cdp', '--document-id', '--sheet', '--plan', '--audit', '--output-dir', '--result']);
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === '--help' || key === '-h') return { help: true };
    if (!allowed.has(key)) throw new Error(`未知参数: ${key}`);
    const value = argv[++i];
    if (!value || value.startsWith('--')) throw new Error(`${key} 缺少值`);
    options[key.slice(2).replaceAll('-', '_')] = value;
  }
  for (const key of ['document_id', 'sheet', 'plan', 'audit', 'output_dir']) if (!options[key]) throw new Error(`缺少 --${key.replaceAll('_', '-')}`);
  return options;
}

async function locateTab(page, name) {
  for (const selector of ['[role="tab"]', '.sheet-tab', '[class*="sheet-tab"]', '[class*="sheetbar"] [class*="item"]', '[class*="tab-item"]']) {
    const locator = page.locator(selector);
    const found = [];
    for (let i = 0; i < Math.min(await locator.count(), 100); i += 1) {
      const candidate = locator.nth(i);
      if (await candidate.isVisible().catch(() => false) && normalize(await candidate.innerText().catch(() => '')) === name) found.push(candidate);
    }
    if (found.length === 1) return found[0];
    if (found.length > 1) throw new Error(`工作表“${name}”匹配到多个可见 tab`);
  }
  throw new Error(`无法唯一定位工作表 tab“${name}”`);
}

async function mutateRows(page, action, startRow, endRow) {
  await page.evaluate(async ({ actionName, start, end }) => {
    const api = SpreadsheetApp?.behaviorApi?.dimensionApi;
    const sheetId = SpreadsheetApp?.workbook?.activeSheetId;
    const method = actionName === 'delete' ? api?.deleteRows : api?.insertRows;
    if (!sheetId || typeof method !== 'function') throw new Error(`工作簿行${actionName === 'delete' ? '删除' : '插入'}接口不可用`);
    await method.call(api, { sheetId, dimensionDataList: [{ index: start - 1, count: end - start + 1 }] });
  }, { actionName: action, start: startRow, end: endRow });
}

export async function compactRows(options) {
  const [rawPlan, audit] = await Promise.all([
    readFile(resolve(options.plan), 'utf8').then(JSON.parse),
    readFile(resolve(options.audit), 'utf8').then(JSON.parse),
  ]);
  const plan = validateCompactionPlan(rawPlan, audit);
  const outputDir = resolve(options.output_dir);
  await mkdir(outputDir, { recursive: true });
  const { chromium } = await loadPlaywrightRuntime();
  const browser = await chromium.connectOverCDP(options.cdp || DEFAULT_CDP, { timeout: 15000 });
  try {
    const pages = browser.contexts().flatMap((context) => context.pages()).filter((page) => page.url().includes(`/sheet/${options.document_id}`));
    if (pages.length !== 1) throw new Error(`目标表格页面必须唯一，实际 ${pages.length}`);
    const page = pages[0];
    page.setDefaultTimeout(15000);
    await (await locateTab(page, normalize(options.sheet))).click();
    const nameBox = page.locator('input.bar-label').filter({ visible: true }).first();
    const formula = page.locator('.formula-input').filter({ visible: true }).first();
    await nameBox.waitFor({ state: 'visible' });
    await formula.waitFor({ state: 'visible' });
    const read = async (cell) => {
      await nameBox.fill(cell);
      await nameBox.press('Enter');
      await page.waitForTimeout(45);
      return normalize(await formula.evaluate((element) => element.innerText));
    };

    const nonblank = [];
    for (const operation of plan.operations.filter((candidate) => candidate.action === 'delete')) {
      for (let row = operation.start_row; row <= operation.end_row; row += 1) {
        for (const column of COLUMNS) {
          const cell = `${column}${row}`;
          const value = await read(cell);
          if (value) nonblank.push({ cell, value });
        }
      }
    }
    if (nonblank.length) throw new Error(`目标行不是全空，未删除: ${JSON.stringify(nonblank)}`);
    for (const operation of plan.operations) {
      for (const anchor of operation.anchors_before) {
        const actual = await read(anchor.cell);
        if (actual !== anchor.value) throw new Error(`删除前锚点不匹配，未删除: ${JSON.stringify({ cell: anchor.cell, expected: anchor.value, actual })}`);
      }
    }

    const completed = [];
    try {
      const afterValues = {};
      for (const operation of plan.operations) {
        await mutateRows(page, operation.action, operation.start_row, operation.end_row);
        completed.push(operation);
        await page.waitForTimeout(500);
        for (const anchor of operation.anchors_after) {
          const actual = await read(anchor.cell);
          afterValues[anchor.cell] = actual;
          if (actual !== anchor.value) throw new Error(`删行后锚点不匹配: ${JSON.stringify({ cell: anchor.cell, expected: anchor.value, actual })}`);
        }
      }
      const focusAnchor = plan.operations.at(-1).anchors_after[0];
      await nameBox.fill(focusAnchor.cell);
      await nameBox.press('Enter');
      await page.waitForTimeout(100);
      const rangeLabel = plan.operations.map((operation) => `${operation.action}-${operation.start_row}-${operation.end_row}`).join('_');
      const screenshot = resolve(outputDir, `compacted-${normalize(options.sheet)}-rows-${rangeLabel}.png`);
      const screenshotSession = await page.context().newCDPSession(page);
      try {
        await screenshotSession.send('Emulation.setDeviceMetricsOverride', { width: 750, height: 650, deviceScaleFactor: 1, mobile: false });
        await page.waitForTimeout(300);
        const capture = await page.evaluate(() => {
          const canvas = document.querySelector('.excel-container canvas');
          return { data: canvas?.toDataURL('image/png') ?? '', width: canvas?.width ?? 0, height: canvas?.height ?? 0 };
        });
        if (!capture.data || capture.height < 300) throw new Error(`修复截图画布异常: ${JSON.stringify({ width: capture.width, height: capture.height })}`);
        await writeFile(screenshot, Buffer.from(capture.data.split(',')[1], 'base64'));
      } finally {
        await screenshotSession.send('Emulation.clearDeviceMetricsOverride').catch(() => {});
        await screenshotSession.detach().catch(() => {});
      }
      const result = { status: 'PASS', schema: COMPACTION_SCHEMA, document_id: options.document_id, sheet: normalize(options.sheet), baseline_sha256: plan.baseline_sha256, current_sha256: plan.current_sha256, operations: plan.operations.map((operation) => ({ action: operation.action, start: operation.start_row, end: operation.end_row, finding_id: operation.finding_id })), after_values: afterValues, screenshot };
      if (options.result) {
        const target = resolve(options.result);
        await mkdir(dirname(target), { recursive: true });
        await writeFile(target, `${JSON.stringify(result, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
      }
      return result;
    } catch (error) {
      const rollbackFailures = [];
      for (const operation of [...completed].reverse()) {
        try {
          const inverse = operation.action === 'delete' ? 'insert' : 'delete';
          if (inverse === 'delete') {
            for (let row = operation.start_row; row <= operation.end_row; row += 1) for (const column of COLUMNS) {
              const cell = `${column}${row}`;
              const value = await read(cell);
              if (value) throw new Error(`插入行已出现内容，拒绝补偿删除: ${JSON.stringify({ cell, value })}`);
            }
          }
          await mutateRows(page, inverse, operation.start_row, operation.end_row);
          await page.waitForTimeout(350);
        } catch (rollbackError) {
          rollbackFailures.push({ rows: [operation.start_row, operation.end_row], error: rollbackError.message });
        }
      }
      for (const operation of plan.operations) for (const anchor of operation.anchors_before) {
        try {
          const actual = await read(anchor.cell);
          if (actual !== anchor.value) rollbackFailures.push({ cell: anchor.cell, expected: anchor.value, actual });
        } catch (rollbackError) {
          rollbackFailures.push({ cell: anchor.cell, error: rollbackError.message });
        }
      }
      if (rollbackFailures.length) throw new Error(`${error.message}; RECOVERY_REQUIRED，删行补偿失败: ${JSON.stringify(rollbackFailures)}`);
      if (!completed.length) throw new Error(`${error.message}; 未确认任何删行操作成功，表格仍须重新审计`);
      throw new Error(`${error.message}; 已插回等量空白行并恢复原行位置，必须重新审计样式与合并`);
    }
  } finally {
    await disconnectPlaywrightTransport(browser);
  }
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  if (options.help) return process.stdout.write('node scripts/compact_daily_sheet_rows.mjs --document-id <id> --sheet YYYY-MM-DD --audit <sheet-change-audit.json> --plan <plan.json> --output-dir <dir> [--result <result.json>]\n');
  process.stdout.write(`${JSON.stringify(await compactRows(options), null, 2)}\n`);
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (isMain) main().catch((error) => { process.stderr.write(`compact_daily_sheet_rows: ${error.message}\n`); process.exit(1); });
