# Claude Code → Codex 自动 Review（Hook 脚本包）

这套方案的目标是：Claude Code 在一次会话里改完代码后（Stop），自动调用 Codex CLI 做一次 review，且只 review「本次会话里 Claude 触碰过的文件」。

为了解决 Claude Code 在 Windows 上按原文 Bash 方案容易遇到的兼容性问题（bash/mktemp/python3/chmod 等），这里推荐使用纯 Python 版本（跨平台，不依赖 bash）。

---

## 前置条件

- 项目是 git 仓库（`git rev-parse --is-inside-work-tree` 能通过）
- 本机可用：`python`、`git`、`codex`（并且 `codex login` 已完成）
- Claude Code hooks 会把 hook 事件 JSON 通过 stdin 传给脚本

---

## 文件清单

### 1) 配置：`.claude/settings.json`（把下面片段合并进去）

每个人项目的 `.claude/settings.json` 往往已经有自己的配置（hooks / permissions 等），所以这里**不建议整文件覆盖**。
你只需要把下面两个 hook 片段按需追加到你现有的 `hooks.PostToolUse` 和 `hooks.Stop` 数组里即可（没有就创建）。

PostToolUse：追加到 `hooks.PostToolUse`：

```json
{
  "matcher": "Write|Edit",
  "hooks": [
    {
      "type": "command",
      "command": "python .claude/hooks/record_touched_files.py"
    }
  ]
}
```

Stop：追加到 `hooks.Stop`：

```json
{
  "hooks": [
    {
      "type": "command",
      "command": "python .claude/hooks/codex_review_touched_once.py",
      "timeout": 180
    }
  ]
}
```

说明：
- `PostToolUse`：仅对 `Write|Edit` 触发，记录本 session 触碰的文件列表
- `Stop`：把 touched files 的 diff 拼成 prompt，调用 `codex exec` 做 review

提示：如果你的环境只有 `python3`，把命令里的 `python` 改成 `python3` 即可。

---

### 2) PostToolUse：`.claude/hooks/record_touched_files.py`

作用：收到 `Write|Edit` 事件后，把本次 session 触碰到的文件记录到：

- `.claude/.codex_review/files_<session_id>.txt`

细节：
- 尽量保存为“项目内相对路径”（方便 `git diff -- <files>`）
- 会忽略项目外路径（例如 `..\\..\\something`）

---

### 3) Stop：`.claude/hooks/codex_review_touched_once.py`

作用：

- 同一 `session_id` 下只执行一次（哨兵文件：`.claude/.codex_review/done_<session_id>`）
- 读取 `.claude/.codex_review/files_<session_id>.txt`
- 对这些文件的 staged/unstaged diff 生成 prompt
- 调用：
  - `codex exec --sandbox read-only --output-last-message <report.md> -`
- 输出文件：
  - 成功：`.claude/reports/codex_review_<session_id>.md`
  - 失败：`.claude/reports/codex_review_<session_id>.error.log`

---

## 建议加到 `.gitignore`（可选）

把下面几行追加到你的项目 `.gitignore`（避免把本地报告/状态文件提交进仓库）：

```gitignore
# Claude/Codex automation
.claude/reports/
.claude/.codex_review/
.claude/.codex_home/
.claude/settings.local.json
```

---

## 快速自检 / 排查

### PowerShell 手动模拟（可选）

用于在不启动 Claude Code 的情况下，直接模拟 hook stdin JSON 输入，快速验证脚本工作流。

```powershell
$env:CLAUDE_PROJECT_DIR = (Get-Location).Path

# 模拟 PostToolUse(Write)
$write = @{session_id='manual_ps1'; tool_name='Write'; tool_input=@{file_path='test_file.txt'}} | ConvertTo-Json -Compress
$write | python .claude/hooks/record_touched_files.py

# 模拟 Stop（dry run：只生成 prompt，不调用 codex）
$env:CODEX_REVIEW_DRY_RUN = '1'
$stop = @{session_id='manual_ps1'} | ConvertTo-Json -Compress
$stop | python .claude/hooks/codex_review_touched_once.py

# 查看生成的 prompt
Get-Content -LiteralPath .claude/reports/codex_review_manual_ps1.prompt.txt -Raw
```

1) 先确认 Stop hook 能触发（不接 Codex）：

把 `.claude/settings.json` 的 Stop command 临时改成：

```json
"command": "echo '[claude stop hook fired]'"
```

2) 确认 codex 可用：

```bash
codex exec --sandbox read-only - <<'EOF'
Say hello in one sentence.
EOF
```

3) Dry run（不调用 Codex，只把 prompt 落盘，便于核对 diff/文件收集）：

- 给 Stop hook 临时加环境变量：`CODEX_REVIEW_DRY_RUN=1`
- 运行后会在 `.claude/reports/` 下生成：`codex_review_<sid>.prompt.txt`

---

## 可调项（可选，环境变量）

- `CODEX_REVIEW_TIMEOUT_SECONDS`：默认 180
- `CODEX_REVIEW_UNIFIED`：默认 5（git diff 上下文行）
- `CODEX_REVIEW_MAX_PROMPT_CHARS`：默认 200000（diff 太大时会截断并提示）

---

## 常见报错（经验）

- `python3` / `bash` / `mktemp` / `chmod` 不存在：不要用 Bash 方案，直接用本文的 Python 方案。
- `Codex cannot access session files at ...\\.codex\\sessions (permission denied)`：通常是 `~/.codex` 权限/归属异常导致；需要修复用户目录权限或重新初始化 Codex。
- `network error`：Codex 调用 API 需要网络；失败时查看 `.claude/reports/codex_review_<sid>.error.log`。

