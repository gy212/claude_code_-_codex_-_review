# Claude Code → Codex 自动 Review（Hook 脚本包）

Claude Code 在一次会话里改完代码后（Stop），自动调用 Codex CLI 做一次 review，并且只 review「本次会话里 Claude 触碰过的文件」。

仓库提供：文档 + 2 个 Python hook 脚本（跨平台，不依赖 bash/mktemp/chmod）。

## 工作原理

- PostToolUse（Write/Edit）：记录 touched files 到 `.claude/.codex_review/files_<session_id>.txt`
- Stop：对 touched files 生成 staged/unstaged diff → 调用 `codex exec` → 输出报告到 `.claude/reports/`

## 快速开始

1) 把脚本拷贝到你的项目：

- `.claude/hooks/record_touched_files.py`
- `.claude/hooks/codex_review_touched_once.py`

2) 修改你项目的 `.claude/settings.json`（不要整文件覆盖，把片段合并进去）：

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

提示：如果你的环境只有 `python3`，把命令里的 `python` 改成 `python3` 即可。

3) （可选）把下面几行追加到你项目的 `.gitignore`：

```gitignore
# Claude/Codex automation
.claude/reports/
.claude/.codex_review/
.claude/.codex_home/
.claude/settings.local.json
```

4) 验证 Codex CLI 可用（已登录 + 网络可用）：

```bash
codex exec --sandbox read-only - <<'EOF'
Say hello in one sentence.
EOF
```

## 输出文件

- 成功：`.claude/reports/codex_review_<session_id>.md`
- 失败：`.claude/reports/codex_review_<session_id>.error.log`
- 状态：`.claude/.codex_review/done_<session_id>`（保证同一 session 只跑一次）

## 可调项（环境变量）

- `CODEX_REVIEW_TIMEOUT_SECONDS`：默认 180
- `CODEX_REVIEW_UNIFIED`：默认 5（git diff 上下文行）
- `CODEX_REVIEW_MAX_PROMPT_CHARS`：默认 200000（diff 太大时会截断并提示）
- `CODEX_REVIEW_DRY_RUN=1`：只生成 `.claude/reports/codex_review_<sid>.prompt.txt`，不调用 codex

## Windows 说明

Windows 上 `codex` 可能是 `codex.ps1`；Stop 脚本已做兼容：会自动用 `pwsh`/`powershell` 启动 `codex.ps1`。

## 详细文档

查看仓库内的：`claude_code_→_codex_自动_review（hook_脚本包）.md`
