# 独立后台 Edge 与绩效系统流程

## 运行时目标与可移植配置

- 系统：默认 `http://192.168.100.235:8080/`
- 页面路径：`项目信息 → 任务填报`
- Edge：自动在 x86 Program Files、Program Files 和当前 Windows 用户的本地安装位置查找。
- 专用用户目录：`<Windows 用户目录>\AppData\Local\Codex\EdgeBackgroundProfile`
- CDP 会话：由相邻安装的 `edge-cdp-session` 技能管理；默认让 Edge 以 `--remote-debugging-port=0` 自动分配，并返回已经过进程、监听和 endpoint 校验的实际地址
- Playwright 会话名：`edge-performance`

`runtime_paths.py` 默认根据技能安装目录推导 Codex 目录和 Windows 用户目录。在非标准机器或共享归档场景中，可只为当前命令设置以下显式覆盖；`doctor.py` 会显示解析后的非敏感值：

| 变量 | 用途 |
|---|---|
| `PERFORMANCE_CODEX_HOME` | Codex 目录；用于派生默认来源和草稿归档 |
| `PERFORMANCE_SOURCE_ROOT` | 统一周报/日报来源归档 |
| `PERFORMANCE_LEGACY_SOURCE_ROOT` | 只读旧 AKBS 来源回退归档；默认 `artifacts/android-knowledge-intake` |
| `PERFORMANCE_MEMBER_ALIAS` | 显式锁定本次来源读取的 AKBS 成员 alias；共享归档无法安全推断身份时必填 |
| `PERFORMANCE_DRAFT_ARCHIVE_ROOT` | 绩效草稿和填写结果归档 |
| `PERFORMANCE_WINDOWS_PROFILE` | Windows 用户目录，如 `C:\Users\<name>` |
| `PERFORMANCE_EDGE_EXECUTABLE` / `PERFORMANCE_EDGE_PROFILE` | Edge 可执行文件或专用用户目录 |
| `PERFORMANCE_SYSTEM_ORIGIN` / `PERFORMANCE_CDP_PORT` | 系统 origin；或仅用于受控排障的显式固定 CDP 端口覆盖 |
| `PERFORMANCE_CREDENTIAL_VAULT` | 本机加密凭据目录；默认仍是当前 Linux 用户的 `~/.codex/...` |

自定义或共享 `PERFORMANCE_SOURCE_ROOT` / `PERFORMANCE_LEGACY_SOURCE_ROOT` 的 `weekly-facts` 只能在 JSON 根对象含有安全且匹配当前成员的 `member_alias` 时使用；没有该字段的历史 facts 仅允许来自默认当前用户 Codex 归档根。`discover_sources.py` 会在 `weekly_fact_omitted_count` 和 `weekly_fact_warnings` 中明确报告拒绝项。

系统地址变化时，先只读检查并在本次任务中覆盖，不直接改技能。

Edge 设置页中的浏览器调试开关不参与本流程，关闭时也能正常执行。不要读取、修改或提示用户开启该开关。

## 两道强制前置门禁

浏览器探测、来源读取和草稿生成之前只能先运行 `python3 scripts/doctor.py`。脚本内以及调用流程都必须保持如下顺序：

1. 第一步检查 Node.js、npm、`playwright-cli`、Python 3 和 `cryptography`。缺少任何一项时返回退出码 `2`，第二步标记为 `not_run`，且不检查凭据、不诊断后台 Edge。一次性列全缺项并询问是否允许协助自动安装。用户最初提出绩效填报不等于授权安装软件。
2. 只有五项环境依赖全部可用，才执行第二步：解密验证账号密码凭据库。缺失、损坏或无法解密时返回退出码 `3`、`ready_for_draft: false`，要求用户直接在对话中提供账号和密码；不能用浏览器 token、历史对话或密码库代替。
3. 收到账号密码后由 Codex 交互式保存并重新运行完整 `doctor.py`。只有退出码 `0` 且 `ready_for_draft: true` 时，才允许读取 `$playwright-cli`、探测 Edge、读取来源和生成草稿。

依赖安装规则：

