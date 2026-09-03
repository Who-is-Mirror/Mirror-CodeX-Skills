#!/usr/bin/env node

import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import {
  BrowserPrerequisiteError,
  DEFAULT_CDP_URL,
  disconnectPlaywrightTransport,
  loadPlaywrightRuntime,
  playwrightInstallRequest,
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

const cdp = options.cdp || DEFAULT_CDP_URL;
let browser;
try {
  browser = await runtime.chromium.connectOverCDP(cdp, { timeout: 5000 });
  const contexts = browser.contexts();
  if (contexts.length !== 1) throw new Error(`Edge CDP context 必须唯一，实际 ${contexts.length}`);
  await emit({
    schema: 'android-daily-browser-prerequisites-v1',
    status: 'PASS',
    node: process.version,
    playwright_module: runtime.modulePath,
    playwright_source: runtime.source,
    cdp,
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
    cdp,
    user_action_required: '请确认是否允许启动或重启带远程调试端口的 Edge；不要关闭用户浏览器或改动配置后自行重试。',
  }, options.result);
  process.exitCode = 3;
} finally {
  await disconnectPlaywrightTransport(browser);
}
process.exit(process.exitCode || 0);
