#!/usr/bin/env node

import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { planDateTabRetention } from './date_tab_retention.mjs';
import { disconnectPlaywrightTransport, loadPlaywrightRuntime } from './playwright_runtime.mjs';

const DEFAULT_CDP = 'http://127.0.0.1:9223';

function parseArgs(argv) {
  const result = { max_tabs: 5, replace_existing: false };
  const valueOptions = new Set(['--cdp', '--document-id', '--date', '--max-tabs', '--output']);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === '--replace-existing') { result.replace_existing = true; continue; }
    if (!valueOptions.has(key)) throw new Error(`未知参数: ${key}`);
    const value = argv[++index];
    if (!value || value.startsWith('--')) throw new Error(`${key} 缺少值`);
    result[key.slice(2).replaceAll('-', '_')] = value;
  }
  if (!result.document_id) throw new Error('缺少 --document-id');
  if (!result.date) throw new Error('缺少 --date');
  result.max_tabs = Number(result.max_tabs);
  if (!Number.isInteger(result.max_tabs) || result.max_tabs < 1) throw new Error('--max-tabs 必须是正整数');
  return result;
}

async function visibleTabs(page) {
  const tabs = [];
  for (const tab of await page.locator('[role="tab"].tab-bar-item').all()) {
    if (!await tab.isVisible().catch(() => false)) continue;
    tabs.push({
      name: (await tab.innerText()).trim(),
      id: await tab.getAttribute('data-tab-id'),
      locator: tab,
    });
  }
  return tabs;
}

async function uniqueTab(page, name) {
  const matches = (await visibleTabs(page)).filter((tab) => tab.name === name);
  if (matches.length !== 1) throw new Error(`页签“${name}”必须唯一，实际 ${matches.length}`);
  return matches[0];
}

async function renameTab(page, tab, nextName) {
  await tab.locator.dblclick();
  const input = page.locator('input.tab-bar-editable-text-input').filter({ visible: true }).last();
  await input.waitFor({ state: 'visible' });
  await input.fill(nextName);
  await input.press('Enter');
  await page.waitForTimeout(180);
  await uniqueTab(page, nextName);
}

async function addBlankTab(page) {
  const before = await visibleTabs(page);
  const beforeIds = new Set(before.map((tab) => tab.id));
  const add = page.locator('.statusbar-sheet-add').filter({ visible: true });
  if (await add.count() !== 1) throw new Error(`新增工作表按钮必须唯一，实际 ${await add.count()}`);
  const worksheetCommand = page.locator('.sheet-type-selector .add-sheet-sub-menu-item').filter({ visible: true });
  if (await worksheetCommand.count() === 1) {
    await worksheetCommand.click();
  } else {
    await add.click();
    const outcome = await Promise.race([
      page.waitForFunction((ids) => [...document.querySelectorAll('[role="tab"].tab-bar-item')]
        .filter((tab) => tab.offsetParent !== null)
        .some((tab) => !ids.includes(tab.getAttribute('data-tab-id'))), [...beforeIds], { timeout: 1800 })
        .then(() => 'created')
        .catch(() => null),
      worksheetCommand.waitFor({ state: 'visible', timeout: 1800 })
        .then(() => 'menu')
        .catch(() => null),
    ]);
    if (outcome === 'menu') await worksheetCommand.click();
  }
  await page.waitForFunction((ids) => {
    const tabs = [...document.querySelectorAll('[role="tab"].tab-bar-item')].filter((tab) => tab.offsetParent !== null);
    return tabs.some((tab) => !ids.includes(tab.getAttribute('data-tab-id')));
  }, [...beforeIds]);
  const after = await visibleTabs(page);
  const created = after.filter((tab) => !beforeIds.has(tab.id));
  if (created.length !== 1) throw new Error(`新增后无法唯一识别空白工作表，实际 ${created.length}`);
  return created[0];
}

async function deleteTab(page, name) {
  const tab = await uniqueTab(page, name);
  await tab.locator.hover();
  await tab.locator.locator('.tab-bar-item-menu-arrow').click({ force: true });
  const command = page.locator('.mainmenu-item-sheet-tab-delete-sheet').filter({ visible: true });
  await command.first().waitFor({ state: 'visible' });
  if (await command.count() !== 1) throw new Error(`删除工作表命令必须唯一，实际 ${await command.count()}`);
  if (await command.getAttribute('aria-disabled') === 'true') throw new Error(`删除工作表命令不可用: ${name}`);
  await command.click();
  const buttons = page.getByRole('button', { name: /^(删除|确定)$/ });
  for (let index = (await buttons.count()) - 1; index >= 0; index -= 1) {
    if (await buttons.nth(index).isVisible().catch(() => false)) {
      await buttons.nth(index).click();
      break;
    }
  }
  await page.waitForFunction((tabName) => ![...document.querySelectorAll('[role="tab"].tab-bar-item')]
    .filter((element) => element.offsetParent !== null)
    .some((element) => element.innerText.trim() === tabName), name);
}

