#!/usr/bin/env node

import { disconnectPlaywrightTransport, loadPlaywrightRuntime } from './playwright_runtime.mjs';

const DEFAULT_CDP = 'http://127.0.0.1:9223';

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!['--cdp', '--reference-document-id'].includes(key)) throw new Error(`未知参数: ${key}`);
    const value = argv[++index];
    if (!value || value.startsWith('--')) throw new Error(`${key} 缺少值`);
    result[key.slice(2).replaceAll('-', '_')] = value;
  }
  return result;
}

async function lastVisible(locator) {
  const candidates = await locator.all();
  for (let index = candidates.length - 1; index >= 0; index -= 1) {
    if (await candidates[index].isVisible().catch(() => false)) return candidates[index];
  }
  return null;
}

const options = parseArgs(process.argv.slice(2));
const { chromium } = await loadPlaywrightRuntime();
const browser = await chromium.connectOverCDP(options.cdp || DEFAULT_CDP);
try {
  const contexts = browser.contexts();
  if (contexts.length !== 1) throw new Error(`Edge CDP context 必须唯一，实际 ${contexts.length}`);
  const context = contexts[0];
  const homeMatches = context.pages().filter((page) => /^https:\/\/doc\.weixin\.qq\.com\/home\//.test(page.url()));
  if (homeMatches.length !== 1) throw new Error(`企业微信文档首页必须唯一，实际 ${homeMatches.length}`);
  const home = homeMatches[0];
  const before = new Set(context.pages());
  await home.getByRole('button', { name: '新建', exact: true }).click();
  await home.waitForTimeout(250);
  const sheetItem = await lastVisible(home.locator('#item-0-3'))
    || await lastVisible(home.getByText('表格', { exact: true }));
  if (!sheetItem) throw new Error('新建菜单中找不到可见“表格”项');
  const pagePromise = context.waitForEvent('page', { timeout: 8000 }).catch(() => null);
  await sheetItem.click();
  let page = await pagePromise;
  if (!page) {
    await home.waitForURL(/\/sheet\//, { timeout: 15000 });
    page = home;
  }
  await page.waitForLoadState('domcontentloaded');
  await page.locator('input.bar-label').filter({ visible: true }).first().waitFor({ state: 'visible', timeout: 30000 });
  if (!before.has(page) && !context.pages().includes(page)) throw new Error('新表格页面未进入当前 Edge context');
  const match = page.url().match(/\/sheet\/([^?/#]+)/);
  if (!match) throw new Error(`新页面不是企业微信在线表格: ${page.url()}`);
  const documentId = match[1];
  if (options.reference_document_id && documentId === options.reference_document_id) throw new Error('新表格 document ID 与旧模板相同，拒绝继续');
  process.stdout.write(`${JSON.stringify({ status: 'PASS', document_id: documentId, url: page.url() }, null, 2)}\n`);
} finally {
  await disconnectPlaywrightTransport(browser);
}
process.exit(0);
