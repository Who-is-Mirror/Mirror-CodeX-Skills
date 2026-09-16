# 绩效草稿存档规则

## 统一存档位置

默认情况下，本技能从安装位置推导 Codex 目录，并把绩效草稿统一放在：

```text
<Codex 目录>/artifacts/performance-drafts/<YYYY-MM>/
```

需要使用共享存档位置时，仅在当前命令环境设置 `PERFORMANCE_DRAFT_ARCHIVE_ROOT`。来源主归档可通过 `PERFORMANCE_SOURCE_ROOT` 覆盖；不设置时为 `<Codex 目录>/artifacts/akbs-member-ops`。旧归档始终是只读回退，默认是 `<Codex 目录>/artifacts/android-knowledge-intake`，仅在本次环境以 `PERFORMANCE_LEGACY_SOURCE_ROOT` 覆盖。`discover_sources.py` 只扫描这两个根的 `submitted` 包，优先主根，并按当前成员、报告日期/周区间及 replacement 链选择有效报告。`<Codex 目录>` 可用 `PERFORMANCE_CODEX_HOME` 显式覆盖，否则由本技能安装位置自动推导。

成员身份优先取 `PERFORMANCE_MEMBER_ALIAS`；否则只读取权威 `<Codex 目录>/akbs-member-ops.toml` 中由 `CODEX_REPORT_PROFILE`、`CODEX_WORK_REPORT_PROFILE` 或 `default_profile` 选择的 profile。若均缺失，脚本仅可在目标月份 `submitted` 归档恰好出现一个 alias 时推断；多个 alias 或无法推断时必须停止，不能拼接多人来源。

来源文件优先使用 manifest `files.display` 中的 `report_view.json`，没有才使用 `report_path` Markdown。有效 submitted 周报是主来源；weekly facts 只作补充，或在没有有效周报时回退；日报只作辅助。weekly facts 本身没有通用成员字段：默认的当前用户 Codex 两个归档根可保留历史无成员 facts；任一 `PERFORMANCE_*_SOURCE_ROOT` 自定义/共享根只能读取根 JSON `member_alias` 安全且等于当前成员的 facts。脚本用 `weekly_fact_omitted_count` 和 `weekly_fact_warnings` 报告被拒绝的 facts，绝不跨成员补充。替换链按日报日期或周报 `week_range` 身份隔离，不能因复用 run id 跨报告删除来源。整理成果时排除 `tomorrow_plan`、`next_week_plan`、`remaining`、未完成/处理中/待验证/阻塞状态和任何未来计划。

每次生成使用同一个时间戳，避免覆盖用户已经检查过的版本：

```text
performance-draft-<YYYYMMDD-HHMMSS>.json
performance-review-<YYYYMMDD-HHMMSS>.md
fill-result-<YYYYMMDD-HHMMSS>.json
```

- `performance-draft`：完整结构化绩效草稿，含周报主来源、日报辅助来源和项目证据。
- `performance-review`：面向用户检查的可读版本，逐项目列出任务类型、描述、日期、工时和成果。
- `fill-result`：阶段 B 完成后记录草稿绝对路径、草稿 SHA-256、实际保存项目、状态、截图路径、跳过项和错误；不得包含凭据或登录态。

截图属于当次任务的用户交付物，保存在当次任务 `outputs/` 中；`fill-result` 只记录其绝对路径。

阶段 A 只能用 `scripts/finalize_draft.py` 归档。审阅文档必须由脚本生成并包含项目汇总、逐项目完整填写内容、每项独立连续编号的工作成果、证据、工时依据、检查提示和 SHA-256；不生成“审阅说明”及其附录。最终回复必须直接给出该 Markdown 文件的可点击链接，不能只在消息里写摘要。

## 审核绑定

阶段 A 输出时计算 JSON 文件 SHA-256，并同时报告草稿路径和哈希。用户检查后进入阶段 B 时：

1. 使用用户确认的精确草稿文件。
2. 重新计算 SHA-256，必须与阶段 A 报告一致。
3. 不一致时停止，让用户确认要采用哪个版本。
4. 不覆盖旧草稿；重新生成时写新时间戳版本。

不维护旧月报或旧测试文件的固定路径，也不把旧绩效草稿当作事实来源。事实来源只取统一周报/日报归档、用户明确提供的文件或已实际读取的 Codex 任务。
