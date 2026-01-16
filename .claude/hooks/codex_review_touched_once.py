#!/usr/bin/env python
"""
Claude Code Stop hook: 调用 Codex 对本 session 触碰的文件进行 review
每个 session 只执行一次；默认把 review 落盘到 .claude/reports/，并在终端打印
"""
import json
import os
import pathlib
import subprocess
import sys
import shutil
from datetime import datetime, timezone

DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_UNIFIED = 5
DEFAULT_MAX_PROMPT_CHARS = 200_000

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe_read_lines(path):
    try:
        return [x.strip() for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except Exception:
        return []

def _write_done(done_file: pathlib.Path, *, ok: bool, note: str) -> None:
    done_file.parent.mkdir(parents=True, exist_ok=True)
    payload = f"ok={str(ok).lower()}\nwhen={_utc_now_iso()}\n{note.strip()}\n"
    try:
        done_file.write_text(payload, encoding="utf-8")
    except Exception:
        # 兜底：至少把 done 文件摸出来，避免重复触发。
        try:
            done_file.touch()
        except Exception:
            pass

def _run(cmd, *, timeout=None, input_text=None):
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )

def _build_codex_exec_cmd(out_file):
    """
    Windows 上 `codex` 可能是 `codex.ps1`（PowerShell 脚本）。
    Python 不能直接 CreateProcess 运行 .ps1，所以需要通过 powershell/pwsh 去跑。
    """
    codex_path = shutil.which("codex")
    if not codex_path:
        return None

    # Prefer absolute resolved path from which(); keep as string.
    suffix = pathlib.Path(codex_path).suffix.lower()
    if os.name == "nt" and suffix == ".ps1":
        ps = shutil.which("pwsh") or shutil.which("powershell")
        if not ps:
            return None
        return [
            ps,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            codex_path,
            "exec",
            "--sandbox",
            "read-only",
            "--output-last-message",
            str(out_file),
            "-",
        ]

    return [
        codex_path,
        "exec",
        "--sandbox",
        "read-only",
        "--output-last-message",
        str(out_file),
        "-",
    ]

def main():
    try:
        data = json.load(sys.stdin)
    except:
        sys.exit(0)

    sid = data.get("session_id", "")
    if not sid:
        sys.exit(0)

    proj = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    os.chdir(proj)

    # 必须是 git 仓库
    try:
        result = _run(["git", "rev-parse", "--is-inside-work-tree"])
        if result.returncode != 0:
            sys.exit(0)
    except:
        sys.exit(0)

    state_dir = pathlib.Path(proj) / ".claude" / ".codex_review"
    state_dir.mkdir(parents=True, exist_ok=True)

    done_file = state_dir / f"done_{sid}"
    list_file = state_dir / f"files_{sid}.txt"

    report_dir = pathlib.Path(proj) / ".claude" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    out_file = report_dir / f"codex_review_{sid}.md"
    err_file = report_dir / f"codex_review_{sid}.error.log"
    prompt_dump = report_dir / f"codex_review_{sid}.prompt.txt"

    # 只 review 一次
    if done_file.exists():
        sys.exit(0)

    # 没有记录到 touched files
    if not list_file.exists() or list_file.stat().st_size == 0:
        sys.exit(0)

    # 读文件列表（去重但保序）
    raw_files = _safe_read_lines(list_file)
    seen = set()
    files = []
    for f in raw_files:
        if f not in seen:
            seen.add(f)
            files.append(f)
    if not files:
        sys.exit(0)

    # 检查是否有 diff
    try:
        r1 = subprocess.run(["git", "diff", "--quiet", "--"] + files, capture_output=True, check=False)
        r2 = subprocess.run(["git", "diff", "--cached", "--quiet", "--"] + files, capture_output=True, check=False)
        if r1.returncode == 0 and r2.returncode == 0:
            sys.exit(0)
    except:
        pass

    timeout = int(os.environ.get("CODEX_REVIEW_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    unified = int(os.environ.get("CODEX_REVIEW_UNIFIED", str(DEFAULT_UNIFIED)))
    max_prompt_chars = int(os.environ.get("CODEX_REVIEW_MAX_PROMPT_CHARS", str(DEFAULT_MAX_PROMPT_CHARS)))
    dry_run = os.environ.get("CODEX_REVIEW_DRY_RUN", "").strip() in ("1", "true", "yes", "on")

    # 生成 review prompt
    prompt_lines = [
        "You are a senior engineer performing a PR-style code review.",
        "Focus on: correctness, security, edge cases, performance, maintainability.",
        "Return: (1) Top risks (2) Concrete suggestions (3) Quick wins.",
        "",
        "## TOUCHED FILES",
        *[f"- {p}" for p in files],
        "",
        "## STAGED DIFF (only touched files)"
    ]

    try:
        staged = _run(["git", "--no-pager", "diff", "--cached", f"--unified={unified}", "--"] + files)
        prompt_lines.append(staged.stdout or "(no staged changes)")
    except:
        prompt_lines.append("(error getting staged diff)")

    prompt_lines.append("")
    prompt_lines.append("## UNSTAGED DIFF (only touched files)")

    try:
        unstaged = _run(["git", "--no-pager", "diff", f"--unified={unified}", "--"] + files)
        prompt_lines.append(unstaged.stdout or "(no unstaged changes)")
    except:
        prompt_lines.append("(error getting unstaged diff)")

    prompt_content = "\n".join(prompt_lines)
    if len(prompt_content) > max_prompt_chars:
        prompt_content = (
            prompt_content[:max_prompt_chars]
            + "\n\n[TRUNCATED: diff too large; set CODEX_REVIEW_MAX_PROMPT_CHARS to increase]\n"
        )

    if dry_run:
        prompt_dump.write_text(prompt_content, encoding="utf-8")
        print(f"[codex-review] dry run: wrote prompt to {prompt_dump}")
        return

    # 调用 Codex：只读审查 + 把最终输出落盘（避免把 Codex 的日志混进报告）
    try:
        codex_cmd = _build_codex_exec_cmd(out_file)
        if not codex_cmd:
            err_file.write_text(
                f"error=codex_not_found_or_unrunnable\nwhen={_utc_now_iso()}\n",
                encoding="utf-8",
            )
            _write_done(done_file, ok=False, note=f"error=codex_not_found_or_unrunnable\nerror_log={err_file}")
            return

        cp = _run(codex_cmd, timeout=timeout, input_text=prompt_content)
        if cp.returncode != 0:
            err_file.write_text(
                f"codex_exit_code={cp.returncode}\nwhen={_utc_now_iso()}\n\nSTDOUT:\n{cp.stdout}\n\nSTDERR:\n{cp.stderr}\n",
                encoding="utf-8",
            )
            _write_done(done_file, ok=False, note=f"codex_exit_code={cp.returncode}\nerror_log={err_file}")
            return
    except Exception as e:
        err_file.write_text(f"exception={repr(e)}\nwhen={_utc_now_iso()}\n", encoding="utf-8")
        _write_done(done_file, ok=False, note=f"exception={repr(e)}\nerror_log={err_file}")
        return

    _write_done(done_file, ok=True, note=f"out={out_file}")

    # 清理 list file（成功后再删，失败保留便于排查/手动重跑）
    try:
        list_file.unlink()
    except Exception:
        pass

    # 在终端打印摘要（可按需删掉）
    try:
        content = out_file.read_text(encoding="utf-8")
        print("")
        print(f"===== Codex Review (session {sid}) =====")
        print(content.rstrip())
        print("=======================================")
    except Exception:
        pass

if __name__ == "__main__":
    main()
