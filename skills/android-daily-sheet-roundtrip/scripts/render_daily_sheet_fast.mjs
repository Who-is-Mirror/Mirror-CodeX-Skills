#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { buildDualClipboardPayload, deriveSheetLayout, refInRange } from './sheet_clipboard.mjs';
import { calculateRowHeights, expectedSheetCells, normalizeCell, validateTemplate, valuesFromInput } from './sheet_template.mjs';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ASSET = resolve(SCRIPT_DIR, '..', 'assets', 'daily-sheet-template.json');
const DEFAULT_CDP = 'http://127.0.0.1:9223';
const WECOM_MODULES = {
  dimensionRangeModule: 538688,
  dimensionTypeModule: 278359
};

function parseArgs(argv) {
  const result = {};
  const allowed = new Set(['--cdp', '--document-id', '--asset', '--input', '--title', '--tab', '--output-dir', '--result']);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!allowed.has(key)) throw new Error(`未知参数: ${key}`);
    const value = argv[++index];
    if (!value || value.startsWith('--')) throw new Error(`${key} 缺少值`);
    result[key.slice(2).replaceAll('-', '_')] = value;
  }
  if (!result.document_id) throw new Error('缺少 --document-id');
  if (!result.output_dir) throw new Error('缺少 --output-dir');
  return result;
}

async function loadPlaywright() {
  if (process.env.CODEX_PLAYWRIGHT_MODULE) return import(pathToFileURL(resolve(process.env.CODEX_PLAYWRIGHT_MODULE)).href);
  try {
    const modulePath = createRequire(resolve(process.cwd(), 'package.json')).resolve('playwright');
    return import(pathToFileURL(modulePath).href);
  } catch {
    return import('playwright');
  }
}

async function disconnect(browser) {
  try { await browser?._connection?.close?.(); } catch {}
}

function columnNumber(column) {
  return column.charCodeAt(0) - 64;
}

const options = parseArgs(process.argv.slice(2));
const assetPath = resolve(options.asset || DEFAULT_ASSET);
const template = validateTemplate(JSON.parse(await readFile(assetPath, 'utf8')));
const input = options.input ? JSON.parse(await readFile(resolve(options.input), 'utf8')) : null;
const values = valuesFromInput(input, template);
if (!Array.isArray(values) || values.some((row) => !Array.isArray(row) || row.length !== 6)) throw new Error('输入 values 每行必须恰有 6 列');
const layout = deriveSheetLayout(template, values);
const renderTemplate = { ...template, layout };
const outputDir = resolve(options.output_dir);
await mkdir(outputDir, { recursive: true });

