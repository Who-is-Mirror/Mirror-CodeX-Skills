#!/usr/bin/env node

const crypto = require('crypto');
const childProcess = require('child_process');
const fs = require('fs');
const path = require('path');

function runtimePaths() {
  const python = process.env.PYTHON || 'python3';
  const result = childProcess.spawnSync(python, [path.join(__dirname, 'runtime_paths.py'), '--json'], {
    encoding: 'utf8',
  });
  if (result.status !== 0) throw new Error((result.stderr || result.stdout || 'runtime path resolution failed').trim());
  return JSON.parse(result.stdout);
}

const RUNTIME_PATHS = runtimePaths();
const SYSTEM_ORIGIN = RUNTIME_PATHS.system_origin;
const VAULT_DIR = RUNTIME_PATHS.credential_vault;
const AAD = Buffer.from('performance-task-entry-credential-v1');

class CdpClient {
  constructor(url) {
    this.url = url;
    this.sequence = 0;
    this.pending = new Map();
  }

  async connect() {
    this.socket = new WebSocket(this.url);
    this.socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      if (message.error) waiter.reject(new Error(message.error.message));
      else waiter.resolve(message.result);
    };
    await new Promise((resolve, reject) => {
      this.socket.onopen = resolve;
      this.socket.onerror = reject;
    });
  }

  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const response = await this.send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (response.exceptionDetails) throw new Error('browser evaluation failed');
    return response.result.value;
  }

  close() {
    this.socket.close();
  }
}

function readCredentials() {
  const key = fs.readFileSync(path.join(VAULT_DIR, 'master.key'));
  const envelope = JSON.parse(fs.readFileSync(path.join(VAULT_DIR, 'credential.enc.json'), 'utf8'));
  const nonce = Buffer.from(envelope.nonce, 'base64');
  const encrypted = Buffer.from(envelope.ciphertext, 'base64');
  const tag = encrypted.subarray(encrypted.length - 16);
  const body = encrypted.subarray(0, encrypted.length - 16);
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, nonce);
  decipher.setAAD(AAD);
  decipher.setAuthTag(tag);
  const cleartext = Buffer.concat([decipher.update(body), decipher.final()]);
  const credentials = JSON.parse(cleartext.toString('utf8'));
  if (!credentials.username || !credentials.password) throw new Error('credential vault is incomplete');
  return credentials;
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`CDP endpoint returned HTTP ${response.status}`);
  return response.json();
}

async function waitFor(client, expression, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await client.evaluate(expression).catch(() => false)) return true;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

const AUTH_CHECK = `(async () => {
  if (location.origin !== ${JSON.stringify(SYSTEM_ORIGIN)}) return false;
  const token = localStorage.getItem('token');
  if (!token) return false;
  try {
    const response = await fetch('/mgmt/userRole/queryDirectoryAll', { headers: { token } });
    const body = await response.json();
    return body && body.code === 200;
  } catch (_) {
    return false;
  }
})()`;

async function main() {
  if (process.argv.includes('--check-vault')) {
    readCredentials();
    console.log(JSON.stringify({ ready: true, decrypted_in_memory: true }));
    return;
  }
  const cdpArgument = process.argv.indexOf('--cdp-endpoint');
  if (cdpArgument < 0 || !process.argv[cdpArgument + 1]) {
    throw new Error('resolved --cdp-endpoint is required');
  }
  // Check the raw explicit port before URL normalization erases HTTP port 80.
  const rawCdpEndpoint = process.argv[cdpArgument + 1];
  const cdpMatch = /^http:\/\/127\.0\.0\.1:([0-9]+)\/?$/.exec(rawCdpEndpoint);
  const cdpPort = cdpMatch ? Number(cdpMatch[1]) : NaN;
  if (!cdpMatch || cdpMatch[0] !== rawCdpEndpoint || !Number.isInteger(cdpPort) || cdpPort < 1 || cdpPort > 65535) {
    throw new Error('resolved CDP endpoint must be a local HTTP origin');
  }
  const CDP_HTTP = new URL(`http://127.0.0.1:${cdpPort}`).origin;
  await fetchJson(`${CDP_HTTP}/json/version`);
  let targets = await fetchJson(`${CDP_HTTP}/json/list`);
  let target = targets.find((item) => item.type === 'page' && item.url.startsWith(SYSTEM_ORIGIN));
  if (!target) {
    target = await fetchJson(`${CDP_HTTP}/json/new?${encodeURIComponent(`${SYSTEM_ORIGIN}/#/login`)}`, { method: 'PUT' });
  }
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.connect();
  await client.send('Runtime.enable');

  if (await client.evaluate(AUTH_CHECK)) {
    console.log(JSON.stringify({ authenticated: true, source: 'existing_session' }));
    client.close();
    return;
  }

  const credentials = readCredentials();
  await client.send('Page.navigate', { url: `${SYSTEM_ORIGIN}/#/login` });
  const loginReady = await waitFor(
    client,
    `document.querySelectorAll('.ms-login input').length >= 2 && !!document.querySelector('.ms-login button')`,
    15000,
  );
  if (!loginReady) throw new Error('login form did not become ready');

  const username = JSON.stringify(credentials.username);
  const password = JSON.stringify(credentials.password);
  const filled = await client.evaluate(`(() => {
    const inputs = document.querySelectorAll('.ms-login input');
    const button = document.querySelector('.ms-login button');
    if (inputs.length < 2 || !button) return false;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(inputs[0], ${username});
    inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
    setter.call(inputs[1], ${password});
    inputs[1].dispatchEvent(new Event('input', { bubbles: true }));
    button.click();
    return true;
  })()`);
  if (!filled) throw new Error('login form changed');

  const authenticated = await waitFor(client, AUTH_CHECK, 20000);
  if (!authenticated) throw new Error('stored credentials were rejected or login timed out');
  await client.send('Page.navigate', { url: `${SYSTEM_ORIGIN}/#/task` });
  console.log(JSON.stringify({ authenticated: true, source: 'encrypted_vault' }));
  client.close();
}

main().catch((error) => {
  console.error(JSON.stringify({ authenticated: false, error: error.message }));
  process.exit(1);
});
