#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { disconnectPlaywrightTransport, loadPlaywrightRuntime } from './playwright_runtime.mjs';
import { COLUMNS, normalizeCell } from './sheet_template.mjs';

export const SNAPSHOT_SCHEMA = 'android-daily-sheet-snapshot-v2';
const DEFAULT_CDP_URL = 'http://127.0.0.1:9223';

function sha256(value) {
  return createHash('sha256').update(JSON.stringify(value)).digest('hex');
}

function normalizeSnapshotText(value) {
  return String(value ?? '').replace(/\r\n?/g, '\n').trim();
}

function positiveInteger(value, option) {
  if (!/^\d+$/.test(String(value ?? '')) || Number(value) <= 0) throw new Error(`${option} 必须是正整数`);
  return Number(value);
}

function parseArgs(argv) {
  const options = {};
  const allowed = new Set(['--cdp', '--document-id', '--sheet', '--output', '--screenshot', '--timeout-ms', '--max-rows', '--empty-row-limit']);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!allowed.has(key)) throw new Error(`未知参数: ${key}`);
    const value = argv[++index];
    if (!value || value.startsWith('--')) throw new Error(`${key} 缺少值`);
    options[key.slice(2).replaceAll('-', '_')] = value;
  }
  if (!options.document_id || !options.sheet || !options.output) throw new Error('缺少 --document-id、--sheet 或 --output');
  options.timeout_ms = positiveInteger(options.timeout_ms || '15000', '--timeout-ms');
  options.max_rows = positiveInteger(options.max_rows || '500', '--max-rows');
  options.empty_row_limit = positiveInteger(options.empty_row_limit || '5', '--empty-row-limit');
  return options;
}

export function buildSnapshot({ documentId, sheetName, sheetId, grid, styles = [], heights = {}, widths = {}, merges = [], outsideTemplateCells = [], rawCellMetadata = [], timings = {} }) {
  if (!Array.isArray(grid) || grid.some((row) => !Array.isArray(row) || row.length !== COLUMNS.length)) {
    throw new Error('grid 每行必须恰有 A:F 六列');
  }
  // Preserve repeated newlines: they are user-visible spacing changes and the
  // Stage B audit must detect them rather than silently canonicalize them.
  const normalizedGrid = grid.map((row) => row.map(normalizeSnapshotText));
  while (normalizedGrid.length && normalizedGrid.at(-1).every((value) => !value)) normalizedGrid.pop();
  if (!normalizedGrid.length) throw new Error('工作表快照为空');
  const rows = normalizedGrid.map((row, index) => ({
    rowNumber: index + 1,
    ...Object.fromEntries(COLUMNS.map((column, columnIndex) => [column, row[columnIndex]])),
  }));
  const cells = {};
  for (const row of rows) for (const column of COLUMNS) cells[`${column}${row.rowNumber}`] = row[column];
  const layout = {
    styles: styles.slice(0, rows.length),
    heights: Object.fromEntries(Object.entries(heights).filter(([row]) => Number(row) <= rows.length)),
    widths,
    merges: [...merges].sort(),
  };
  const outside = outsideTemplateCells.map((item) => ({ address: String(item.address), value: normalizeSnapshotText(item.value) })).filter((item) => item.value);
  const metadata = rawCellMetadata.filter((item) => item && item.address);
  const stable = { document_id: String(documentId), sheet_name: String(sheetName), sheet_id: String(sheetId), rows, outside_template_cells: outside, raw_cell_metadata: metadata, layout };
  return {
    schema: SNAPSHOT_SCHEMA,
    captured_at: new Date().toISOString(),
    document_id: String(documentId),
    sheet_name: String(sheetName),
    sheet_id: String(sheetId),
    row_count: rows.length,
    column_count: COLUMNS.length,
    value_sha256: sha256(rows),
    snapshot_sha256: sha256(stable),
    cells,
    rows,
    outside_template_cells: outside,
    raw_cell_metadata: metadata,
    layout,
    timings_ms: timings,
  };
}

async function findExactVisible(locator, wanted) {
  const count = Math.min(await locator.count(), 100);
  const matches = [];
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if (!(await candidate.isVisible().catch(() => false))) continue;
    if (normalizeCell(await candidate.innerText().catch(() => '')) === wanted) matches.push(candidate);
  }
  return matches;
}