- 只有用户在缺失提示之后再次明确同意，才能开始安装。
- 根据当前操作系统和可用包管理器依次安装缺失项：Node.js/npm、Python 3、Python 包 `cryptography`、`playwright-cli`；不得使用来源不明的安装脚本。
- Python 包优先使用操作系统包管理器提供的 `python3-cryptography`；环境适合使用 pip 时才执行 `python3 -m pip install cryptography`。pip 不存在、环境禁止全局 pip 或需要管理员操作时，给出准确指引并停止。
- `node --version` 和 `npm --version` 验证成功后，若仍缺少 `playwright-cli`，执行 `npm install -g @playwright/cli@latest`。
- 安装后从 `doctor.py` 重新开始；任何安装或验证失败都报告具体错误并停止。

## 两道门禁通过后的浏览器检查

1. 只运行 `python3 scripts/probe_browser.py`。`ensure_background_edge.py` 是业务侧兼容适配器：它把本技能原有专用 profile 交给 `edge-cdp-session/scripts/ensure_session.py`，由共享技能解析或启动一个独立、后台、可持久复用的 Edge 实例。共享技能使用的关键参数为：

```text
--remote-debugging-port=0
--remote-allow-origins=*
--user-data-dir=<解析后的专用 Edge 用户目录>
--headless=new
--disable-gpu
--no-first-run
--no-default-browser-check
about:blank
```

2. 以下启动、等待和归属校验均由 `edge-cdp-session` 独占实现；本技能不得复制、弱化或绕过。共享技能启动时使用独立进程和隐藏窗口；按单调时钟设置 30 秒启动等待期限。自动端口模式优先在 WSL 本地轮询专用目录的完整 `DevToolsActivePort`；直接挂载路径不可见或内容不完整时，使用轻量 PowerShell 文件读取，让 Windows 解析 Codex 桌面包的 LocalCache 重定向。状态文件尚未出现时不反复执行较重的 Windows 进程和监听查询；文件出现后才执行完整归属校验。截止时间到达时仍做最后一次验证；正在执行的 Windows 查询允许正常结束，所以最终实际耗时可能略高于 30 秒，失败时会输出实际耗时和最后停留阶段：

   - 保留 Windows Edge 进程的 PID、父 PID 和命令行，先按规范化后的完整 `--user-data-dir` 查找所有根浏览器进程，不按调试端口提前筛选。带 `--type` 的 renderer/utility 子进程不作为根进程。只有恰好一个根进程时才检查它的调试端口是否为本次模式要求的 `0` 或显式覆盖值。其他模式、缺失调试端口、多个根进程或不可读元数据均不允许复用。
   - `Get-NetTCPConnection` 保留该端口所有地址的 `LocalAddress`、`LocalPort`、`OwningProcess`。实际连接固定拨向 `127.0.0.1`，因此 `127.0.0.1` 和 IPv4 通配地址 `0.0.0.0` 的所有 owner 都参与判定，owner 集合必须恰好等于该唯一根 PID。仅有 `::1` 不能证明 IPv4 归属；IPv6 通配或 IPv4 映射地址缺少双栈信息、记录不完整、查询失败或有竞争 owner 时，在 CDP 探测前失败。
   - 自动模式只读取这一专用目录逻辑路径下的 `DevToolsActivePort`；WSL 直接视图与 PowerShell 重定向视图共用同一严格两行格式校验。监听归属通过后，再验证 `/json/version` 返回的本地端口和 WebSocket 路径与文件一致；旧文件本身不能授权连接。显式固定端口覆盖不读取该文件，但仍要求同样的进程、IPv4 监听归属和本地 WebSocket endpoint 验证。
   - 已有任何专用根进程而端点无法验证时，在清理状态文件及启动前失败，包括自动/固定模式切换和多个根进程。自动模式只有在该目录没有根进程时才删除这一目录的旧状态文件。显式固定端口没有专用根进程时，采取保守启动策略：该端口所有地址必须均无 TCP 监听，元数据不可读或任一地址已有监听则失败。

   WSL 正常情况下直接执行 Windows PowerShell；若当前会话缺少 `WSLInterop` 的 binfmt 注册并返回 `Exec format error`，脚本自动通过 `/init` 转发同一条命令重试。不要探测或依赖默认 Edge 用户目录的调试状态文件。
