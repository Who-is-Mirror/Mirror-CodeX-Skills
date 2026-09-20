import { access } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { fileURLToPath } from 'node:url';

export const EDGE_CDP_SESSION_NAME = 'android-daily-sheet-roundtrip';
const SKILL_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..');

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

function sharedEnsureCandidates() {
  const candidates = [];
  const explicit = String(process.env.EDGE_CDP_SESSION_SKILL_ROOT || '').trim();
  if (explicit) candidates.push(resolve(explicit));
  candidates.push(resolve(SKILL_DIR, '..', 'edge-cdp-session'));
  if (process.env.CODEX_HOME) candidates.push(resolve(process.env.CODEX_HOME, 'skills', 'edge-cdp-session'));
  return [...new Set(candidates)].map((root) => resolve(root, 'scripts', 'ensure_session.py'));
}

export function locateSharedEnsureScript({ exists = (path) => {
  try { return createRequire(import.meta.url)('node:fs').statSync(path).isFile(); } catch { return false; }
} } = {}) {
  const candidates = sharedEnsureCandidates();
  const found = candidates.find(exists);
  if (found) return found;
  throw new BrowserPrerequisiteError(
    'EDGE_CDP_SKILL_MISSING',
    '未找到共享 edge-cdp-session 技能；请将它与 android-daily-sheet-roundtrip 一起安装',
    { candidates },
  );
}

function explicitCdpEndpoint(value) {
  const raw = String(value || '').trim();
  if (!raw) return null;
  let parsed;
  try { parsed = new URL(raw); } catch { throw new BrowserPrerequisiteError('CDP_OVERRIDE_INVALID', '--cdp 必须是本机 HTTP 或 WebSocket endpoint'); }
  if (!['http:', 'ws:'].includes(parsed.protocol) || parsed.hostname !== '127.0.0.1' || !parsed.port) {
    throw new BrowserPrerequisiteError('CDP_OVERRIDE_INVALID', '--cdp 只允许显式的 127.0.0.1 endpoint');
  }
  return raw;
}

export function resolveManagedCdpEndpoint(override, { spawn = spawnSync, ensureScript } = {}) {
  const explicit = explicitCdpEndpoint(override);
  const script = ensureScript || locateSharedEnsureScript();
  const args = [script, '--session', EDGE_CDP_SESSION_NAME];
  if (explicit) args.push('--port', new URL(explicit).port);
  const completed = spawn('python3', args, {
    encoding: 'utf8',
    env: process.env,
  });
  if (completed.error) {
    throw new BrowserPrerequisiteError('EDGE_CDP_START_FAILED', `无法运行共享 Edge 会话技能: ${completed.error.message}`);
  }
  let result;
  try { result = JSON.parse(String(completed.stdout || '').trim()); } catch {
    throw new BrowserPrerequisiteError(
      'EDGE_CDP_START_FAILED',
      '共享 Edge 会话技能未返回有效 JSON',
      { exit_code: completed.status, stderr: String(completed.stderr || '').trim() },
    );
  }
  if (completed.status !== 0 || result.ready !== true || result.session !== EDGE_CDP_SESSION_NAME) {
    throw new BrowserPrerequisiteError(
      'EDGE_CDP_START_FAILED',
      result.error || '共享 Edge 会话未就绪',
      { exit_code: completed.status, result },
    );
  }
  const endpoint = explicitCdpEndpoint(result.cdp_http_endpoint);
  if (!endpoint) {
    throw new BrowserPrerequisiteError('EDGE_CDP_START_FAILED', '共享 Edge 会话缺少已验证的 HTTP endpoint');
  }
  if (explicit && new URL(endpoint).port !== new URL(explicit).port) {
    throw new BrowserPrerequisiteError('EDGE_CDP_START_FAILED', '共享 Edge 会话返回的固定排障端口与请求不一致');
  }
  const source = explicit
    ? 'managed-fixed-port-override'
    : result.started ? 'managed-edge-started' : 'managed-edge-reused';
  return { endpoint, source, session: result };
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
