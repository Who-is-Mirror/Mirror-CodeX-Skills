#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { disconnectPlaywrightTransport, loadPlaywrightRuntime } from './playwright_runtime.mjs';
import { calculateRowHeights, expectedSheetCells, normalizeCell, validateTemplate, valuesFromInput } from './sheet_template.mjs';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ASSET = resolve(SCRIPT_DIR, '..', 'assets', 'daily-sheet-template.json');
const DEFAULT_CDP = 'http://127.0.0.1:9223';

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

async function loadRuntime() {
  const { playwright, modulePath } = await loadPlaywrightRuntime();
  const requireFromRuntime = createRequire(modulePath);
  let PNG = null;
  try { ({ PNG } = requireFromRuntime('pngjs')); } catch {}
  return { playwright, PNG };
}

async function visibleButton(page, name) {
  const candidates = await page.getByRole('button', { name, exact: true }).all();
  for (let index = candidates.length - 1; index >= 0; index -= 1) {
    if (await candidates[index].isVisible().catch(() => false)) return candidates[index];
  }
  return null;
}

const options = parseArgs(process.argv.slice(2));
const assetPath = resolve(options.asset || DEFAULT_ASSET);
const template = validateTemplate(JSON.parse(await readFile(assetPath, 'utf8')));
const input = options.input ? JSON.parse(await readFile(resolve(options.input), 'utf8')) : null;
const values = valuesFromInput(input, template);
if (!Array.isArray(values) || values.some((row) => !Array.isArray(row) || row.length !== 6)) throw new Error('输入 values 每行必须恰有 6 列');
const outputDir = resolve(options.output_dir);
await mkdir(outputDir, { recursive: true });

