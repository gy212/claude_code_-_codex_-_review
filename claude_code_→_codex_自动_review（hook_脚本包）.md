# Claude Code → Codex 自动 Review（Hook 脚本包）

目标：
- Claude Code 在一次会话里改完代码后（Stop），自动调用 Codex CLI 做一次 review。
- 不依赖 `git commit`。
- 只 review「本次会话里 Claude 触碰过的文件」的 diff（避免把历史脏 diff 混进来）。
- **每个 session 只 review 一次**（避免 Stop 多次触发导致重复 review）。

---

## 你需要改动/新增的文件

### 1) 项目配置：`.claude/settings.json`

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/record_touched_files.sh"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/codex_review_touched_once.sh",
            "timeout": 180
          }
        ]
      }
    ]
  }
}
```

> 说明：
> - `PostToolUse` 只对 `Write|Edit` 触发：记录被改的文件。
> - `Stop` 触发：收集本 session 的 touched files，拼 diff，调用 `codex exec`。

---

### 2) 新增脚本：`.claude/hooks/record_touched_files.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# Claude Code 会把 hook 事件 JSON 从 stdin 传进来。
# 这里解析 session_id + tool_input.file_path，把被触碰的文件记录到：
#   .claude/.codex_review/files_<session_id>.txt

python3 - <<'PY'
import json, os, pathlib, sys

data = json.load(sys.stdin)
sid = data.get("session_id")
tool = data.get("tool_name")
inp = data.get("tool_input") or {}
fp = inp.get("file_path")

if not sid or tool not in ("Write","Edit") or not fp:
    sys.exit(0)

proj = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

# 规范化路径：尽量存相对路径，便于 git diff -- <files>
if os.path.isabs(fp):
    rel = os.path.relpath(fp, proj)
else:
    rel = fp
rel = os.path.normpath(rel)

out_dir = pathlib.Path(proj) / ".claude" / ".codex_review"
out_dir.mkdir(parents=True, exist_ok=True)

lst = out_dir / f"files_{sid}.txt"

existing = set()
if lst.exists():
    existing = set(x.strip() for x in lst.read_text(encoding="utf-8").splitlines() if x.strip())

if rel not in existing:
    with lst.open("a", encoding="utf-8") as f:
        f.write(rel + "\n")
PY
```

---

### 3) 新增脚本：`.claude/hooks/codex_review_touched_once.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# Stop hook JSON 从 stdin 读取。
# 我们用 session_id 做“只跑一次”的哨兵文件：
#   .claude/.codex_review/done_<session_id>

SID="$(python3 - <<'PY'
import json, sys
print((json.load(sys.stdin) or {}).get("session_id", ""))
PY
)"

# 没有 session_id 就不执行（保险）
[[ -n "$SID" ]] || exit 0

cd "${CLAUDE_PROJECT_DIR:-.}"

# 必须是 git 仓库
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

STATE_DIR=".claude/.codex_review"
mkdir -p "$STATE_DIR"

DONE_FILE="$STATE_DIR/done_${SID}"
LIST_FILE="$STATE_DIR/files_${SID}.txt"

# 只 review 一次：如果 done 文件存在，直接退出
if [[ -f "$DONE_FILE" ]]; then
  exit 0
fi

# 没有记录到 touched files，就不 review
[[ -s "$LIST_FILE" ]] || exit 0

# 读文件列表
mapfile -t FILES < "$LIST_FILE"

# 过滤掉空行
FILTERED=()
for f in "${FILES[@]}"; do
  [[ -n "${f// }" ]] && FILTERED+=("$f")
done
FILES=("${FILTERED[@]}")

[[ ${#FILES[@]} -gt 0 ]] || exit 0

# touched files 在 staged/unstaged 都没变化，就不 review
if git diff --quiet -- "${FILES[@]}" && git diff --cached --quiet -- "${FILES[@]}"; then
  exit 0
fi

# 输出目录
OUT_DIR=".claude/reports"
mkdir -p "$OUT_DIR"
OUT_FILE="$OUT_DIR/codex_review_${SID}.md"

# 生成 review prompt（把 diff 塞给 Codex）
TMP_PROMPT="$(mktemp)"
{
  echo "You are a senior engineer performing a PR-style code review."
  echo "Focus on: correctness, security, edge cases, performance, maintainability."
  echo "Return: (1) Top risks (2) Concrete suggestions (3) Quick wins."
  echo ""
  echo "## STAGED DIFF (only touched files)"
  git --no-pager diff --cached --unified=5 -- "${FILES[@]}" || true
  echo ""
  echo "## UNSTAGED DIFF (only touched files)"
  git --no-pager diff --unified=5 -- "${FILES[@]}" || true
} > "$TMP_PROMPT"

# 调 Codex：只读审查 + 把最终输出落盘
# 依赖：codex 在 PATH 里
codex exec --sandbox read-only --output-last-message "$OUT_FILE" - < "$TMP_PROMPT"

# 标记 done：保证同 session 后续 Stop 不再重复 review
: > "$DONE_FILE"

# 清理临时文件（保留 OUT_FILE 和 DONE_FILE，便于你回看/排查）
rm -f "$TMP_PROMPT" "$LIST_FILE"

# 在终端打印摘要（可删）
echo ""
echo "===== Codex Review (session $SID) ====="
cat "$OUT_FILE"
echo "======================================="
```

---

### 4) 权限

```bash
chmod +x .claude/hooks/record_touched_files.sh
chmod +x .claude/hooks/codex_review_touched_once.sh
```

---

## 建议加到 .gitignore（可选）

避免把本地报告/状态文件提交进去：

```gitignore
# Claude/Codex automation
.claude/reports/
.claude/.codex_review/
```

---

## 快速自检（排查不触发/不执行）

1) **先确认 Stop hook 能触发**（不接 Codex）：

把 `.claude/settings.json` 的 Stop command 临时替换成：

```json
"command": "echo '[claude stop hook fired]'"
```

只要 Claude Code 回复一次，你就应该看到输出。

2) **确认 codex 可被脚本调用**：

```bash
codex exec --sandbox read-only - <<'EOF'
Say hello in one sentence.
EOF
```

3) **确认 touched files 记录正常**：

在 Claude Code 里让它改一个文件，然后看：

```bash
ls -la .claude/.codex_review/
cat .claude/.codex_review/files_*.txt
```

---

## 可调整项（按你的习惯）

- 只 review staged：把脚本里 UNSTAGED DIFF 那段删掉即可。
- review 太慢：把 diff 的 `--unified=5` 改小，或只传 staged。
- 想让输出更“可机器处理”：在 `codex exec` 上加 `--output-schema`，让它输出 JSON（你再加工）。

---

## 你只需要做的事

1. 新建目录：`.claude/hooks/`
2. 按上述内容新增两个脚本
3. 写入 `.claude/settings.json`
4. `chmod +x`
5. 运行 Claude Code，让它改动一个文件，结束后看终端是否出现 Codex Review 输出，以及 `.claude/reports/codex_review_<sid>.md`

