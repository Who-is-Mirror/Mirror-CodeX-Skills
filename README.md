# Mirror-CodeX-Skills

Mirror 的 Codex Skills 仓库。

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

## 安装

将需要的技能目录复制到 `$CODEX_HOME/skills/`：

```bash
cp -R skills/android-daily-sheet-roundtrip "$CODEX_HOME/skills/"
cp -R skills/performance-task-entry "$CODEX_HOME/skills/"
cp -R skills/edge-cdp-session "$CODEX_HOME/skills/"
```

两个业务技能都依赖同版本的 `edge-cdp-session`。日报技能还依赖已安装的
`android-framework-codex-suite` 插件和 `android-daily-customer-guard` 技能。
无需开启日常 Edge 的浏览器调试设置，也不复用日常 Edge 会话。

`performance-task-entry` 需要 Node.js/npm、`playwright-cli`、Python 3、
Python 包 `cryptography`、Windows Edge，以及可调用 Windows PowerShell 的 WSL 环境。
账号密码由技能的本机加密凭据库管理，不包含在本仓库中。

## 校验

```bash
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