async function locateExactTab(page, sheetName) {
  for (const selector of ['[role="tab"]', '.sheet-tab', '[class*="sheet-tab"]', '[class*="sheetbar"] [class*="item"]', '[class*="tab-item"]']) {
    const matches = await findExactVisible(page.locator(selector), sheetName);
    if (matches.length === 1) return matches[0];
    if (matches.length > 1) throw new Error(`工作表名称“${sheetName}”匹配到多个可见 tab`);
  }
  const matches = await findExactVisible(page.getByText(sheetName, { exact: true }), sheetName);
  if (matches.length === 1) return matches[0];
  if (matches.length > 1) throw new Error(`工作表名称“${sheetName}”匹配到多个可见元素`);
  throw new Error(`未找到工作表 tab“${sheetName}”`);
}

export async function captureDailySheetSnapshot({ cdpUrl = DEFAULT_CDP_URL, documentId, sheetName, screenshotPath, timeoutMs = 15_000, maxRows = 500, emptyRowLimit = 5 }) {
  const started = performance.now();
  const { chromium } = await loadPlaywrightRuntime();
  const attachStarted = performance.now();
  const browser = await chromium.connectOverCDP(cdpUrl, { timeout: timeoutMs });
  const attachMs = performance.now() - attachStarted;
  try {
    const pages = browser.contexts().flatMap((context) => context.pages()).filter((page) => page.url().includes(`/sheet/${documentId}`));
    if (pages.length !== 1) throw new Error(`目标表格页面必须唯一，实际 ${pages.length}`);
    const page = pages[0];
    page.setDefaultTimeout(timeoutMs);
    const selectStarted = performance.now();
    const tab = await locateExactTab(page, normalizeCell(sheetName));
    await tab.click({ timeout: timeoutMs });
    await page.locator('input.bar-label').filter({ visible: true }).first().waitFor({ state: 'visible', timeout: timeoutMs });
    await page.waitForTimeout(100);
    const selectMs = performance.now() - selectStarted;

    const modelStarted = performance.now();
    const model = await page.evaluate(({ maxRows: rowLimit, emptyRowLimit: blankLimit }) => {
      const app = globalThis.SpreadsheetApp;
      const sheet = app?.workbook?.activeSheet;
      if (!sheet || !app?.e2eTools?.getCellEditValue) throw new Error('企业微信工作簿模型接口不可用');
      const sheetId = sheet.getSheetId();
      const grid = [];
      const styles = [];
      const heights = {};
      const outsideTemplateCells = [];
      const rawCellMetadata = [];
      let consecutiveEmpty = 0;
      let stopped = false;
      for (let row = 0; row < rowLimit; row += 1) {
        const rowValues = [];
        const rowStyles = [];
        let empty = true;
        for (let column = 0; column < 6; column += 1) {
          const cell = sheet.getCellDataAtPosition(row, column);
          const value = app.e2eTools.getCellEditValue(app.workbook, row, column, sheetId, cell);
          rowValues.push(value ?? '');
          if (String(value ?? '').trim()) empty = false;
          const style = cell?.getStyle?.();
          const styleOptions = style?.getOptions?.();
          rowStyles.push(style ? {
            bold: style.getFontBold?.() === true,
            wrap: style.getWrapText?.() === true,
            horizontal: style.getHorizontalAlignment?.(),
            vertical: style.getVerticalAlignment?.(),
            fillRgb: styleOptions?.fill?.patternFill?.fgColor?.rgb ?? null,
          } : null);
          const formulaModel = cell?.getFormulaModel?.();
          const hyperlinks = cell?.getHyperlinks?.();
          const hasFormula = Boolean(formulaModel || cell?.isFormulaResultCell?.() || cell?.getFormulaExtType?.());
          const hasLink = Boolean(cell?.hasLink?.() || (Array.isArray(hyperlinks) && hyperlinks.length));
          const richText = Boolean(cell?.isRstType?.());
          if (hasFormula || hasLink || richText) rawCellMetadata.push({
            address: `${String.fromCharCode(65 + column)}${row + 1}`,
            type: String(cell?.getType?.() ?? cell?.type ?? ''),
            has_formula: hasFormula,
            has_link: hasLink,
            rich_text: richText,
          });
        }
        for (let column = 6; column < 26; column += 1) {
          const cell = sheet.getCellDataAtPosition(row, column);
          const value = app.e2eTools.getCellEditValue(app.workbook, row, column, sheetId, cell);
          const address = `${String.fromCharCode(65 + column)}${row + 1}`;
          const formulaModel = cell?.getFormulaModel?.();
          const hyperlinks = cell?.getHyperlinks?.();
          const hasFormula = Boolean(formulaModel || cell?.isFormulaResultCell?.() || cell?.getFormulaExtType?.());
          const hasLink = Boolean(cell?.hasLink?.() || (Array.isArray(hyperlinks) && hyperlinks.length));
          const richText = Boolean(cell?.isRstType?.());
          if (String(value ?? '').trim()) outsideTemplateCells.push({ address, value });
          if (String(value ?? '').trim() || hasFormula || hasLink || richText) empty = false;
          if (hasFormula || hasLink || richText) rawCellMetadata.push({ address, type: String(cell?.getType?.() ?? cell?.type ?? ''), has_formula: hasFormula, has_link: hasLink, rich_text: richText });
        }
        grid.push(rowValues);
        styles.push(rowStyles);
        heights[row + 1] = sheet.getRowHeightWithDefault(row);
        consecutiveEmpty = empty ? consecutiveEmpty + 1 : 0;
        if (consecutiveEmpty >= blankLimit) { stopped = true; break; }
      }
      if (!stopped) throw new Error(`读取达到最大行数 ${rowLimit}，仍未出现连续 ${blankLimit} 个空行`);
      const widths = {};
      for (let column = 0; column < 6; column += 1) widths[String.fromCharCode(65 + column)] = sheet.getColWidthWithDefault(column);
      const merges = Array.from(sheet.mergeManager?.mergeList || []).map(String);
      return { sheetId, grid, styles, heights, widths, merges, outsideTemplateCells, rawCellMetadata };
    }, { maxRows, emptyRowLimit });
    const modelReadMs = performance.now() - modelStarted;
    let screenshotMs = 0;
    if (screenshotPath) {
      const screenshotStarted = performance.now();
      const nameBox = page.locator('input.bar-label').filter({ visible: true }).first();
      await nameBox.fill('A1');
      await nameBox.press('Enter');
      await page.waitForTimeout(100);
      const session = await page.context().newCDPSession(page);
      try {
        const captureWidth = Math.min(4_000, Math.max(900, Math.ceil(Object.values(model.widths).reduce((sum, width) => sum + Number(width || 0), 0) + 80)));
        const captureHeight = Math.min(10_000, Math.max(650, Math.ceil(Object.entries(model.heights).filter(([row]) => Number(row) <= model.grid.length).reduce((sum, [, height]) => sum + Number(height || 0), 0) + 240)));
        await session.send('Emulation.setDeviceMetricsOverride', { width: captureWidth, height: captureHeight, deviceScaleFactor: 1, mobile: false });
        await page.evaluate(() => window.dispatchEvent(new Event('resize')));
        await page.waitForFunction((minimumHeight) => [...document.querySelectorAll('.excel-container canvas')].some((canvas) => canvas.height >= minimumHeight), Math.max(300, captureHeight - 260), { timeout: 3_000 });
        const capture = await page.evaluate(() => {
          const canvases = [...document.querySelectorAll('.excel-container canvas')];
          const canvas = canvases.sort((left, right) => (right.width * right.height) - (left.width * left.height))[0];
          return { data: canvas?.toDataURL('image/png') ?? '', width: canvas?.width ?? 0, height: canvas?.height ?? 0 };
        });
        if (!capture.data || capture.height < 300) throw new Error(`快照截图画布异常: ${JSON.stringify({ width: capture.width, height: capture.height })}`);
        const target = resolve(screenshotPath);
        await mkdir(dirname(target), { recursive: true });
        await writeFile(target, Buffer.from(capture.data.split(',')[1], 'base64'), { flag: 'wx' });
      } finally {
        await session.send('Emulation.clearDeviceMetricsOverride').catch(() => {});
        await session.detach().catch(() => {});
      }
      screenshotMs = performance.now() - screenshotStarted;
    }
    return buildSnapshot({
      documentId,
      sheetName,
      ...model,
      timings: {
        attach: Math.round(attachMs),
        select_tab: Math.round(selectMs),
        model_read: Math.round(modelReadMs),
        screenshot: Math.round(screenshotMs),
        total: Math.round(performance.now() - started),
      },
    });
  } finally {
    await disconnectPlaywrightTransport(browser);
  }
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  const snapshot = await captureDailySheetSnapshot({
    cdpUrl: options.cdp || DEFAULT_CDP_URL,
    documentId: options.document_id,
    sheetName: options.sheet,
    timeoutMs: options.timeout_ms,
    maxRows: options.max_rows,
    emptyRowLimit: options.empty_row_limit,
    screenshotPath: options.screenshot,
  });
  const target = resolve(options.output);
  await mkdir(dirname(target), { recursive: true });
  await writeFile(target, `${JSON.stringify(snapshot, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
  process.stdout.write(`${JSON.stringify({ output: target, schema: snapshot.schema, row_count: snapshot.row_count, value_sha256: snapshot.value_sha256, snapshot_sha256: snapshot.snapshot_sha256, timings_ms: snapshot.timings_ms })}\n`);
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (isMain) main().then(() => process.exit(0)).catch((error) => { process.stderr.write(`capture_daily_sheet_snapshot: ${error.message}\n`); process.exit(1); });
