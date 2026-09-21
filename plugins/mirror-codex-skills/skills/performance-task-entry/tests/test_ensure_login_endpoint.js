// Execute the actual login entry point; runtime discovery, transport, and secrets are mocked.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'ensure_login.js'), 'utf8');
const transportStop = 'test stops at the first mocked fetch';

async function exercise(endpoint, expectedOrigin) {
  const fetches = [];
  const errors = [];
  let secretReads = 0;
  let exitCode = 0;
  const context = {
    require(name) {
      if (name === 'child_process') {
        return { spawnSync() {
          return { status: 0, stdout: JSON.stringify({ system_origin: 'http://example.invalid', credential_vault: 'never-read' }) };
        } };
      }
      if (name === 'fs') {
        return { readFileSync() { secretReads++; throw new Error('credential read forbidden'); } };
      }
      if (name === 'crypto' || name === 'path') return require(name);
      throw new Error(`unexpected module: ${name}`);
    },
    __dirname: 'mock-script-directory',
    Buffer, URL,
    process: {
      env: {},
      argv: ['node', 'ensure_login.js', ...(endpoint === null ? [] : ['--cdp-endpoint', endpoint])],
      exit(code) { exitCode = code; },
    },
    console: {
      log() { throw new Error('unexpected login completion'); },
      error(text) { errors.push(JSON.parse(text)); },
    },
    async fetch(url) {
      fetches.push(url);
      throw new Error(transportStop);
    },
  };
  vm.runInNewContext(source, context, { filename: 'ensure_login.js', timeout: 1000 });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(secretReads, 0, `credential access for ${endpoint}`);
  assert.equal(exitCode, 1, `entry point did not stop for ${endpoint}`);
  assert.equal(errors.length, 1, `missing diagnostic for ${endpoint}`);
  if (expectedOrigin !== null) {
    assert.deepEqual(fetches, [`${expectedOrigin}/json/version`], `endpoint propagation for ${endpoint}`);
    assert.equal(errors[0].error, transportStop);
  } else {
    assert.deepEqual(fetches, [], `invalid endpoint reached transport: ${endpoint}`);
    assert.match(errors[0].error, /resolved (?:--cdp-endpoint is required|CDP endpoint must be a local HTTP origin)/);
  }
}

async function main() {
  const valid = [
    ['http://127.0.0.1:1', 'http://127.0.0.1:1'],
    ['http://127.0.0.1:80', 'http://127.0.0.1'],
    ['http://127.0.0.1:43123', 'http://127.0.0.1:43123'],
    ['http://127.0.0.1:65535', 'http://127.0.0.1:65535'],
    ['http://127.0.0.1:80/', 'http://127.0.0.1'],
    ['http://127.0.0.1:43123/', 'http://127.0.0.1:43123'],
    ['http://127.0.0.1:00080', 'http://127.0.0.1'],
  ];
  const invalid = [
    null, '', 'http://127.0.0.1', 'http://127.0.0.1/', 'http://127.0.0.1:',
    'http://127.0.0.1:0', 'http://127.0.0.1:65536', 'http://127.0.0.1:-1',
    'http://127.0.0.1:1.5', 'http://127.0.0.1:1e2', 'http://127.0.0.1:abc',
    'http://127.0.0.1:+80', 'http://127.0.0.1:99999999999999999999999999',
    'https://127.0.0.1:43123', 'ws://127.0.0.1:43123', 'file://127.0.0.1:43123',
    'http://localhost:43123', 'http://example.invalid:43123', 'http://127.1:43123',
    'http://[::1]:43123', 'http://user@127.0.0.1:43123', 'http://user:pass@127.0.0.1:43123',
    'http://@127.0.0.1:43123', 'http://127.0.0.1:43123?query', 'http://127.0.0.1:43123?',
    'http://127.0.0.1:43123#fragment', 'http://127.0.0.1:43123#',
    'http://127.0.0.1:43123/path', 'http://127.0.0.1:43123/./',
    'http://127.0.0.1:43123//', 'http://127.0.0.1:43123\\',
    ' http://127.0.0.1:80', 'http://127.0.0.1:80\n', 'http://127.0.0.1:80\r\n',
  ];
  for (const [endpoint, origin] of valid) await exercise(endpoint, origin);
  for (const endpoint of invalid) await exercise(endpoint, null);
  console.log(`LOGIN_ENDPOINT_VM_PASS valid=${valid.length} rejected=${invalid.length} secretReads=0`);
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
