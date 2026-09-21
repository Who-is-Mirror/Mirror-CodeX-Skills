# Mirror Codex Skills

Mirror 的公开 GitHub Codex Plugin Marketplace。Marketplace 名称为
`mirror-codex-marketplace`，显示名称为 **Mirror Codex Skills**；其中提供
`mirror-codex-skills` 插件。

## Skills

### edge-cdp-session

共享的浏览器连接层：启动或复用隔离的专用 Edge，默认通过
`--remote-debugging-port=0` 动态分配端口，并校验专用 profile、唯一根进程、
IPv4 监听 PID、`DevToolsActivePort` 和 WebSocket endpoint。它不执行任何业务页面操作。

技能目录：[`skills/edge-cdp-session`](skills/edge-cdp-session)

### android-daily-sheet-roundtrip

Android 日报双阶段工作流：

1. 经用户授权后扫描 Codex 会话，生成可编辑的企业微信在线表格草稿。
2. 用户确认表格修改完成后，从表格反推日报事实并准备待提交报告。

表格渲染通过 `edge-cdp-session` 获得独立后台 Edge 的已验证动态 CDP endpoint，再由 Playwright 同时写入 `text/plain` 和
`text/html`，一次粘贴数据与格式，并批量设置行高、列宽。流程会执行模型级
校验、恢复原剪贴板并保存截图证据。

技能目录：[`skills/android-daily-sheet-roundtrip`](skills/android-daily-sheet-roundtrip)

### performance-task-entry

月度绩效双阶段工作流：

1. 以周报为主、日报为辅生成结构化绩效草稿，交付 Markdown 和 JSON 供用户检查。
2. 用户再次确认后，通过专用后台 Edge 和 Playwright 将确认版本保存为绩效系统待提交记录；不会点击“提交审核”。

浏览器连接同样委托给 `edge-cdp-session`；共享技能通过 `--remote-debugging-port=0` 自动分配 CDP 端口，
校验专用用户目录、根进程、监听 PID 和 WebSocket endpoint 后才返回连接，
无需开启日常 Edge 的浏览器调试设置。

技能目录：[`skills/performance-task-entry`](skills/performance-task-entry)

## 插件包含内容

`mirror-codex-skills` 会一起安装当前仓库 `skills/` 下的全部技能：

- `android-daily-sheet-roundtrip`
- `edge-cdp-session`
- `performance-task-entry`

## 让 Codex 安装

把下面这段话直接发给 Codex：

> 请从 GitHub 仓库 `Who-is-Mirror/Mirror-CodeX-Skills` 的 `main` 分支注册
> Codex Plugin Marketplace，然后安装
> `mirror-codex-skills@mirror-codex-marketplace`。安装后检查 Marketplace 和插件列表，
> 确认插件已安装并启用；不要改用其他来源。

Codex 对应执行的首次安装命令是：

```bash
codex plugin marketplace add Who-is-Mirror/Mirror-CodeX-Skills --ref main --json
codex plugin add mirror-codex-skills@mirror-codex-marketplace --json
```

安装后可以在 Codex 插件商店中搜索 `Mirror Codex Skills` 或
`mirror-codex-skills`。这个仓库不会自动出现在所有账号的全局默认商店里；每台电脑
需要先注册一次上述 Marketplace 来源，之后才能在本机商店中搜索到。

## 检查是否安装成功

让 Codex 执行：

```bash
codex plugin marketplace list --json
codex plugin list --marketplace mirror-codex-marketplace --available --json
```

结果中应同时出现 Marketplace `mirror-codex-marketplace` 和已安装插件
`mirror-codex-skills`，插件版本应为 `0.2.0` 或更高稳定版本。

## 获取更新

`0.2.0` 起，三个技能都有同一个任务启动门禁。每个完整用户需求第一次使用任一
Mirror 技能时，Codex 会检查 GitHub `main` 的插件清单；只有远端稳定版本更高时，
才自动执行：

```bash
codex plugin marketplace upgrade mirror-codex-marketplace --json
codex plugin add mirror-codex-skills@mirror-codex-marketplace --json
```

更新器随后重新读取插件 inventory，并核对新版本缓存和启用状态。确认成功后它会
停止当前业务操作并要求退出、重启 Codex；重启后同一任务复用原任务 ID，再次门禁
通过才继续。检查或更新失败会保留失败状态，不会在每条命令前无限重试；修复网络或
Marketplace 状态后才显式重试。

这不是后台轮询，也不是单纯重启 Codex 就刷新 GitHub。只有实际开始使用本插件技能
的新任务才触发一次检查。仍在使用 `0.1.0` 的成员必须先手动执行上面两条命令一次，
获得带更新器的 `0.2.0`；旧版本无法自动安装自己尚未包含的更新器。自动更新异常时，
同样可用这两条命令手动恢复。

维护者发布新版本时，需要同步更新 `plugins/mirror-codex-skills/skills/`、提升
`plugins/mirror-codex-skills/.codex-plugin/plugin.json` 的版本号、完成校验并推送到
GitHub `main`。

## 运行依赖

两个业务技能都依赖同版本的 `edge-cdp-session`。日报技能还依赖已安装的
`android-framework-codex-suite` 插件和 `android-daily-customer-guard` 技能。
无需开启日常 Edge 的浏览器调试设置，也不复用日常 Edge 会话。

`performance-task-entry` 需要 Node.js/npm、`playwright-cli`、Python 3、
Python 包 `cryptography`、Windows Edge，以及可调用 Windows PowerShell 的 WSL 环境。
账号密码由技能的本机加密凭据库管理，不包含在本仓库中。

## 校验

```bash
python3 "$CODEX_HOME/skills/.system/plugin-creator/scripts/read_marketplace_name.py" \
  --marketplace-path .agents/plugins/marketplace.json
python3 "$CODEX_HOME/skills/.system/plugin-creator/scripts/validate_plugin.py" \
  plugins/mirror-codex-skills

diff -qr -x __pycache__ skills plugins/mirror-codex-skills/skills

python3 -m unittest discover \
  -s plugins/mirror-codex-skills/tests \
  -p 'test_*.py'

python3 -m unittest discover \
  -s skills/edge-cdp-session/tests \
  -p 'test_*.py'

node --test skills/android-daily-sheet-roundtrip/tests/*.test.mjs
python3 -m unittest discover \
  -s skills/android-daily-sheet-roundtrip/tests \
  -p 'test_*.py'

python3 -m unittest discover \
  -s skills/performance-task-entry/tests \
  -p 'test_*.py'
node --check skills/performance-task-entry/scripts/ensure_login.js
node skills/performance-task-entry/tests/test_ensure_login_endpoint.js
```