async function reorderTabs(page, desiredOrder) {
  for (let destinationIndex = 0; destinationIndex < desiredOrder.length; destinationIndex += 1) {
    let current = await visibleTabs(page);
    const sourceIndex = current.findIndex((tab) => tab.name === desiredOrder[destinationIndex]);
    if (sourceIndex < 0) throw new Error(`排序时缺少页签: ${desiredOrder[destinationIndex]}`);
    if (sourceIndex === destinationIndex) continue;
    const sourceBox = await current[sourceIndex].locator.boundingBox();
    const destinationBox = await current[destinationIndex].locator.boundingBox();
    if (!sourceBox || !destinationBox) throw new Error(`排序目标不可见: ${desiredOrder[destinationIndex]}`);
    const sourceX = sourceBox.x + sourceBox.width / 2;
    const sourceY = sourceBox.y + sourceBox.height / 2;
    const destinationX = sourceIndex > destinationIndex
      ? destinationBox.x + 2
      : destinationBox.x + destinationBox.width - 2;
    const destinationY = destinationBox.y + destinationBox.height / 2;
    await page.mouse.move(sourceX, sourceY);
    await page.mouse.down();
    await page.mouse.move(sourceX + (sourceIndex > destinationIndex ? -10 : 10), sourceY, { steps: 5 });
    await page.mouse.move(destinationX, destinationY, { steps: 20 });
    await page.waitForTimeout(120);
    await page.mouse.up();
    await page.waitForTimeout(280);
    current = await visibleTabs(page);
    if (current[destinationIndex]?.name !== desiredOrder[destinationIndex]) {
      throw new Error(`页签拖拽排序失败: 期望 ${desiredOrder.join(', ')}，实际 ${current.map((tab) => tab.name).join(', ')}`);
    }
  }
}

const options = parseArgs(process.argv.slice(2));
const { chromium } = await loadPlaywrightRuntime();
const browser = await chromium.connectOverCDP(options.cdp || DEFAULT_CDP);
let result;
try {
  const pages = browser.contexts().flatMap((context) => context.pages()).filter((page) => page.url().includes(`/sheet/${options.document_id}`));
  if (pages.length !== 1) throw new Error(`目标受管工作簿页面必须唯一，实际 ${pages.length}`);
  const page = pages[0];
  await page.bringToFront();
  page.setDefaultTimeout(15000);
  await page.locator('input.bar-label').filter({ visible: true }).first().waitFor({ state: 'visible' });
  const before = await visibleTabs(page);
  const blankBootstrap = before.length === 1 && await page.evaluate(() => {
    const app = SpreadsheetApp;
    const sheet = app?.workbook?.activeSheet;
    if (!sheet || !app?.e2eTools?.getCellEditValue) return false;
    for (let row = 0; row < 80; row += 1) for (let column = 0; column < 6; column += 1) {
      const cell = sheet.getCellDataAtPosition(row, column);
      if (String(app.e2eTools.getCellEditValue(app.workbook, row, column, sheet.getSheetId(), cell) ?? '').trim()) return false;
    }
    return true;
  });
  const plan = planDateTabRetention(before.map((tab) => tab.name), options.date, {
    maxTabs: options.max_tabs,
    replaceExisting: options.replace_existing,
    allowBlankBootstrap: blankBootstrap,
  });

  if (plan.action === 'bootstrap') {
    await renameTab(page, before[0], options.date);
  } else {
    let created = null;
    if (plan.deleteTabs.length && before.length === 1) created = await addBlankTab(page);
    for (const name of plan.deleteTabs) await deleteTab(page, name);
    if (!created) created = await addBlankTab(page);
    await renameTab(page, created, options.date);
  }
  await reorderTabs(page, plan.desiredOrder);
  const target = await uniqueTab(page, options.date);
  await target.locator.click();
  const after = await visibleTabs(page);
  const afterNames = after.map((tab) => tab.name);
  if (JSON.stringify(afterNames) !== JSON.stringify(plan.desiredOrder)) throw new Error(`最终日期顺序不一致: ${afterNames.join(', ')}`);
  if (afterNames.length > options.max_tabs) throw new Error(`最终日期页签超过 ${options.max_tabs} 个`);
  result = {
    schema: 'android-daily-managed-tab-result-v1',
    status: 'PASS',
    document_id: options.document_id,
    target_date: options.date,
    action: plan.action,
    deleted_tabs: plan.deleteTabs,
    before_tabs: before.map((tab) => tab.name),
    after_tabs: afterNames,
    max_tabs: options.max_tabs,
  };
  if (options.output) {
    const path = resolve(options.output);
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
  }
} finally {
  await disconnectPlaywrightTransport(browser);
}
process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
process.exit(0);
