# 绩效系统凭据库

用户明确要求持久保存账号密码后，使用本技能的本机加密凭据库。禁止把明文凭据写入 `SKILL.md`、脚本、草稿、日志、截图或命令参数。

## 位置与安全边界

- 默认凭据目录：当前 Linux 用户的 `~/.codex/secrets/performance-task-entry`
- 若需使用另一处本机凭据库，只能在当前命令环境显式设置 `PERFORMANCE_CREDENTIAL_VAULT`；不要把该路径或任何凭据写入草稿、输出或技能文件。
- 主密钥：`master.key`，权限 `0600`
- 加密凭据：`credential.enc.json`，权限 `0600`
- 目录权限：`0700`
- 加密：AES-256-GCM；明文只在存储进程或登录进程内存中短暂出现。

该实现主要防止明文搜索、日志泄漏和误复制；能够读取当前 Linux 用户全部文件的进程仍可同时取得主密钥和密文。不得把凭据目录复制到输出、提交到版本库或随草稿归档。

## 状态与登录

只读检查凭据库：

```bash
python3 scripts/credential_vault.py status
```

专用 Edge 已启动后，执行安全登录检查：

```bash
node scripts/ensure_login.js
```

`ensure_login.js` 先用现有 token 调用只读接口验证登录态；有效时不解密凭据。token 失效时才在进程内解密账号密码并登录，输出只包含 `existing_session` 或 `encrypted_vault`，不得输出表单值或 token。

## 新增、替换与清除

- 凭据库缺失或损坏时，直接请用户在对话中提供账号和密码；不要把终端命令作为正常流程交给用户执行。
- 用户明确要求保存或替换，或在本技能索取后同时提供账号和密码时（明确声明仅本次使用的除外），由 Codex 在带 TTY 的后台会话中启动 `credential_vault.py store --interactive`，再通过该会话的标准输入依次发送账号和密码。两项均由 `getpass` 禁止回显。不得放进命令参数、命令文本、脚本正文、环境变量、shell 历史或可回显日志。
- 保存完成后立即运行 `status` 验证，只报告 `ready` 状态和更新时间，不得复述账号或密码。自动保存确实失败时，先报告错误；只有无法在当前环境完成时，才把交互命令作为备用方案交给用户。
- 只有用户明确要求清除时才运行 `credential_vault.py delete`，并清除绩效系统 origin 的 `localStorage` 登录字段。删除后报告凭据不可恢复。
- `status` 返回缺失、损坏或解密失败时停止自动登录并向用户索取新凭据；不要从历史对话、浏览器密码库或其他文件补齐。
