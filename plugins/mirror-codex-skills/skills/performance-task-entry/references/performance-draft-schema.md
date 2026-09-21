# 绩效草稿数据契约

阶段 A 必须生成一份 JSON 绩效草稿。草稿先写入当前任务的 `work/`，验证后由 `finalize_draft.py` 同时写入统一归档，并在当前任务的 `outputs/` 生成可直接查看的普通文件；不得用符号链接交付，也不得包含账号、密码、Cookie 或登录令牌。

```json
{
  "schema": "mirror-performance-draft-v3",
  "month": "2026-08",
  "source_policy": "weekly-primary-daily-secondary",
  "weekly_sources": [
    "/absolute/path/to/weekly-facts/20260803-20260809.json"
  ],
  "daily_sources": [
    "/absolute/path/to/daily-facts/2026-08-31.json"
  ],
  "work_days_basis": "按目标月份工作日和周报项目投入拟定，等待用户检查确认",
  "confirmed_work_days": 21,
  "entries": [
    {
      "task_type": "计划任务",
      "project": "TVA10A2R",
      "task_description": "杭研中屏需求与问题处理",
      "start_date": "2026-08-03",
      "end_date": "2026-08-30",
      "work_days": 6,
      "work_results": [
        "修复删除设备后无法连接闪联问题。",
        "完成播放响应时间基线验证。",
        "定位 HLS 新分片加载为主要耗时。",
        "修复移动高清影视快捷键直达功能。",
        "完成项目重新送测。"
      ],
      "evidence": [
        "weekly:20260803-20260809",
        "daily:2026-08-31"
      ]
    }
  ]
}
```

## 字段含义

- `month`：系统中的“工时计算月份”，格式 `YYYY-MM`。
- `source_policy`：固定为 `weekly-primary-daily-secondary`。
- `weekly_sources`：覆盖目标月份的周报或 weekly facts 绝对路径，必须至少一项。没有周报时停止，不用日报独立生成绩效草稿。
- `daily_sources`：用于补充或核对的日报或 daily facts 绝对路径，可以为空数组。
- `work_days_basis`：总工时与各项目工时的来源或拟定依据；即使用户尚未单独给出总工时，也要写清建议口径，交由用户在审阅阶段统一确认。
- `confirmed_work_days`：阶段 A 中填写供用户检查的当月总工时口径，必须等于各 `work_days` 之和；用户对整份草稿二次确认后，该数值才获得填写授权。
- `task_type`：`计划任务` 或 `临时任务`。
- `project`：系统下拉框中的精确项目代码。
- `task_description`：项目本月任务概括。
- `start_date`、`end_date`：目标月份内的实际工作区间，格式 `YYYY-MM-DD`。
- `work_results`：非空字符串数组。一个元素只写一个独立成果，写入系统时按数组顺序转成连续编号和换行。
- `evidence`：该项目成果的具体周报/日报追溯依据，不写入绩效系统；周报证据必须存在，日报证据只能作为辅助。

如果周报来自 Codex 任务而不是现成文件，先把与目标月份直接相关的周报证据整理到当前任务 `work/` 下的本地 JSON 或 Markdown 文件，再把该绝对路径写入 `weekly_sources`。不要复制无关对话或敏感信息。

## 工作成果原子化规则

- 一项成果只能有一个主动作和一个明确对象或结果，例如“修复删除设备后无法连接闪联问题”。
- 同一条中出现多个独立的完成、修复、定位、验证、适配、交付事项时，必须拆成多个数组元素。
- 元素内禁止使用分号、顿号、换行、预编号或多个完整句子，也禁止用“并完成”“并修复”“并定位”等连接新的成果。
- 不为凑固定数量合并或拆碎成果；有五项就生成 1～5，有八项就生成 1～8。
- 未完成或等待外部反馈的事项不写入成果；已完成的阶段性结论可以作为单独成果准确表达。

## 校验

```bash
python3 scripts/validate_draft.py /absolute/path/to/draft.json
```

只有校验输出 `PASS` 才能归档并交给用户检查。校验会确认所有列出的来源文件实际存在。校验通过只证明结构和来源层级一致，不代表用户已经确认；收到用户第二次确认前不得进入网页写入阶段。
