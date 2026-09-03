import { access } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const DEFAULT_CDP_URL = 'http://127.0.0.1:9223';

export class BrowserPrerequisiteError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = 'BrowserPrerequisiteError';
    this.code = code;
    this.details = details;
  }
}

async function isFile(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

export function normalizePlaywrightApi(imported) {
  for (const candidate of [imported, imported?.default]) {
    if (typeof candidate?.chromium?.connectOverCDP === 'function') return candidate;
  }
  throw new BrowserPrerequisiteError(
    'PLAYWRIGHT_INVALID',
    'Playwright 模块未导出可用的 chromium.connectOverCDP',
  );
}

export function dedicatedRuntimePrefix() {
  const codexHome = process.env.CODEX_HOME
    ? resolve(process.env.CODEX_HOME)
    : join(homedir(), '.codex');
  return join(codexHome, 'runtime', 'android-daily-sheet-roundtrip');
}

function windowsUserHomeFromCwd() {
  const match = resolve(process.cwd()).match(/^\/mnt\/([a-z])\/users\/([^/]+)/i);
  return match ? `/mnt/${match[1].toLowerCase()}/Users/${match[2]}` : null;
}

function bundledCandidates() {
  const homes = [homedir(), windowsUserHomeFromCwd()].filter(Boolean);
  return homes.map((home) => join(
    home,
    '.cache',
    'codex-runtimes',
    'codex-primary-runtime',
    'dependencies',
    'node',
    'node_modules',
    'playwright',
    'index.mjs',
  ));
}

function localResolvableCandidate() {
  try {
    return createRequire(resolve(process.cwd(), 'package.json')).resolve('playwright');
  } catch {
    return null;
  }
}

export function playwrightInstallRequest() {
  const prefix = dedicatedRuntimePrefix();
  return {
    user_confirmation_required: true,
    action: 'install-local-playwright-library',
    install_prefix: prefix,
    command: `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --prefix "${prefix}" playwright`,
    note: '只安装本地 Node Playwright 库；本工作流连接现有 Edge，不下载或启动另一个浏览器。',
  };
}

export async function loadPlaywrightRuntime({ configuredPath } = {}) {
  const explicit = String(configuredPath || process.env.CODEX_PLAYWRIGHT_MODULE || '').trim();
  const candidates = [];
  if (explicit) {
    candidates.push({ source: 'configured', path: resolve(explicit) });
  } else {
    for (const path of bundledCandidates()) candidates.push({ source: 'codex-bundled', path });
    candidates.push({
      source: 'approved-local-install',
      path: join(dedicatedRuntimePrefix(), 'node_modules', 'playwright', 'index.mjs'),
    });
    const local = localResolvableCandidate();
    if (local) candidates.push({ source: 'workspace-local', path: local });
  }

  const attempted = [];
  for (const candidate of candidates) {
    if (!await isFile(candidate.path)) {
      attempted.push({ ...candidate, outcome: 'missing' });
      continue;
    }
    try {
      const imported = await import(pathToFileURL(candidate.path).href);
      const playwright = normalizePlaywrightApi(imported);
      return { playwright, chromium: playwright.chromium, modulePath: candidate.path, source: candidate.source };
    } catch (error) {
      attempted.push({ ...candidate, outcome: 'invalid', error: error.message });
      if (explicit) {
        throw new BrowserPrerequisiteError(
          'PLAYWRIGHT_INVALID',
          `CODEX_PLAYWRIGHT_MODULE 无法使用: ${candidate.path}`,
          { attempted },
        );
      }
    }
  }

  throw new BrowserPrerequisiteError(
    'PLAYWRIGHT_MISSING',
    '未找到可用的 Playwright Node 库；安装前必须取得用户明确确认',
    { attempted, install_request: playwrightInstallRequest() },
  );
}

export async function disconnectPlaywrightTransport(browser) {
  try { await browser?._connection?.close?.(); } catch {}
}
