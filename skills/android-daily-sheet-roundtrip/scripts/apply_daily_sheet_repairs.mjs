#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { disconnectPlaywrightTransport, loadPlaywrightRuntime, resolveManagedCdpEndpoint } from './playwright_runtime.mjs';

export const REPAIR_SCHEMA = 'android-daily-sheet-repair-plan-v2';
const AUDIT_SCHEMA = 'android-daily-sheet-change-audit-v1';
const REPAIR_KINDS = new Set(['restore', 'renumber', 'canonicalize-label', 'consistency-rewrite']);

function normalize(value) {
  return String(value ?? '').replace(/\r\n?/g, '\n').trim();
}

function normalizeCellValue(value) {
  return String(value ?? '').replace(/\r\n?/g, '\n');
}

export function validateRepairPlan(payload, audit) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('修复计划必须是 JSON 对象');
  if (payload.schema !== REPAIR_SCHEMA) throw new Error(`修复计划 schema 必须是 ${REPAIR_SCHEMA}`);
  if (!audit || audit.schema !== AUDIT_SCHEMA || !Array.isArray(audit.findings) || !Array.isArray(audit.suggested_repairs)) throw new Error(`必须提供 ${AUDIT_SCHEMA} 审计结果`);
  if (payload.baseline_sha256 !== audit.baseline_sha256 || payload.current_sha256 !== audit.current_sha256) throw new Error('STALE_REPAIR_PLAN：修复计划与审计快照哈希不一致');
  if (!Array.isArray(payload.repairs) || payload.repairs.length === 0) throw new Error('repairs 必须是非空数组');
  const findings = new Map(audit.findings.map((finding) => [finding.finding_id, finding]));
  const suggestions = new Map(audit.suggested_repairs.map((repair) => [`${repair.finding_id}\u0000${String(repair.cell).toUpperCase()}`, repair]));
  const seen = new Set();
  const repairs = payload.repairs.map((entry, index) => {
    const cell = String(entry?.cell ?? '').toUpperCase();
    if (!/^[A-F][1-9]\d*$/.test(cell)) throw new Error(`repairs[${index}].cell 必须是 A:F 单元格地址`);
    if (seen.has(cell)) throw new Error(`修复计划包含重复单元格: ${cell}`);
    seen.add(cell);
    const before = normalizeCellValue(entry.before);
    const after = normalizeCellValue(entry.after);
    const kind = normalize(entry.kind);
    const reason = normalize(entry.reason);
    const findingId = normalize(entry.finding_id);
    if (before === after) throw new Error(`${cell} 的 before 与 after 相同`);
    if (/^[=+@]/.test(after) || /^-\d/.test(after)) throw new Error(`${cell} 的 after 疑似公式，日报模板只允许纯文本`);
    if (!kind) throw new Error(`${cell} 缺少 kind`);
    if (!REPAIR_KINDS.has(kind)) throw new Error(`${cell} 的 kind 不受支持: ${kind}`);
    if (!reason) throw new Error(`${cell} 缺少 reason`);
    if (!findingId || !findings.has(findingId)) throw new Error(`${cell} 缺少有效 finding_id`);
    const suggested = suggestions.get(`${findingId}\u0000${cell}`);
    if (kind === 'consistency-rewrite') {
      const finding = findings.get(findingId);
      const allowedCells = new Set((finding.cells ?? []).map((item) => String(item?.cell ?? '').toUpperCase()));
      if (finding.type !== 'stale_summary_after_task_deletion' || !allowedCells.has(cell)) throw new Error(`${cell} 的一致性改写不在该 finding 允许范围内`);
    } else if (!suggested || suggested.before !== before || suggested.after !== after || suggested.kind !== kind || suggested.reason !== reason) {
      throw new Error(`${cell} 与审计建议的精确修复不一致`);
    }
    return { cell, before, after, kind, reason, finding_id: findingId };
  });
  return { schema: REPAIR_SCHEMA, baseline_sha256: payload.baseline_sha256, current_sha256: payload.current_sha256, repairs };
}

function parseArgs(argv) {
  const options = {};
  const allowed = new Set(['--cdp', '--document-id', '--sheet', '--plan', '--audit', '--output-dir', '--result']);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === '--help' || key === '-h') return { help: true };
    if (!allowed.has(key)) throw new Error(`未知参数: ${key}`);
    const value = argv[++index];
    if (!value || value.startsWith('--')) throw new Error(`${key} 缺少值`);
    options[key.slice(2).replaceAll('-', '_')] = value;
  }
  for (const key of ['document_id', 'sheet', 'plan', 'audit', 'output_dir']) if (!options[key]) throw new Error(`缺少 --${key.replaceAll('_', '-')}`);
  return options;
}

async function exactVisible(page, selector, text) {
  const locator = page.locator(selector);
  const matches = [];
  for (let index = 0; index < Math.min(await locator.count(), 100); index += 1) {
    const candidate = locator.nth(index);
    if (await candidate.isVisible().catch(() => false) && normalize(await candidate.innerText().catch(() => '')) === text) matches.push(candidate);
  }
  return matches;
}

async function locateSheetTab(page, name) {
  for (const selector of ['[role="tab"]', '.sheet-tab', '[class*="sheet-tab"]', '[class*="sheetbar"] [class*="item"]', '[class*="tab-item"]']) {
    const matches = await exactVisible(page, selector, name);
    if (matches.length === 1) return matches[0];
    if (matches.length > 1) throw new Error(`工作表名称“${name}”匹配到多个可见 tab`);
  }
  const fallback = page.getByText(name, { exact: true });
  const matches = [];
  for (let index = 0; index < Math.min(await fallback.count(), 100); index += 1) {
    const candidate = fallback.nth(index);
    if (await candidate.isVisible().catch(() => false) && normalize(await candidate.innerText().catch(() => '')) === name) matches.push(candidate);
  }
  if (matches.length !== 1) throw new Error(`无法唯一定位工作表 tab“${name}”`);
  return matches[0];
}

