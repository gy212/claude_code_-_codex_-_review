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
