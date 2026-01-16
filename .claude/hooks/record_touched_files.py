#!/usr/bin/env python
"""
Claude Code PostToolUse hook: 记录被 Write/Edit 触碰的文件
"""
import json
import os
import pathlib
import sys

def _get_file_path(tool_input):
    # Claude Code 的工具输入字段在不同版本/工具里可能略有不同，尽量兼容。
    for k in ("file_path", "path", "filepath", "filePath", "filename"):
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None

def _to_project_relpath(fp, proj):
    fp = fp.strip()
    if not fp:
        return None

    # 规范化路径：尽量保存为相对路径，便于 git diff -- <files>
    if os.path.isabs(fp):
        try:
            rel = os.path.relpath(fp, proj)
        except Exception:
            return None
    else:
        rel = fp

    rel = os.path.normpath(rel)

    # 忽略项目外路径（例如 ..\\..\\something），避免 git diff 误扫出仓库外文件。
    if rel.startswith("..") or os.path.isabs(rel):
        return None

    return rel

def main():
    try:
        data = json.load(sys.stdin)
    except:
        sys.exit(0)

    sid = data.get("session_id")
    tool = data.get("tool_name")
    inp = data.get("tool_input") or {}
    if isinstance(inp, str):
        try:
            inp = json.loads(inp) or {}
        except Exception:
            inp = {}
    fp = _get_file_path(inp)

    if not sid or tool not in ("Write", "Edit") or not fp:
        sys.exit(0)

    proj = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

    rel = _to_project_relpath(fp, proj)
    if not rel:
        sys.exit(0)

    out_dir = pathlib.Path(proj) / ".claude" / ".codex_review"
    out_dir.mkdir(parents=True, exist_ok=True)

    lst = out_dir / f"files_{sid}.txt"

    existing = set()
    if lst.exists():
        try:
            existing = set(x.strip() for x in lst.read_text(encoding="utf-8").splitlines() if x.strip())
        except Exception:
            existing = set()

    if rel not in existing:
        with lst.open("a", encoding="utf-8") as f:
            f.write(rel + "\n")

if __name__ == "__main__":
    main()
