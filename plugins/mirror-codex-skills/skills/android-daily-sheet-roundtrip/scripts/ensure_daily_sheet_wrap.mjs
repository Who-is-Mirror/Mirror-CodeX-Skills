#!/usr/bin/env node

import { mkdir, readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validateTemplate } from './sheet_template.mjs';
import { disconnectPlaywrightTransport, loadPlaywrightRuntime, resolveManagedCdpEndpoint } from './playwright_runtime.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const template = validateTemplate(JSON.parse(await readFile(resolve(root, 'assets', 'daily-sheet-template.json'), 'utf8')));
const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, index, all) => {
  if (value.startsWith('--')) pairs.push([value.slice(2).replaceAll('-', '_'), all[index + 1]]);
  return pairs;
}, []));
if (!args.document_id || !args.output_dir) throw new Error('需要 --document-id 和 --output-dir');

async function visibleButton(page, name) {
  const candidates = await page.getByRole('button', { name, exact: true }).all();
  for (let index = candidates.length - 1; index >= 0; index -= 1) if (await candidates[index].isVisible().catch(() => false)) return candidates[index];
  return null;
}

const { chromium } = await loadPlaywrightRuntime();
const browser = await chromium.connectOverCDP(resolveManagedCdpEndpoint(args.cdp).endpoint);
try {
  const pages = browser.contexts().flatMap((context) => context.pages()).filter((page) => page.url().includes(`/sheet/${args.document_id}`));
  if (pages.length !== 1) throw new Error(`目标表格页面必须唯一，实际 ${pages.length}`);
  const page = pages[0];
  const nameBox = page.locator('input.bar-label').filter({ visible: true }).first();
  async function select(ref) {
    await page.keyboard.press('Escape');
    await page.mouse.click(700, 250);
    await page.keyboard.press('Escape');
    await nameBox.fill(ref);
    await nameBox.press('Enter');
    await page.waitForTimeout(80);
  }
  async function wrapButton() {
    let button = await visibleButton(page, '换行');
    if (!button) {
      const group = await visibleButton(page, '对齐方式');
      if (!group) throw new Error('找不到对齐方式工具组');
      await group.click();
      await page.waitForTimeout(100);
      button = await visibleButton(page, '换行');
    }
    if (!button) throw new Error('找不到换行按钮');
    return button;
  }
  const checks = {};
  for (const range of template.layout.wrap_ranges) {
    const anchor = range.split(':')[0];
    await select(anchor);
    let button = await wrapButton();
    if (await button.getAttribute('aria-pressed') !== 'true') {
      await select(range);
      button = await wrapButton();
      await button.click();
      await page.waitForTimeout(150);
    }
    await select(anchor);
    button = await wrapButton();
    checks[range] = await button.getAttribute('aria-pressed') === 'true';
    if (!checks[range]) throw new Error(`${range} 换行校验失败`);
  }
  const outputDir = resolve(args.output_dir);
  await mkdir(outputDir, { recursive: true });
  const screenshots = [];
  for (const [ref, name] of [['D14', 'footer-wrap'], ['D26', 'other-footer-wrap']]) {
    await select(ref);
    const path = resolve(outputDir, `${name}.png`);
    await page.screenshot({ path });
    screenshots.push(path);
  }
  process.stdout.write(`${JSON.stringify({ status: 'PASS', document_id: args.document_id, wrap_checks: checks, screenshots }, null, 2)}\n`);
} finally {
  await disconnectPlaywrightTransport(browser);
}
process.exit(0);
