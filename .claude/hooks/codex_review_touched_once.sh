#!/usr/bin/env bash
set -euo pipefail

# Stop hook JSON 从 stdin 读取。
# 我们用 session_id 做"只跑一次"的哨兵文件：
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
