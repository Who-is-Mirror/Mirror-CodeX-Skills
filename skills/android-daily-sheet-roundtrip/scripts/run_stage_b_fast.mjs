#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { auditSheetChanges, validateDecisionLedger } from './audit_daily_sheet_changes.mjs';
import { captureDailySheetSnapshot } from './capture_daily_sheet_snapshot.mjs';
import { convertDailySheet, writeFactsFile } from './daily_sheet_to_facts.mjs';

function parseArgs(argv) {
  const options = {};
  const allowed = new Set(['--cdp', '--baseline', '--document-id', '--sheet', '--date', '--decisions', '--output-dir']);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!allowed.has(key)) throw new Error(`未知参数: ${key}`);
    const value = argv[++index];
    if (!value || value.startsWith('--')) throw new Error(`${key} 缺少值`);
    options[key.slice(2).replaceAll('-', '_')] = value;
  }
  for (const key of ['baseline', 'document_id', 'sheet', 'date', 'output_dir']) if (!options[key]) throw new Error(`缺少 --${key.replaceAll('_', '-')}`);
  return options;
}

export function evaluateStageBSnapshot(baseline, snapshot, { reportDate, decisions } = {}) {
  const preliminary = auditSheetChanges(baseline, snapshot);
  const acceptedFindingIds = decisions ? validateDecisionLedger(decisions, preliminary) : [];
  const audit = acceptedFindingIds.length ? auditSheetChanges(baseline, snapshot, { acceptedFindingIds }) : preliminary;
  const facts = audit.status === 'PASS' ? convertDailySheet(snapshot, { reportDate }) : null;
  return { audit, facts };
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  const outputDir = resolve(options.output_dir);
  await mkdir(outputDir, { recursive: true });
  const paths = {
    snapshot: join(outputDir, 'sheet-snapshot.json'),
    screenshot: join(outputDir, 'sheet-snapshot.png'),
    audit: join(outputDir, 'sheet-change-audit.json'),
    facts: join(outputDir, 'daily-facts.json'),
    result: join(outputDir, 'stage-b-result.json'),
  };
  const [baseline, decisions] = await Promise.all([
    readFile(resolve(options.baseline), 'utf8').then(JSON.parse),
    options.decisions ? readFile(resolve(options.decisions), 'utf8').then(JSON.parse) : null,
  ]);
  const started = performance.now();
  const snapshot = await captureDailySheetSnapshot({
    cdpUrl: options.cdp || 'http://127.0.0.1:9223',
    documentId: options.document_id,
    sheetName: options.sheet,
    screenshotPath: paths.screenshot,
  });
  await writeFile(paths.snapshot, `${JSON.stringify(snapshot, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
  const { audit, facts } = evaluateStageBSnapshot(baseline, snapshot, { reportDate: options.date, decisions });
  await writeFile(paths.audit, `${JSON.stringify(audit, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
  if (facts) await writeFactsFile(paths.facts, facts);
  const result = {
    schema: 'android-daily-sheet-stage-b-result-v1',
    status: audit.status,
    next_action: audit.status === 'PASS' ? 'safe-to-run-customer-guard-and-intake-prepare' : audit.next_action,
    online_reads: 1,
    elapsed_ms: Math.round(performance.now() - started),
    paths: { snapshot: paths.snapshot, screenshot: paths.screenshot, audit: paths.audit, facts: facts ? paths.facts : null },
  };
  await writeFile(paths.result, `${JSON.stringify(result, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  if (audit.status !== 'PASS') process.exitCode = 2;
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (isMain) main().then(() => process.exit(process.exitCode || 0)).catch((error) => { process.stderr.write(`run_stage_b_fast: ${error.message}\n`); process.exit(1); });
