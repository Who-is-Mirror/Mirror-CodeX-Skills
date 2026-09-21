#!/usr/bin/env node

import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import {
  BrowserPrerequisiteError,
  disconnectPlaywrightTransport,
  loadPlaywrightRuntime,
  playwrightInstallRequest,
  resolveManagedCdpEndpoint,
} from './playwright_runtime.mjs';

function parseArgs(argv) {
  const result = {};
  const allowed = new Set(['--cdp', '--playwright-module', '--result']);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!allowed.has(key)) throw new Error(`未知参数: ${key}`);
    const value = argv[++index];
    if (!value || value.startsWith('--')) throw new Error(`${key} 缺少值`);
    result[key.slice(2).replaceAll('-', '_')] = value;
  }
  return result;
}

async function emit(result, resultPath) {
  if (resultPath) {
    const path = resolve(resultPath);
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
  }
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

const options = parseArgs(process.argv.slice(2));
let runtime;
try {
  runtime = await loadPlaywrightRuntime({ configuredPath: options.playwright_module });
} catch (error) {
  const result = {
    schema: 'android-daily-browser-prerequisites-v1',
    status: error instanceof BrowserPrerequisiteError ? error.code : 'PLAYWRIGHT_INVALID',
    message: error.message,
    ...(error.details || {}),
    install_request: error.details?.install_request || playwrightInstallRequest(),
  };
  await emit(result, options.result);
  process.exit(2);
}

let browser;
let cdpSession;
try {
  cdpSession = resolveManagedCdpEndpoint(options.cdp);
  browser = await runtime.chromium.connectOverCDP(cdpSession.endpoint, { timeout: 5000 });
  const contexts = browser.contexts();
  if (contexts.length !== 1) throw new Error(`Edge CDP context 必须唯一，实际 ${contexts.length}`);
  await emit({
    schema: 'android-daily-browser-prerequisites-v1',
    status: 'PASS',
    node: process.version,
    playwright_module: runtime.modulePath,
    playwright_source: runtime.source,
    cdp: cdpSession.endpoint,
    cdp_source: cdpSession.source,
    context_count: contexts.length,
    page_count: contexts[0].pages().length,
  }, options.result);
} catch (error) {
  await emit({
    schema: 'android-daily-browser-prerequisites-v1',
    status: 'CDP_UNAVAILABLE',
    message: `无法连接后台 Edge CDP: ${error.message}`,
    playwright_module: runtime.modulePath,
    playwright_source: runtime.source,
    cdp: cdpSession?.endpoint || options.cdp || null,
    cdp_source: cdpSession?.source || null,
    user_action_required: error instanceof BrowserPrerequisiteError
      ? '按错误信息修复共享 Edge 会话前置条件后重试；无需开启日常 Edge 的浏览器调试开关。'
      : '专用 Edge 已由共享技能管理；请根据连接错误修复环境后重试。',
  }, options.result);
  process.exitCode = 3;
} finally {
  await disconnectPlaywrightTransport(browser);
}
process.exit(process.exitCode || 0);