export async function applyRepairs(options) {
  const [rawPlan, audit] = await Promise.all([
    readFile(resolve(options.plan), 'utf8').then(JSON.parse),
    readFile(resolve(options.audit), 'utf8').then(JSON.parse),
  ]);
  const plan = validateRepairPlan(rawPlan, audit);
  const outputDir = resolve(options.output_dir);
  await mkdir(outputDir, { recursive: true });
  const { chromium } = await loadPlaywrightRuntime();
  const browser = await chromium.connectOverCDP(resolveManagedCdpEndpoint(options.cdp).endpoint, { timeout: 15000 });
  let page;
  const attempted = [];
  try {
    const pages = browser.contexts().flatMap((context) => context.pages())
      .filter((candidate) => candidate.url().includes(`/sheet/${options.document_id}`));
    if (pages.length !== 1) throw new Error(`目标表格页面必须唯一，实际 ${pages.length}`);
    [page] = pages;
    page.setDefaultTimeout(15000);
    const tab = await locateSheetTab(page, normalize(options.sheet));
    await tab.click();
    const nameBox = page.locator('input.bar-label').filter({ visible: true }).first();
    const formula = page.locator('.formula-input').filter({ visible: true }).first();
    await nameBox.waitFor({ state: 'visible' });
    await formula.waitFor({ state: 'visible' });

    const select = async (cell) => {
      await nameBox.fill(cell);
      await nameBox.press('Enter');
      await page.waitForTimeout(60);
    };
    const read = async (cell) => {
      await select(cell);
      return normalize(await formula.evaluate((element) => element.innerText));
    };
    const write = async (cell, value) => {
      await select(cell);
      await formula.fill(value);
      await formula.press('Enter');
      await page.waitForTimeout(80);
    };

    const beforeValues = {};
    for (const repair of plan.repairs) beforeValues[repair.cell] = await read(repair.cell);
    const mismatches = plan.repairs.filter((repair) => beforeValues[repair.cell] !== repair.before)
      .map((repair) => ({ cell: repair.cell, expected: repair.before, actual: beforeValues[repair.cell] }));
    if (mismatches.length) throw new Error(`修复前值不匹配，未执行任何写入: ${JSON.stringify(mismatches)}`);

    try {
      for (const repair of plan.repairs) {
        const immediate = await read(repair.cell);
        if (immediate !== repair.before) throw new Error(`${repair.cell} 写入前即时值不匹配: ${JSON.stringify({ expected: repair.before, actual: immediate })}`);
        attempted.push(repair);
        await write(repair.cell, repair.after);
        const actual = await read(repair.cell);
        if (actual !== repair.after) throw new Error(`${repair.cell} 写后复读不一致: ${JSON.stringify({ expected: repair.after, actual })}`);
      }
    } catch (error) {
      const rollbackFailures = [];
      for (const repair of [...attempted].reverse()) {
        try {
          const current = await read(repair.cell);
          if (current === repair.before) continue;
          if (current !== repair.after) {
            rollbackFailures.push({ cell: repair.cell, expected_repair_value: repair.after, actual: current, error: '并发变化，拒绝覆盖回滚' });
            continue;
          }
          await write(repair.cell, repair.before);
          const actual = await read(repair.cell);
          if (actual !== repair.before) rollbackFailures.push({ cell: repair.cell, expected: repair.before, actual });
        } catch (rollbackError) {
          rollbackFailures.push({ cell: repair.cell, error: rollbackError.message });
        }
      }
      if (rollbackFailures.length) throw new Error(`${error.message}; 回滚失败: ${JSON.stringify(rollbackFailures)}`);
      throw new Error(`${error.message}; 已回滚本次尝试写入的单元格`);
    }

    const afterValues = {};
    for (const repair of plan.repairs) afterValues[repair.cell] = await read(repair.cell);
    const screenshotCells = [...new Set([plan.repairs[0].cell, plan.repairs.at(-1).cell])];
    const screenshots = [];
    for (const cell of screenshotCells) {
      await select(cell);
      const screenshot = resolve(outputDir, `repaired-${normalize(options.sheet)}-${cell}.png`);
      await page.screenshot({ path: screenshot });
      screenshots.push(screenshot);
    }
    const result = {
      status: 'PASS', schema: REPAIR_SCHEMA, document_id: options.document_id,
      sheet: normalize(options.sheet), baseline_sha256: plan.baseline_sha256, current_sha256: plan.current_sha256,
      repairs: plan.repairs, after_values: afterValues,
      screenshot: screenshots[0], screenshots,
    };
    if (options.result) {
      const target = resolve(options.result);
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, `${JSON.stringify(result, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
    }
    return result;
  } finally {
    await disconnectPlaywrightTransport(browser);
  }
}

function usage() {
  return 'node scripts/apply_daily_sheet_repairs.mjs --document-id <id> --sheet YYYY-MM-DD --audit <sheet-change-audit.json> --plan <plan.json> --output-dir <dir> [--result <result.json>]';
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  if (options.help) return process.stdout.write(`${usage()}\n`);
  process.stdout.write(`${JSON.stringify(await applyRepairs(options), null, 2)}\n`);
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (isMain) main().catch((error) => { process.stderr.write(`apply_daily_sheet_repairs: ${error.message}\n`); process.exit(1); });