const { chromium } = await loadPlaywright();
const browser = await chromium.connectOverCDP(options.cdp || DEFAULT_CDP);
let clipboardSnapshotKey;
let clipboardRestore = { captured: false, restored: false, captured_types: [], skipped_types: [] };
let operationError;
let operationResult;
try {
  const matches = browser.contexts().flatMap((context) => context.pages()).filter((page) => page.url().includes(`/sheet/${options.document_id}`));
  if (matches.length !== 1) throw new Error(`目标新表格页面必须唯一，实际 ${matches.length}`);
  const page = matches[0];
  // Browser paste is focus-gated. Activating the CDP tab does not move
  // the user's pointer or foreground the Edge window at the OS level.
  await page.bringToFront();
  page.setDefaultTimeout(15000);
  const nameBox = page.locator('input.bar-label').filter({ visible: true }).first();
  const formula = page.locator('.formula-input').filter({ visible: true }).first();
  await nameBox.waitFor({ state: 'visible' });
  await formula.waitFor({ state: 'visible' });

  async function select(ref) {
    await page.keyboard.press('Escape');
    await page.mouse.click(700, 160);
    await page.keyboard.press('Escape');
    await nameBox.fill(ref);
    await nameBox.press('Enter');
    await page.waitForTimeout(60);
  }

  const visibleTabs = [];
  for (const tab of await page.locator('[role="tab"].tab-bar-item').all()) if (await tab.isVisible().catch(() => false)) visibleTabs.push(tab);
  if (visibleTabs.length !== 1) throw new Error(`高速渲染仅接受只有一个可见工作表的新工作簿，实际 ${visibleTabs.length}`);

  const preflight = await page.evaluate(({ rows, columns }) => {
    const app = SpreadsheetApp;
    const sheet = app?.workbook?.activeSheet;
    if (!sheet || !app?.e2eTools?.getCellEditValue) throw new Error('企业微信工作簿模型接口不可用');
    const nonempty = [];
    for (let row = 0; row < rows; row += 1) {
      for (let column = 0; column < columns; column += 1) {
        const cell = sheet.getCellDataAtPosition(row, column);
        const value = app.e2eTools.getCellEditValue(app.workbook, row, column, sheet.getSheetId(), cell);
        if (String(value ?? '').trim()) nonempty.push({ row: row + 1, column: column + 1 });
      }
    }
    return { sheetId: sheet.getSheetId(), nonempty: nonempty.slice(0, 20) };
  }, { rows: Math.max(80, values.length), columns: 6 });
  if (preflight.nonempty.length) throw new Error(`高速渲染拒绝覆盖非空工作表: ${JSON.stringify(preflight.nonempty)}`);

  const title = options.title || template.template_name;
  const tabName = options.tab || template.sheet_tab;
  const titleInput = page.locator('input.melo-doc-title').filter({ visible: true }).first();
  await titleInput.fill(title);
  await titleInput.press('Enter');
  await page.waitForTimeout(250);
  if (normalizeCell(await visibleTabs[0].innerText()) !== tabName) {
    await visibleTabs[0].dblclick();
    const inputBox = page.locator('input.tab-bar-editable-text-input').filter({ visible: true }).last();
    await inputBox.waitFor({ state: 'visible' });
    await inputBox.fill(tabName);
    await inputBox.press('Enter');
    await page.waitForTimeout(250);
  }

  const totalStarted = performance.now();
  const characters = [...new Set(values.flat().join(''))];
  const measurements = await page.evaluate(({ characters: source, font }) => {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    context.font = font;
    return Object.fromEntries(source.map((character) => [character, context.measureText(character).width]));
  }, { characters, font: layout.row_height.font });
  const rowHeights = calculateRowHeights(renderTemplate, values, (character) => measurements[character] ?? 0);
  const payloadStarted = performance.now();
  const payload = buildDualClipboardPayload(template, values, rowHeights, layout);
  const payloadBuildMs = performance.now() - payloadStarted;

  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin: 'https://doc.weixin.qq.com' });
  clipboardSnapshotKey = `__codexClipboardSnapshot_${Date.now()}`;
  clipboardRestore = await page.evaluate(async (key) => {
    const captured = [];
    const capturedTypes = [];
    const skippedTypes = [];
    const items = await navigator.clipboard.read();
    for (const item of items) {
      const blobs = {};
      for (const type of item.types) {
        const supported = typeof ClipboardItem.supports !== 'function' || ClipboardItem.supports(type);
        if (!supported) { skippedTypes.push(type); continue; }
        try {
          blobs[type] = await item.getType(type);
          capturedTypes.push(type);
        } catch { skippedTypes.push(type); }
      }
      if (Object.keys(blobs).length) captured.push(blobs);
    }
    globalThis[key] = captured;
    return { captured: true, restored: false, captured_types: [...new Set(capturedTypes)], skipped_types: [...new Set(skippedTypes)] };
  }, clipboardSnapshotKey);

  const clipboardWriteStarted = performance.now();
  const clipboardWrite = await page.evaluate(async ({ plain, html }) => {
    const item = new ClipboardItem({
      'text/plain': new Blob([plain], { type: 'text/plain' }),
      'text/html': new Blob([html], { type: 'text/html' })
    });
    await navigator.clipboard.write([item]);
    return { types: item.types, focused: document.hasFocus() };
  }, { plain: payload.plain, html: payload.html });
  const clipboardWriteMs = performance.now() - clipboardWriteStarted;
  if (!clipboardWrite.types.includes('text/plain') || !clipboardWrite.types.includes('text/html')) throw new Error(`写入剪贴板 MIME 不完整: ${clipboardWrite.types.join(', ')}`);

  await select('A1');
  const pasteStarted = performance.now();
  if (!await page.evaluate(() => document.hasFocus())) throw new Error('目标表格标签未获得浏览器内焦点');
  await page.keyboard.press('Control+V');
  const pasteDispatch = { mode: 'playwright-keyboard-Control+V', presses: 1 };
  await page.waitForTimeout(650);
  const pasteMs = performance.now() - pasteStarted;

  const dimensionStarted = performance.now();
  const dimensionDispatch = await page.evaluate(async ({ modules, rowHeights: heights, widths }) => {
    let webpackRequire;
    webpackChunksheet.push([[`codex-fast-dimensions-${Date.now()}`], {}, (runtime) => { webpackRequire = runtime; }]);
    const DimensionRange = webpackRequire(modules.dimensionRangeModule)?.N;
    const DimensionType = webpackRequire(modules.dimensionTypeModule)?.O;
    const dimensionApi = SpreadsheetApp?.behaviorApi?.dimensionApi;
    if (typeof DimensionRange !== 'function' || !DimensionType || typeof dimensionApi?.resizeRows !== 'function' || typeof dimensionApi?.resizeColumns !== 'function') throw new Error('维度接口漂移');
    const sheetId = SpreadsheetApp.workbook.activeSheetId;
    const rowEntries = Object.entries(heights);
    await dimensionApi.resizeRows({
      sheetId,
      dimensionRanges: rowEntries.map(([row]) => new DimensionRange(sheetId, Number(row) - 1, Number(row) - 1, DimensionType.Row)),
      size: rowEntries.map(([, height]) => height)
    });
    const widthEntries = Object.values(widths);
    await dimensionApi.resizeColumns({
      sheetId,
      dimensionRanges: widthEntries.map((unused, column) => new DimensionRange(sheetId, column, column, DimensionType.Col)),
      size: widthEntries
    });
    return { mode: 'wecom-dimensionApi.resizeRows+resizeColumns', sheetId, rows: rowEntries.length, columns: widthEntries.length };
  }, { modules: WECOM_MODULES, rowHeights, widths: layout.column_widths });
  await page.waitForTimeout(400);
  const dimensionMs = performance.now() - dimensionStarted;

  const modelAudit = await page.evaluate(({ rowCount, columnCount }) => {
    const app = SpreadsheetApp;
    const sheet = app.workbook.activeSheet;
    const grid = [];
    const styles = [];
    for (let row = 0; row < rowCount; row += 1) {
      const rowValues = [];
      const rowStyles = [];
      for (let column = 0; column < columnCount; column += 1) {
        const cell = sheet.getCellDataAtPosition(row, column);
        rowValues.push(app.e2eTools.getCellEditValue(app.workbook, row, column, sheet.getSheetId(), cell));
        const style = cell?.getStyle?.();
        const options = style?.getOptions?.();
        rowStyles.push(style ? {
          bold: style.getFontBold?.() === true,
          wrap: style.getWrapText?.() === true,
          horizontal: style.getHorizontalAlignment?.(),
          vertical: style.getVerticalAlignment?.(),
          fillRgb: options?.fill?.patternFill?.fgColor?.rgb
        } : null);
      }
      grid.push(rowValues);
      styles.push(rowStyles);
    }
    const heights = {};
    for (let row = 1; row < rowCount; row += 1) heights[row + 1] = sheet.getRowHeightWithDefault(row);
    const widths = {};
    for (let column = 0; column < columnCount; column += 1) widths[String.fromCharCode(65 + column)] = sheet.getColWidthWithDefault(column);
    const merges = Array.from(sheet.mergeManager?.mergeList || []).map((merge) => String(merge)).sort();
    return { grid, styles, heights, widths, merges };
  }, { rowCount: values.length, columnCount: 6 });

  const expected = expectedSheetCells(values, layout.footer_rows);
  const valueMismatches = [];
  for (const [ref, wanted] of expected) {
    const match = ref.match(/^([A-F])(\d+)$/);
    const row = Number(match[2]) - 1;
    const column = columnNumber(match[1]) - 1;
    const actual = normalizeCell(modelAudit.grid[row]?.[column]);
    if (actual !== wanted) valueMismatches.push({ ref, wanted, actual });
  }
  const heightMismatches = Object.entries(rowHeights).filter(([row, wanted]) => modelAudit.heights[row] !== wanted).map(([row, wanted]) => ({ row: Number(row), wanted, actual: modelAudit.heights[row] }));
  const widthMismatches = Object.entries(layout.column_widths).filter(([column, wanted]) => modelAudit.widths[column] !== wanted).map(([column, wanted]) => ({ column, wanted, actual: modelAudit.widths[column] }));
  const mergeMismatches = JSON.stringify(modelAudit.merges) === JSON.stringify([...layout.merge_ranges].sort()) ? [] : [{ wanted: [...layout.merge_ranges].sort(), actual: modelAudit.merges }];
  const styleMismatches = [];
  const footerRows = new Set(layout.footer_rows);
  const separatorRows = new Set(layout.separator_rows);
  for (let rowIndex = 0; rowIndex < modelAudit.styles.length; rowIndex += 1) {
    for (let columnIndex = 0; columnIndex < 6; columnIndex += 1) {
      const style = modelAudit.styles[rowIndex]?.[columnIndex];
      const ref = `${String.fromCharCode(65 + columnIndex)}${rowIndex + 1}`;
      const row = rowIndex + 1;
      const coveredFooterCell = footerRows.has(row) && columnIndex >= 4;
      const requiresBold = layout.bold_ranges.some((range) => refInRange(ref, range));
      const requiresWrap = !coveredFooterCell && !separatorRows.has(row) && layout.wrap_ranges.some((range) => refInRange(ref, range));
      const requiresHeaderStyle = rowIndex === 0;
      if (!style) {
        if (requiresBold || requiresWrap || requiresHeaderStyle) styleMismatches.push({ ref, expected: 'style object', actual: null });
        continue;
      }
      if (requiresBold && !style.bold) styleMismatches.push({ ref, expected: 'bold', actual: style.bold });
      if (requiresWrap && !style.wrap) styleMismatches.push({ ref, expected: 'wrap', actual: style.wrap });
      if (rowIndex === 0 && style.fillRgb !== 'FF8CDDFA') styleMismatches.push({ ref, expected: 'FF8CDDFA', actual: style.fillRgb });
      if (rowIndex === 0 && style.horizontal !== 3) styleMismatches.push({ ref, expected: 'center(3)', actual: style.horizontal });
    }
  }
  if (valueMismatches.length || heightMismatches.length || widthMismatches.length || mergeMismatches.length || styleMismatches.length) {
    throw new Error(`高速渲染验证失败: ${JSON.stringify({ valueMismatches: valueMismatches.slice(0, 10), heightMismatches, widthMismatches, mergeMismatches, styleMismatches: styleMismatches.slice(0, 20) })}`);
  }

  const combined = [...expected.values()].join('\n');
  const leaked = template.forbidden_real_markers.filter((token) => combined.includes(token));
  if (leaked.length) throw new Error(`新表格含真实数据标记: ${leaked.join(', ')}`);

  const screenshotRefs = [['A1', 'top-left'], ['E2', 'top-right']];
  for (let index = 1; index < values.length; index += 1) if (normalizeCell(values[index][2]) === '今日概况') screenshotRefs.push([`A${index + 1}`, `scope-${index + 1}`]);
  if (layout.footer_rows.length) screenshotRefs.push([`D${layout.footer_rows.at(-1)}`, 'footer']);
  const screenshots = [];
  for (const [ref, name] of screenshotRefs.slice(0, 5)) {
    await select(ref);
    const path = resolve(outputDir, `daily-fast-${name}.png`);
    await page.screenshot({ path });
    screenshots.push(path);
  }

  operationResult = {
    schema: 'android-daily-sheet-fast-render-result-v1',
    status: 'PASS',
    document_id: options.document_id,
    url: page.url(),
    title,
    sheet_tab: tabName,
    checked_cells: expected.size,
    checked_row_heights: Object.keys(rowHeights).length,
    checked_column_widths: 6,
    checked_merges: layout.merge_ranges.length,
    real_data_markers_found: leaked,
    calculated_row_heights: rowHeights,
    clipboard_write: clipboardWrite,
    paste_dispatch: pasteDispatch,
    dimension_dispatch: dimensionDispatch,
    timings_ms: {
      payload_build: Math.round(payloadBuildMs),
      clipboard_write: Math.round(clipboardWriteMs),
      paste_and_wait: Math.round(pasteMs),
      dimensions_and_wait: Math.round(dimensionMs),
      total_before_screenshots: Math.round(performance.now() - totalStarted)
    },
    screenshots,
    template_asset: assetPath,
    wecom_modules: WECOM_MODULES
  };
} catch (error) {
  operationError = error;
} finally {
  if (clipboardSnapshotKey) {
    try {
      const restored = await browser.contexts().flatMap((context) => context.pages()).find((page) => page.url().includes(`/sheet/${options.document_id}`))?.evaluate(async (key) => {
        const snapshot = globalThis[key];
        if (!Array.isArray(snapshot)) throw new Error('剪贴板快照丢失');
        if (snapshot.length) await navigator.clipboard.write(snapshot.map((blobs) => new ClipboardItem(blobs)));
        else await navigator.clipboard.writeText('');
        delete globalThis[key];
        return true;
      }, clipboardSnapshotKey);
      clipboardRestore.restored = Boolean(restored);
    } catch (error) {
      clipboardRestore.restore_error = error.message;
    }
  }
  await disconnect(browser);
}

if (operationError) throw operationError;
if (!clipboardRestore.restored) throw new Error(`表格已写入，但原剪贴板恢复失败: ${clipboardRestore.restore_error || 'unknown'}`);
operationResult.clipboard_restore = clipboardRestore;
const resultPath = resolve(options.result || resolve(outputDir, 'fast-render-result.json'));
await writeFile(resultPath, `${JSON.stringify(operationResult, null, 2)}\n`, 'utf8');
process.stdout.write(`${JSON.stringify({ ...operationResult, result_path: resultPath }, null, 2)}\n`);
process.exit(0);
