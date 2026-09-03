#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { disconnectPlaywrightTransport, loadPlaywrightRuntime } from './playwright_runtime.mjs';

export const COMPACTION_SCHEMA = 'android-daily-sheet-row-compaction-v1';
const DEFAULT_CDP = 'http://127.0.0.1:9223';
const COLUMNS = ['A', 'B', 'C', 'D', 'E', 'F'];

const normalize = (value) => String(value ?? '').replace(/\r\n?/g, '\n').trim();

function validateAnchor(anchor, label) {
  const cell = String(anchor?.cell ?? '').toUpperCase();
  if (!/^[A-F][1-9]\d*$/.test(cell)) throw new Error(`${label}.cell 必须是 A:F 单元格地址`);
  return { cell, value: normalize(anchor.value) };
}

export function validateCompactionPlan(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('行压缩计划必须是 JSON 对象');
  if (payload.schema !== COMPACTION_SCHEMA) throw new Error(`schema 必须是 ${COMPACTION_SCHEMA}`);
  if (!Array.isArray(payload.delete_rows) || payload.delete_rows.length !== 1) throw new Error('当前每个计划必须且只能删除一个连续空白行块');
  const raw = payload.delete_rows[0];
  const startRow = Number(raw?.start_row);
  const endRow = Number(raw?.end_row);
  if (!Number.isInteger(startRow) || !Number.isInteger(endRow) || startRow < 2 || endRow < startRow || endRow > 500) throw new Error('start_row/end_row 必须是第 2 至 500 行内的有效连续范围');
  const before = Array.isArray(raw.anchors_before) ? raw.anchors_before.map((v, i) => validateAnchor(v, `anchors_before[${i}]`)) : [];
  const after = Array.isArray(raw.anchors_after) ? raw.anchors_after.map((v, i) => validateAnchor(v, `anchors_after[${i}]`)) : [];
  if (!before.length || !after.length) throw new Error('anchors_before 和 anchors_after 均不能为空');
  const insideBefore = before.filter((anchor) => {
    const row = Number(anchor.cell.match(/\d+$/)[0]);
    return row >= startRow && row <= endRow;
  });
  if (insideBefore.length) throw new Error(`anchors_before 不得位于待删除范围内: ${insideBefore.map((anchor) => anchor.cell).join(', ')}`);
  return { schema: COMPACTION_SCHEMA, delete_rows: [{ start_row: startRow, end_row: endRow, anchors_before: before, anchors_after: after }] };
}

function parseArgs(argv) {
  const options = {};
  const allowed = new Set(['--cdp', '--document-id', '--sheet', '--plan', '--output-dir', '--result']);
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === '--help' || key === '-h') return { help: true };
    if (!allowed.has(key)) throw new Error(`未知参数: ${key}`);
    const value = argv[++i];
    if (!value || value.startsWith('--')) throw new Error(`${key} 缺少值`);
    options[key.slice(2).replaceAll('-', '_')] = value;
  }
  for (const key of ['document_id', 'sheet', 'plan', 'output_dir']) if (!options[key]) throw new Error(`缺少 --${key.replaceAll('_', '-')}`);
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

export async function compactRows(options) {
  const plan = validateCompactionPlan(JSON.parse(await readFile(resolve(options.plan), 'utf8')));
  const operation = plan.delete_rows[0];
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
    for (let row = operation.start_row; row <= operation.end_row; row += 1) {
      for (const column of COLUMNS) {
        const cell = `${column}${row}`;
        const value = await read(cell);
        if (value) nonblank.push({ cell, value });
      }
    }
    if (nonblank.length) throw new Error(`目标行不是全空，未删除: ${JSON.stringify(nonblank)}`);
    for (const anchor of operation.anchors_before) {
      const actual = await read(anchor.cell);
      if (actual !== anchor.value) throw new Error(`删除前锚点不匹配，未删除: ${JSON.stringify({ cell: anchor.cell, expected: anchor.value, actual })}`);
    }

    await page.evaluate(async ({ startRow, endRow }) => {
      const api = SpreadsheetApp?.behaviorApi?.dimensionApi;
      const sheetId = SpreadsheetApp?.workbook?.activeSheetId;
      if (!sheetId || typeof api?.deleteRows !== 'function') throw new Error('工作簿行删除接口不可用');
      await api.deleteRows({
        sheetId,
        dimensionDataList: [{ index: startRow - 1, count: endRow - startRow + 1 }],
      });
    }, { startRow: operation.start_row, endRow: operation.end_row });
    await page.waitForTimeout(500);

    const afterValues = {};
    for (const anchor of operation.anchors_after) {
      const actual = await read(anchor.cell);
      afterValues[anchor.cell] = actual;
      if (actual !== anchor.value) throw new Error(`删行后锚点不匹配: ${JSON.stringify({ cell: anchor.cell, expected: anchor.value, actual })}`);
    }
    await nameBox.fill(operation.anchors_after[0].cell);
    await nameBox.press('Enter');
    await page.waitForTimeout(100);
    const screenshot = resolve(outputDir, `compacted-${normalize(options.sheet)}-rows-${operation.start_row}-${operation.end_row}.png`);
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
    const result = { status: 'PASS', schema: COMPACTION_SCHEMA, document_id: options.document_id, sheet: normalize(options.sheet), deleted_rows: { start: operation.start_row, end: operation.end_row }, after_values: afterValues, screenshot };
    if (options.result) {
      const target = resolve(options.result);
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, `${JSON.stringify(result, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
    }
    return result;
  } finally {
    await disconnectPlaywrightTransport(browser);
  }
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  if (options.help) return process.stdout.write('node scripts/compact_daily_sheet_rows.mjs --document-id <id> --sheet YYYY-MM-DD --plan <plan.json> --output-dir <dir> [--result <result.json>]\n');
  process.stdout.write(`${JSON.stringify(await compactRows(options), null, 2)}\n`);
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (isMain) main().catch((error) => { process.stderr.write(`compact_daily_sheet_rows: ${error.message}\n`); process.exit(1); });
