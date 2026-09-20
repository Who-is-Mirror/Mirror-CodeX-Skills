#!/usr/bin/env node

import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { disconnectPlaywrightTransport, loadPlaywrightRuntime, resolveManagedCdpEndpoint } from './playwright_runtime.mjs';

const HOME_URL = 'https://doc.weixin.qq.com/home/recent';

export function isWecomHomeUrl(value) {
  try {
    const url = new URL(value);
    return url.hostname === 'doc.weixin.qq.com' && url.pathname.startsWith('/home/');
  } catch {
    return false;
  }
}

export function isWecomLoginUrl(value) {
  try {
    const url = new URL(value);
    return url.hostname === 'doc.weixin.qq.com' && url.pathname === '/scenario/login.html';
  } catch {
    return false;
  }
}

export function isWecomQrFrameUrl(value) {
  try {
    return new URL(value).hostname === 'login.work.weixin.qq.com';
  } catch {
    return false;
  }
}

function parseArgs(argv) {
  const result = {};
  const allowed = new Set(['--cdp', '--output-dir', '--result']);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!allowed.has(key)) throw new Error(`未知参数: ${key}`);
    const value = argv[++index];
    if (!value || value.startsWith('--')) throw new Error(`${key} 缺少值`);
    result[key.slice(2).replaceAll('-', '_')] = value;
  }
  if (!result.output_dir) throw new Error('缺少 --output-dir');
  return result;
}

async function waitForQrFrame(page, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const frame = page.frames().find((candidate) => isWecomQrFrameUrl(candidate.url()));
    if (frame) {
      const qr = frame.locator('.wwLogin_qrcode_img');
      if (await qr.isVisible().catch(() => false)) return { frame, qr };
    }
    await page.waitForTimeout(150);
  }
  return null;
}

export async function checkWecomLogin(options) {
  const outputDir = resolve(options.output_dir);
  await mkdir(outputDir, { recursive: true });
  const { chromium } = await loadPlaywrightRuntime();
  const browser = await chromium.connectOverCDP(resolveManagedCdpEndpoint(options.cdp).endpoint);
  try {
    const contexts = browser.contexts();
    if (contexts.length !== 1) throw new Error(`Edge CDP context 必须唯一，实际 ${contexts.length}`);
    const context = contexts[0];
    const homePages = context.pages().filter((page) => isWecomHomeUrl(page.url()) || isWecomLoginUrl(page.url()));
    if (homePages.length > 1) throw new Error(`企业微信文档首页/登录页必须唯一，实际 ${homePages.length}`);
    const page = homePages[0] || await context.newPage();
    await page.bringToFront();
    await page.goto(HOME_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(800);

    const qrMatch = await waitForQrFrame(page, isWecomLoginUrl(page.url()) ? 15000 : 1200);
    if (qrMatch) {
      const qrPath = resolve(outputDir, 'wecom-login-qr.png');
      await qrMatch.qr.screenshot({ path: qrPath });
      return {
        schema: 'android-daily-sheet-wecom-login-check-v1',
        status: 'LOGIN_REQUIRED',
        qr_screenshot: qrPath,
        restart_hint: '用户完成扫码后重新运行本检查；若二维码过期，重新运行会生成并截取新的二维码。'
      };
    }
    if (!isWecomHomeUrl(page.url())) throw new Error(`企业微信登录状态不可判定: ${page.url()}`);
    return {
      schema: 'android-daily-sheet-wecom-login-check-v1',
      status: 'PASS',
      home_url: page.url()
    };
  } finally {
    await disconnectPlaywrightTransport(browser);
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const result = await checkWecomLogin(options);
  const resultPath = resolve(options.result || resolve(options.output_dir, 'wecom-login-check.json'));
  await writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
  process.stdout.write(`${JSON.stringify({ ...result, result_path: resultPath }, null, 2)}\n`);
  process.exit(result.status === 'LOGIN_REQUIRED' ? 2 : 0);
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : '';
if (invokedPath === import.meta.url) main();
