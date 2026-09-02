# Mirror-CodeX-Skills

Mirror 的 Codex Skills 仓库。

## Skills

### android-daily-sheet-roundtrip

Android 日报双阶段工作流：

1. 经用户授权后扫描 Codex 会话，生成可编辑的企业微信在线表格草稿。
2. 用户确认表格修改完成后，从表格反推日报事实并准备待提交报告。

表格渲染使用后台 Edge CDP 与 Playwright，同时写入 `text/plain` 和
`text/html`，一次粘贴数据与格式，并批量设置行高、列宽。流程会执行模型级
校验、恢复原剪贴板并保存截图证据。

技能目录：[`skills/android-daily-sheet-roundtrip`](skills/android-daily-sheet-roundtrip)

## 安装

将需要的技能目录复制到 `$CODEX_HOME/skills/`：

```bash
cp -R skills/android-daily-sheet-roundtrip "$CODEX_HOME/skills/"
```

该技能依赖已安装的 `android-framework-codex-suite` 插件、
`android-daily-customer-guard` 技能，以及已开启 CDP 的用户自有 Edge 会话。

## 校验

```bash
node --test skills/android-daily-sheet-roundtrip/tests/*.test.mjs
python3 -m unittest discover \
  -s skills/android-daily-sheet-roundtrip/tests \
  -p 'test_*.py'
```