3. `probe_browser.py` 在临时目录中设置 WSL 临时变量，使用当前 `webSocketDebuggerUrl` 做一次只读连接并立即 `detach`；临时 Playwright 日志自动删除。此时不新建页面、不登录、不填写。
4. 独立实例由命令行调试参数直接开放 CDP 端口，不需要浏览器内确认。不得改用日常 Edge 的调试开关。
5. 不关闭、复用或批量终止用户日常 Edge。若为了受控排障显式设置了 `PERFORMANCE_CDP_PORT`，该固定端口只在上述专用进程归属验证成功时复用；被其他浏览器或服务占用时硬失败。端口被非 CDP 服务占用、专用目录锁定或启动失败时，只做精确诊断并停止；不得执行 `taskkill /IM msedge.exe` 一类广泛终止命令。

## 阶段 B 连接

1. 用户确认绩效草稿后，只运行 `scripts/fill_draft.py`；不要手工组合 `attach`、`tab-select`、`snapshot`、`run-code` 或临时填写脚本。
2. 脚本重新检查或启动专用 Edge，并把已验证的实际本地 CDP endpoint 传给 `ensure_login.js` 验证现有 token；失效才从授权凭据库解密并登录。凭据不进入 Playwright 命令、runner、参数或输出。
3. 脚本通过 `context.pages()` 和绩效系统 origin 查找或新建专用页面，不使用会随标签变化的数字序号，也不操作其他 origin 的页面。
4. Playwright 在临时目录运行，完成后无论成功或失败都 `detach` 并删除临时 runner、snapshot 和 console 日志；专用 Edge 保持运行。
5. 凭据库缺失、损坏或登录被拒绝时停止并重新向用户索取；不要读取浏览器密码库、历史对话或草稿文件。

## 页面操作

以下均由 `fill_draft.py` 实现，调用者不得另写低层选择器覆盖：

1. 打开系统，按安全登录检查确认当前会话有效并显示当前用户。
2. 通过可见文本精确点击“项目信息”，再点击“任务填报”；确认 URL 路由为 `#/task`。
3. 在“工时计算月份”筛选框选择目标月份，读取表格所有行。
4. 对每个草稿项目检查是否已有同月记录。与确认草稿完全一致的记录作为可恢复检查点；存在差异的记录在任何新增前阻塞。
5. 点击精确名称为“添加”的按钮，只操作当前可见且 `aria-label=添加` 的对话框。
6. 在当前可见对话框中逐个读取 `.el-form-item` 的直属标签，去掉首尾空白和可选的中文/英文冒号后做完全相等比较；不要把带对话框根路径的标签 locator 作为 `filter({has: ...})` 的子 locator，不点击隐藏 input，不依赖固定 `nth()`：
   - 任务类型
   - 项目
   - 项目归属（系统自动带出，只读校验）
   - 任务描述
   - 开始时间
   - 结束时间
   - 工时（/天）
   - 工作成果
   - 工时计算月份
7. 项目下拉使用完全相等的文本定位。选择后回读输入值，必须等于草稿项目代码。
8. 日期、数字输入框如果普通 `fill()` 不适配，先聚焦，`Control+A` 后键入；随后触发 `Tab`/`change` 并回读值。不要直接改 Vue 内部状态来绕过校验。
9. 先确认 `work_results` 中每个元素只有一个独立成果，再按数组顺序转成连续的 `1. ...\n2. ...\n3. ...` 后填入工作成果；不得在一个编号中合并多项成果。
10. 点击“确定”前构造表单快照，与草稿逐字段严格比较。全部一致才保存。
11. 出现“添加成功”后只点击该提示框的“确定”关闭提示；这不是“提交审核”。
12. 在列表中定位新行并严格核对：项目、月份、`待提交`、工时、开始日期、结束日期、任务描述、工作成果。
13. 单条在保存前发生 DOM 超时或下拉未展开时，关闭未保存的添加框后最多自动重试三次；如果保存动作后的提示丢失，先在列表查找完全匹配记录，再决定是否重试，避免重复新增。

## 截图与收尾

- 视口建议 `1440 × 900`。
- 目标月份筛选后，优先截包含新增行的表格区域；需要多张时按表格上下段截图。
- 不使用 `fullPage` 作为首选，动态表格可能导致截图超时。
- 截图后重新读取新增行，确认状态仍为“待提交”。
- `fill_draft.py` 在 `finally` 中执行 `detach`；保留专用后台 Edge 和本次绩效标签页供复查，不关闭用户日常 Edge 或其标签页。