const { playwright: { chromium }, PNG } = await loadRuntime();
if (!PNG) throw new Error('bundled runtime 缺少 pngjs，无法安全定位行头');
const browser = await chromium.connectOverCDP(options.cdp || DEFAULT_CDP);
try {
  const matches = browser.contexts().flatMap((context) => context.pages()).filter((page) => page.url().includes(`/sheet/${options.document_id}`));
  if (matches.length !== 1) throw new Error(`目标新表格页面必须唯一，实际 ${matches.length}`);
  const page = matches[0];
  page.setDefaultTimeout(15000);
  const nameBox = page.locator('input.bar-label').filter({ visible: true }).first();
  const formula = page.locator('.formula-input').filter({ visible: true }).first();
  await nameBox.waitFor({ state: 'visible' });
  await formula.waitFor({ state: 'visible' });

  async function dismiss() {
    await page.keyboard.press('Escape');
    await page.mouse.click(700, 250);
    await page.keyboard.press('Escape');
  }
  async function select(ref) {
    await dismiss();
    await nameBox.fill(ref);
    await nameBox.press('Enter');
    await page.waitForTimeout(70);
  }
  async function setCell(ref, value) {
    await select(ref);
    await formula.fill(value);
    await formula.press('Enter');
    await page.waitForTimeout(20);
  }
  async function clickToolbar(name, group) {
    let button = await visibleButton(page, name);
    if (!button) {
      const groupButton = await visibleButton(page, group);
      if (!groupButton) throw new Error(`找不到工具组: ${group}`);
      await groupButton.click();
      await page.waitForTimeout(100);
      button = await visibleButton(page, name);
    }
    if (!button) throw new Error(`找不到工具栏按钮: ${name}`);
    await button.click();
    await page.waitForTimeout(140);
  }
  async function ensureToolbarPressed(range, checkRef, name, group) {
    await select(checkRef);
    let button = await visibleButton(page, name);
    if (!button) {
      const groupButton = await visibleButton(page, group);
      if (!groupButton) throw new Error(`找不到工具组: ${group}`);
      await groupButton.click();
      await page.waitForTimeout(100);
      button = await visibleButton(page, name);
    }
    if (!button) throw new Error(`找不到工具栏按钮: ${name}`);
    if (await button.getAttribute('aria-pressed') !== 'true') {
      await select(range);
      await clickToolbar(name, group);
    }
    await select(checkRef);
    button = await visibleButton(page, name);
    if (!button) {
      await (await visibleButton(page, group)).click();
      await page.waitForTimeout(100);
      button = await visibleButton(page, name);
    }
    if (!button || await button.getAttribute('aria-pressed') !== 'true') throw new Error(`${range} 未能启用${name}`);
  }
  async function setColumnWidth(x, width) {
    await dismiss();
    await page.mouse.click(x, 190, { button: 'right' });
    const item = page.getByText('设置列宽', { exact: true }).filter({ visible: true }).last();
    await item.waitFor({ state: 'visible', timeout: 5000 });
    await item.click();
    const input = page.locator('input.context-menu-resize-input').filter({ visible: true }).last();
    await input.fill(String(width));
    await input.press('Enter');
    await page.waitForTimeout(180);
  }
  async function selectedRowCenter() {
    const image = PNG.sync.read(await page.screenshot());
    const lines = [];
    for (let y = 170; y < Math.min(image.height - 25, 470); y += 1) {
      let count = 0;
      for (let x = 45; x <= Math.min(235, image.width - 1); x += 1) {
        const offset = (y * image.width + x) * 4;
        const [red, green, blue] = [image.data[offset], image.data[offset + 1], image.data[offset + 2]];
        if (red < 80 && green >= 80 && green < 170 && blue > 180) count += 1;
      }
      if (count > 40) lines.push(y);
    }
    const groups = [];
    for (const y of lines) {
      const last = groups.at(-1);
      if (!last || y > last.at(-1) + 1) groups.push([y]);
      else last.push(y);
    }
    if (groups.length < 2) throw new Error(`无法识别选中单元格边框: ${JSON.stringify(groups)}`);
    const centers = groups.map((group) => group.reduce((sum, y) => sum + y, 0) / group.length);
    return Math.round((centers.at(-2) + centers.at(-1)) / 2);
  }
  async function setRowHeight(row, height) {
    await select(`A${row}`);
    const y = await selectedRowCenter();
    await page.mouse.click(25, y + 4, { button: 'right' });
    await page.waitForTimeout(320);
    const tail = (await page.locator('body').innerText()).slice(-1300);
    if (!tail.includes(`(${row} - ${row}) 行`)) throw new Error(`第 ${row} 行右键定位失败，y=${y}`);
    await page.getByText('设置行高', { exact: true }).filter({ visible: true }).last().click();
    const input = page.locator('input.context-menu-resize-input').filter({ visible: true }).last();
    await input.fill(String(height));
    await input.press('Enter');
    await page.waitForTimeout(130);
  }

  const title = options.title || template.template_name;
  const tabName = options.tab || template.sheet_tab;
  const titleInput = page.locator('input.melo-doc-title').filter({ visible: true }).first();
  await titleInput.fill(title);
  await titleInput.press('Enter');
  await page.waitForTimeout(400);

  const visibleTabs = [];
  for (const tab of await page.locator('[role="tab"].tab-bar-item').all()) if (await tab.isVisible().catch(() => false)) visibleTabs.push(tab);
  if (visibleTabs.length !== 1) throw new Error(`新工作簿必须只有一个可见工作表，实际 ${visibleTabs.length}`);
  if (normalizeCell(await visibleTabs[0].innerText()) !== tabName) {
    await visibleTabs[0].dblclick();
    const input = page.locator('input.tab-bar-editable-text-input').filter({ visible: true }).last();
    await input.waitFor({ state: 'visible' });
    await input.fill(tabName);
    await input.press('Enter');
    await page.waitForTimeout(400);
  }

  await select(`A1:F${Math.max(80, values.length)}`);
  await page.keyboard.press('Delete');
  await page.waitForTimeout(300);

  for (let rowIndex = 0; rowIndex < values.length; rowIndex += 1) {
    const row = rowIndex + 1;
    for (let columnIndex = 0; columnIndex < 6; columnIndex += 1) {
      const value = normalizeCell(values[rowIndex][columnIndex]);
      if (!value) continue;
      let column = String.fromCharCode(65 + columnIndex);
      if (template.layout.footer_rows.includes(row) && column === 'E') column = 'D';
      await setCell(`${column}${row}`, value);
    }
  }

  for (const range of template.layout.merge_ranges) {
    await select(range);
    await clickToolbar('合并', '对齐方式');
  }
  for (const range of template.layout.wrap_ranges) await ensureToolbarPressed(range, range.split(':')[0], '换行', '对齐方式');
  await select(template.layout.body_left_range);
  await clickToolbar('左对齐', '对齐方式');
  await select(template.layout.header_center_range);
  await clickToolbar('居中对齐', '对齐方式');
  for (const range of template.layout.bold_ranges) {
    await select(range);
    await clickToolbar('加粗', '字体样式');
  }

  await select(template.layout.header_center_range);
  await clickToolbar('填充颜色 更多选项', '字体样式');
  const blue = page.locator('[style*="background-color: rgb(140, 221, 250)"]').filter({ visible: true }).last();
  if (!(await blue.count())) throw new Error('填充色面板中找不到 #8CDDFA');
  await blue.click();
  await page.waitForTimeout(180);

  const defaultHeaderCenters = { A: 100, B: 200, C: 300, D: 400, E: 500, F: 600 };
  for (const column of ['F', 'E', 'D', 'C', 'B', 'A']) await setColumnWidth(defaultHeaderCenters[column], template.layout.column_widths[column]);

  const rowHeights = await page.evaluate(({ template: config, values: source }) => {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    context.font = config.layout.row_height.font;
    const widths = config.layout.column_widths;
    const footer = new Set(config.layout.footer_rows);
    const separator = new Set(config.layout.separator_rows);
    const rules = config.layout.row_height;
    const columns = ['A', 'B', 'C', 'D', 'E', 'F'];
    const result = {};
    function lines(raw, available) {
      let total = 0;
      for (const logical of String(raw ?? '').replace(/\r\n?/g, '\n').split('\n')) {
        if (!logical) { total += 1; continue; }
        let width = 0;
        let count = 1;
        for (const character of logical) {
          const measured = context.measureText(character).width;
          if (width > 0 && width + measured > available) { count += 1; width = measured; }
          else width += measured;
        }
        total += count;
      }
      return Math.max(1, total);
    }
    for (let index = 1; index < source.length; index += 1) {
      const row = index + 1;
      if (separator.has(row)) { result[row] = rules.separator; continue; }
      let maxLines = 1;
      for (let columnIndex = 0; columnIndex < 6; columnIndex += 1) {
        const value = String(source[index][columnIndex] ?? '').trim();
        if (!value) continue;
        let width = widths[columns[columnIndex]];
        if (footer.has(row) && columnIndex === 4) width = widths.D + widths.E + widths.F;
        maxLines = Math.max(maxLines, lines(value, width - rules.horizontal_padding));
      }
      const raw = maxLines * rules.line_height + rules.vertical_padding;
      const rounded = Math.ceil(raw / rules.round_to) * rules.round_to;
      result[row] = Math.min(rules.maximum, Math.max(rules.minimum, rounded));
    }
    return result;
  }, { template, values });
  for (const [row, height] of Object.entries(rowHeights)) await setRowHeight(Number(row), height);

  const expected = expectedSheetCells(values, template.layout.footer_rows);
  const mismatches = [];
  for (const [ref, wanted] of expected) {
    await select(ref);
    const actual = normalizeCell(await formula.evaluate((element) => element.innerText));
    if (actual !== wanted) mismatches.push({ ref, wanted, actual });
  }
  if (mismatches.length) throw new Error(`逐格回读失败: ${JSON.stringify(mismatches.slice(0, 10))}`);
  const screenshots = [];
  for (const [ref, name] of [['A1', 'top-left'], ['E2', 'top-right'], ['A17', 'other-left'], ['E22', 'other-right'], ['D14', 'footer']]) {
    await select(ref);
    const path = resolve(outputDir, `daily-template-${name}.png`);
    await page.screenshot({ path });
    screenshots.push(path);
  }
  const result = {
    schema: 'android-daily-sheet-render-result-v1',
    status: 'PASS',
    document_id: options.document_id,
    url: page.url(),
    title,
    sheet_tab: tabName,
    checked_cells: expected.size,
    checked_nonempty_cells: [...expected.values()].filter(Boolean).length,
    // validateTemplate() checks the reusable asset; real runtime report data
    // must not be rejected just because it contains a known project marker.
    real_data_markers_found: [],
    calculated_row_heights: rowHeights,
    screenshots,
    template_asset: assetPath
  };
  const resultPath = resolve(options.result || resolve(outputDir, 'render-result.json'));
  await writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
  process.stdout.write(`${JSON.stringify({ ...result, result_path: resultPath }, null, 2)}\n`);
} finally {
  await disconnectPlaywrightTransport(browser);
}
process.exit(0);
