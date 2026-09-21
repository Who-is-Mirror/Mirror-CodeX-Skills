#!/usr/bin/env python3
"""Fill one confirmed draft through a reusable Playwright runner and record the outcome."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from runtime_paths import resolve_runtime_paths
from validate_draft import validate


SKILL_DIR = Path(__file__).resolve().parent.parent
RUNTIME_PATHS = resolve_runtime_paths()
ARCHIVE_ROOT = RUNTIME_PATHS.draft_archive_root
RESULT_MARKER = "PERFORMANCE_RESULT:"
SYSTEM_ORIGIN = RUNTIME_PATHS.system_origin


def command(args: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update({"TMPDIR": "/tmp", "TEMP": "/tmp", "TMP": "/tmp"})
    return subprocess.run(
        args, cwd=cwd, env=environment, capture_output=True, text=True,
        timeout=timeout, check=False,
    )


def command_error(run: subprocess.CompletedProcess[str], fallback: str) -> str:
    details = "\n".join(part.strip() for part in (run.stdout, run.stderr) if part.strip())
    return details or fallback


def runner_source(draft: dict, screenshot: Path) -> str:
    payload = json.dumps(draft, ensure_ascii=False, separators=(",", ":"))
    screenshot_json = json.dumps(str(screenshot))
    origin_json = json.dumps(SYSTEM_ORIGIN)
    return f"""async page => {{
  const draft = {payload};
  const origin = {origin_json};
  const screenshotPath = {screenshot_json};
  const result = {{status:'blocked', month:draft.month, saved:[], existing:[], errors:[], screenshot:screenshotPath}};
  const normalized = value => String(value ?? '').replaceAll(String.fromCharCode(92) + 'n', ' ').replace(/\\s+/g, ' ').trim();
  const exact = value => new RegExp('^\\\\s*' + String(value).replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&') + '\\\\s*$');
  const pause = ms => target.waitForTimeout(ms);
  const context = page.context();
  const pages = context.pages();
  let target = pages.find(item => item.url().startsWith(origin));
  if (!target) target = await context.newPage();
  await target.bringToFront();

  const rows = () => target.locator('.el-table__body-wrapper tbody tr');
  const findRow = async project => {{
    const all = rows();
    for (let index = 0; index < await all.count(); index++) {{
      const text = normalized(await all.nth(index).innerText().catch(() => ''));
      if (text.includes(project) && text.includes(draft.month)) return {{locator: all.nth(index), text}};
    }}
    return null;
  }};
  const rowMatches = (entry, text) => {{
    const resultsText = entry.work_results.map((value,index) => `${{index + 1}}. ${{value}}`).join(' ');
    const required = [entry.task_type, entry.project, draft.month, '待提交', entry.start_date, entry.end_date,
      entry.task_description, resultsText];
    return required.every(value => text.includes(normalized(value)))
      && new RegExp(`(^| )${{String(entry.work_days).replace('.', '\\\\.')}}( |$)`).test(text);
  }};
  const closeDialog = async () => {{
    const dialogs = target.locator('.el-dialog:visible');
    if (!await dialogs.count()) return;
    const current = dialogs.last();
    const cancel = current.getByRole('button', {{name:/取\\s*消/}});
    if (await cancel.count()) await cancel.click().catch(() => {{}});
    else await current.locator('.el-dialog__headerbtn').click().catch(() => {{}});
  }};
  const formItem = async (dialog, label) => {{
    const expected = normalized(label).replace(/[：:]$/, '');
    const items = dialog.locator('.el-form-item');
    for (let index = 0; index < await items.count(); index++) {{
      const item = items.nth(index);
      const labelNode = item.locator('.el-form-item__label').first();
      const actual = normalized(await labelNode.innerText().catch(() => '')).replace(/[：:]$/, '');
      if (actual === expected) return item;
    }}
    throw new Error(`表单字段不存在: ${{label}}`);
  }};
  const inputValue = async locator => normalized(await locator.inputValue());
  const setText = async (locator, value) => {{
    await locator.scrollIntoViewIfNeeded();
    try {{
      await locator.fill(String(value));
    }} catch (_) {{
      await locator.click();
      await locator.press('Control+A');
      await locator.pressSequentially(String(value));
    }}
    await locator.press('Tab').catch(() => {{}});
  }};
  const setSelect = async (input, value) => {{
    await input.scrollIntoViewIfNeeded();
    await input.click();
    let option = target.locator('.el-select-dropdown:visible .el-select-dropdown__item:visible').filter({{hasText:exact(value)}});
    if (!await option.count()) {{
      if (!await input.getAttribute('readonly')) await input.fill(value).catch(() => {{}});
      await pause(400);
      option = target.locator('.el-select-dropdown:visible .el-select-dropdown__item:visible').filter({{hasText:exact(value)}});
    }}
    if (!await option.count()) throw new Error(`下拉项不存在: ${{value}}`);
    await option.first().scrollIntoViewIfNeeded();
    await option.first().click();
    if (await inputValue(input) !== normalized(value)) throw new Error(`下拉回读不一致: ${{value}}`);
  }};
  const chooseMonthFilter = async () => {{
    const monthInput = target.getByRole('textbox', {{name:'工时计算月份'}}).first();
    if (!await monthInput.count()) return;
    if (await inputValue(monthInput) !== draft.month) {{
      await monthInput.click();
      const desiredYear = Number(draft.month.slice(0,4));
      const monthNames = ['一月','二月','三月','四月','五月','六月','七月','八月','九月','十月','十一月','十二月'];
      const panel = target.locator('.el-picker-panel:visible').last();
      for (let guard = 0; guard < 20; guard++) {{
        const yearLabel = normalized(await panel.locator('.el-date-picker__header-label').first().innerText());
        const currentYear = Number(yearLabel.replace(/\\D/g,''));
        if (currentYear === desiredYear) break;
        const selector = currentYear > desiredYear
          ? '.el-date-picker__prev-btn.el-icon-d-arrow-left'
          : '.el-date-picker__next-btn.el-icon-d-arrow-right';
        await panel.locator(selector).click();
      }}
      const monthName = monthNames[Number(draft.month.slice(5)) - 1];
      const cell = panel.locator('.el-month-table td:visible').filter({{hasText:exact(monthName)}});
      if (!await cell.count()) throw new Error(`月份筛选器无法选择 ${{draft.month}}`);
      await cell.first().click();
      await pause(300);
      if (await inputValue(monthInput) !== draft.month) throw new Error(`月份筛选回读不一致: ${{draft.month}}`);
    }}
    const search = target.getByRole('button', {{name:'搜索', exact:true}});
    if (await search.count()) await search.click();
    await pause(500);
  }};

  try {{
    await target.goto(origin + '/#/task');
    await target.waitForLoadState('domcontentloaded');
    await target.getByRole('button', {{name:'添加', exact:true}}).waitFor({{timeout:15000}});
    await chooseMonthFilter();

    const conflicts = [];
    for (const entry of draft.entries) {{
      const row = await findRow(entry.project);
      if (row && rowMatches(entry, row.text)) {{
        result.existing.push({{project:entry.project, status:'待提交', matches_confirmed_draft:true}});
        result.saved.push({{project:entry.project, work_days:entry.work_days, status:'待提交', recovered:true}});
      }} else if (row) {{
        conflicts.push({{project:entry.project, row:row.text}});
      }}
    }}
    if (conflicts.length) {{
      result.existing.push(...conflicts);
      result.status = 'blocked_existing_conflict';
      result.errors.push('目标月份已有同项目但内容不同的记录；未修改、未删除冲突记录');
    }} else {{
      for (const entry of draft.entries) {{
        if (result.saved.some(item => item.project === entry.project)) continue;
        let completed = false;
        let lastError = '';
        for (let attempt = 1; attempt <= 3 && !completed; attempt++) {{
          try {{
            const appeared = await findRow(entry.project);
            if (appeared) {{
              if (!rowMatches(entry, appeared.text)) throw new Error('恢复检查发现同项目记录与确认草稿不一致');
              result.saved.push({{project:entry.project, work_days:entry.work_days, status:'待提交', recovered:true}});
              completed = true;
              break;
            }}
            await closeDialog();
            await target.getByRole('button', {{name:'添加', exact:true}}).click();
            const dialog = target.locator('.el-dialog:visible').last();
            await dialog.waitFor({{timeout:10000}});
            const dialogTitle = normalized(await dialog.locator('.el-dialog__title').innerText().catch(() => ''));
            if (dialogTitle !== '添加') throw new Error(`当前对话框不是添加: ${{dialogTitle || '无标题'}}`);
            const radio = dialog.locator('.el-radio').filter({{hasText:exact(entry.task_type)}});
            if (!await radio.count()) throw new Error('任务类型控件不存在');
            await radio.first().click();

            const projectInput = (await formItem(dialog, '项目')).locator('.el-select input:visible').first();
            await setSelect(projectInput, entry.project);
            const ownershipItem = await formItem(dialog, '项目归属');
            const ownershipInput = ownershipItem.locator('input:visible').first();
            const ownershipText = normalized(
              await ownershipInput.count()
                ? await ownershipInput.inputValue()
                : await ownershipItem.innerText()
            );
            if (!ownershipText || ownershipText === '项目归属') throw new Error('项目归属未自动带出');
            const description = (await formItem(dialog, '任务描述')).locator('textarea:visible').first();
            const start = (await formItem(dialog, '开始时间')).locator('input:visible').first();
            const end = (await formItem(dialog, '结束时间')).locator('input:visible').first();
            const daysItem = await formItem(dialog, '工时（/天）');
            const days = daysItem.locator('input:visible').first();
            const workResults = (await formItem(dialog, '工作成果')).locator('textarea:visible').first();
            const month = (await formItem(dialog, '工时计算月份')).locator('input:visible').first();
            await setText(description, entry.task_description);
            await setText(start, entry.start_date);
            await setText(end, entry.end_date);
            await setText(days, entry.work_days);
            const resultsText = entry.work_results.map((value,index) => `${{index + 1}}. ${{value}}`).join('\\n');
            await setText(workResults, resultsText);
            await setSelect(month, draft.month);

            const actual = {{
              task_type: await radio.first().locator('input').isChecked(),
              project: await inputValue(projectInput),
              task_description: await inputValue(description),
              start_date: await inputValue(start),
              end_date: await inputValue(end),
              work_days: await inputValue(days),
              work_results: await inputValue(workResults),
              month: await inputValue(month),
            }};
            const expected = {{
              task_type:true, project:normalized(entry.project), task_description:normalized(entry.task_description),
              start_date:entry.start_date, end_date:entry.end_date, work_days:normalized(entry.work_days),
              work_results:normalized(resultsText), month:draft.month,
            }};
            for (const key of Object.keys(expected)) {{
              if (normalized(actual[key]) !== normalized(expected[key])) throw new Error(`表单回读不一致 ${{key}}`);
            }}
            await dialog.getByRole('button', {{name:/确\\s*定/}}).click();
            const notice = target.locator('.el-message-box:visible').filter({{hasText:'添加成功'}});
            await notice.waitFor({{timeout:10000}});
            await notice.getByRole('button', {{name:'确定', exact:true}}).click();
            await pause(500);
            const savedRow = await findRow(entry.project);
            if (!savedRow) throw new Error('保存后列表未找到记录');
            if (!rowMatches(entry, savedRow.text)) throw new Error('保存后列表与确认草稿不一致');
            result.saved.push({{project:entry.project, work_days:entry.work_days, status:'待提交'}});
            completed = true;
          }} catch (error) {{
            lastError = `第${{attempt}}次: ${{error.message}}`;
            const appeared = await findRow(entry.project).catch(() => null);
            if (appeared) {{
              if (rowMatches(entry, appeared.text)) {{
                result.saved.push({{project:entry.project, work_days:entry.work_days, status:'待提交', recovered:true}});
                completed = true;
              }} else {{
                lastError = `第${{attempt}}次: 保存后发现同项目记录与确认草稿不一致`;
              }}
            }} else {{
              await closeDialog();
              await pause(300);
            }}
          }}
        }}
        if (!completed) {{
          result.errors.push(`${{entry.project}}: ${{lastError}}`);
          break;
        }}
      }}
      result.status = result.saved.length === draft.entries.length ? 'complete' : 'blocked';
    }}
    await chooseMonthFilter().catch(() => {{}});
    try {{
      const table = target.locator('.el-table').last();
      if (await table.count()) await table.screenshot({{path:screenshotPath}});
      else await target.screenshot({{path:screenshotPath}});
    }} catch (screenshotError) {{
      try {{
        await target.screenshot({{path:screenshotPath}});
      }} catch (_) {{
        result.errors.push(`截图失败: ${{screenshotError.message}}`);
        if (result.status === 'complete') result.status = 'blocked';
      }}
    }}
  }} catch (error) {{
    result.errors.push(error.message);
  }}
  const encoded = await target.evaluate(value => {{
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary);
  }}, result);
  return '{RESULT_MARKER}' + encoded;
}}"""


def parse_result(output: str) -> dict:
    match = re.search(re.escape(RESULT_MARKER) + r"([A-Za-z0-9+/=]+)", output)
    if not match:
        raise RuntimeError("Playwright runner did not return a structured result")
    return json.loads(base64.b64decode(match.group(1)).decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("draft", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, default=ARCHIVE_ROOT)
    args = parser.parse_args()

    draft_path = args.draft.resolve()
    draft_bytes = draft_path.read_bytes()
    digest = hashlib.sha256(draft_bytes).hexdigest()
    if digest != args.expected_sha256.lower():
        raise ValueError("draft SHA-256 does not match the user-confirmed version")
    draft = json.loads(draft_bytes)
    validation = validate(draft)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    screenshot = (args.output_dir / f"performance-pending-{validation['month']}-{timestamp}.png").resolve()
    result_dir = (args.archive_root / validation["month"]).resolve()
    result_dir.mkdir(parents=True, exist_ok=True)
    result_path = result_dir / f"fill-result-{timestamp}.json"

    edge = command(["python3", str(SKILL_DIR / "scripts" / "ensure_background_edge.py")], Path.cwd())
    if edge.returncode:
        raise RuntimeError(command_error(edge, "background Edge unavailable"))
    edge_result = json.loads(edge.stdout)
    endpoint = edge_result["webSocketDebuggerUrl"]
    cdp_http = edge_result["cdp_http_endpoint"]
    login = command(
        ["node", str(SKILL_DIR / "scripts" / "ensure_login.js"), "--cdp-endpoint", cdp_http],
        Path.cwd(),
        60,
    )
    if login.returncode:
        raise RuntimeError(command_error(login, "login failed"))

    with tempfile.TemporaryDirectory(prefix="performance-playwright-") as temporary:
        work = Path(temporary)
        runner = work / "fill-runner.js"
        runner.write_text(runner_source(draft, screenshot), encoding="utf-8")
        attached = command(["playwright-cli", "-s=edge-performance", "attach", f"--cdp={endpoint}"], work)
        if attached.returncode:
            raise RuntimeError(command_error(attached, "Playwright attach failed"))
        try:
            executed = command(
                ["playwright-cli", "-s=edge-performance", "run-code", f"--filename={runner}"],
                work,
                360,
            )
            if executed.returncode:
                raise RuntimeError(command_error(executed, "Playwright runner failed"))
            result = parse_result(executed.stdout)
        finally:
            command(["playwright-cli", "-s=edge-performance", "detach"], work)

    record = {
        "schema": "mirror-performance-fill-result-v1",
        "draft_path": str(draft_path),
        "draft_sha256": digest,
        **result,
    }
    result_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record["result_path"] = str(result_path)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "complete" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(2)
