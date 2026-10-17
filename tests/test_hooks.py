"""Offline tests for .claude/hooks/guard_loader.py (no network, no FalkorDB). Last updated by Aaryan, Oct 17 2026.

Run:  uv run python tests/test_hooks.py
Each test pipes a fixture PreToolUse event (the JSON shape from the Claude Code hooks docs) into the hook
in a subprocess, the same way Claude Code runs it, and checks the exit code: 0 = no objection, 2 = blocked.
"""
import json
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "guard_loader.py"
FAILED = []


def run(stdin):
    p = subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, timeout=20,
                       env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(ROOT)})
    return p.returncode, p.stderr


def bash(command, tool="Bash"):
    event = {"session_id": "t", "cwd": str(ROOT), "hook_event_name": "PreToolUse", "tool_name": tool,
             "tool_input": {"command": command, "description": "test"}, "tool_use_id": "toolu_test"}
    return run(json.dumps(event))


def blocked(command):
    code, err = bash(command)
    assert code == 2 and "guard_loader:" in err, (command, code, err)
    return err


def allowed(command):
    code, err = bash(command)
    assert code == 0, (command, code, err)


def test_valid_case_allowed():
    assert (ROOT / "cases" / "obesity-coke" / "case.json").is_file()
    allowed("uv run python loader.py --case obesity-coke --rebuild")
    allowed("uv run python loader.py --case=philip-morris-lung --dry-run")
    allowed("FALKORDB_PORT=6380 python3 ./loader.py --rebuild --case obesity-coke")
    allowed("cd /home/x/falkor && uv run --directory . python loader.py --case obesity-coke --rebuild")


def test_refresh_blocked():
    assert "--refresh" in blocked("uv run python loader.py --case obesity-coke --rebuild --refresh")
    blocked("python loader.py --case obesity-coke --ref")       # argparse prefix of --refresh
    blocked("python loader.py --refresh --case obesity-coke")


def test_missing_or_bad_case_blocked():
    blocked("uv run python loader.py --rebuild")                 # default case is not enough
    blocked("uv run python loader.py --case")                    # no value
    blocked("uv run python loader.py --case no-such-case --rebuild")
    blocked("uv run python loader.py --case ../cases/obesity-coke")
    blocked("uv run python loader.py --case obesity-coke --case nope")  # argparse keeps the last one
    blocked("uv run python loader.py --case=obesity-coke --case=nope")  # reviewer: same with the = form


def test_bypasses_blocked():
    blocked("uv run python -m loader --rebuild")
    blocked("python -mloader --case obesity-coke --refresh")
    blocked("/home/x/falkor/loader.py --refresh --case obesity-coke")
    blocked("echo hi; python loader.py --refresh --case obesity-coke")
    blocked("true && uv run python loader.py --rebuild")
    blocked("ls || python loader.py")
    blocked("env OPENALEX_API_KEY=x uv run python loader.py --rebuild")
    blocked("bash -c \"uv run python loader.py --case obesity-coke --refresh\"")
    blocked("sh -c 'cd /tmp && python loader.py'")
    blocked("bash -c \"sh -c 'python loader.py --refresh --case obesity-coke'\"")  # nested
    blocked("echo $(python loader.py --refresh --case obesity-coke)")
    blocked("timeout 60 nohup python loader.py --case obesity-coke --refresh &")
    blocked("python -c 'import loader; loader.main()'")
    blocked("python loader.py --case 'obesity-coke")             # unbalanced quote


def test_newline_separates_commands():
    """Reviewer (Oct 17): bash runs each line as its own command, so a newline is a separator like `;`.
    A harmless first line (echo, grep, git ...) must not hide a loader call on the next line."""
    blocked("echo hi\nuv run python loader.py --case obesity-coke --refresh")
    blocked("git status\npython loader.py --rebuild")
    blocked("grep -n x README.md\n\npython3 loader.py --case obesity-coke --ref")
    allowed("echo hi\nuv run python loader.py --case obesity-coke --rebuild")
    allowed("uv run python loader.py --case obesity-coke \\\n  --rebuild")  # line continuation is one command


def test_reading_loader_allowed():
    allowed("grep -n refresh loader.py")
    allowed("head -40 loader.py")
    # Reviewer round 3: sed and awk can run commands (sed e, awk system()), so they are no longer readers
    blocked("sed -n 1,40p loader.py")
    blocked("awk 'BEGIN{system(\"python loader.py --refresh\")}' loader.py")
    allowed("git diff loader.py tests/test_loader.py")
    allowed("uv run python tests/test_loader.py")
    allowed("uv run python tests/test_hooks.py")
    allowed("ls -la")


def test_env_file_blocked():
    for cmd in ("cat .env", "cat ./.env", "head -1 /home/x/falkor/.env", "source .env && echo $OPENALEX_API_KEY",
                "grep KEY < .env", "python3 -c \"print(open('.env').read())\"", "cp .env /tmp/k",
                "echo x > .env"):
        assert ".env" in blocked(cmd), cmd
    allowed("cat .env.example")
    allowed("ls .envrc-notes")


def test_fail_closed():
    for stdin in ("", "not json", "[]", "{}", json.dumps({"tool_name": "Bash"}),
                  json.dumps({"tool_name": "Bash", "tool_input": "cat x"}),
                  json.dumps({"tool_name": "Bash", "tool_input": {"command": 5}})):
        code, err = run(stdin)
        assert code == 2 and "blocking to be safe" in err, (stdin, code, err)


def test_other_tools_ignored():
    code, _ = bash("loader.py --refresh", tool="Read")
    assert code == 0


def test_registered_in_settings():
    s = json.loads((ROOT / ".claude" / "settings.json").read_text())
    hooks = [h for m in s["hooks"]["PreToolUse"] if m["matcher"] == "Bash" for h in m["hooks"]]
    assert any(".claude/hooks/guard_loader.py" in " ".join(h.get("args", [])) and h.get("onFailure") == "block"
               for h in hooks), hooks
    assert s["permissions"]["ask"] == [  # the human-only gate is unchanged (CLAUDE.md)
        "mcp__tietrace-alpha__record_verdict", "mcp__tietrace-alpha__approve_note",
        "mcp__tietrace-beta__record_verdict", "mcp__tietrace-beta__approve_note"]
    # /path in project settings = <primary working directory>/path (permissions docs, Oct 17)
    assert {"Read(/.env)", "Edit(/.env)"} <= set(s["permissions"]["deny"])


def test_redirections():
    allowed("cat > loader.py <<EOF\nx = 1\nEOF")          # writing the file is not running it
    allowed("grep x < loader.py")
    allowed("git diff > out.txt")
    allowed("uv run python tests/test_loader.py > /tmp/out.txt 2>&1")
    blocked("python < loader.py")                          # runs loader code with no --case
    blocked("python3 - < ./loader.py --refresh")
    blocked("cat <(python loader.py --refresh --case obesity-coke)")  # process substitution runs a command
    blocked("echo x > .env")
    blocked("echo x >> ./.env")


def test_commit_message_may_name_env():
    allowed("git commit -m \"Deny reads of .env\"")
    allowed("git commit -m 'a' -m 'the .env deny rule'")
    allowed("git commit --message='.env rule'")
    allowed("git commit -F /tmp/msg.txt")
    blocked("git commit -m \"$(cat .env)\"")                # a substitution inside the message would run
    blocked("git add .env")
    blocked("git log -p -- .env")
    blocked("git commit -m \"$(python loader.py --refresh --case obesity-coke)\"")
    allowed("git commit -m \"use `--case` always\"")       # backticks run like $(...); this one is harmless
    blocked("git commit -m \"`python loader.py --refresh --case obesity-coke`\"")


def test_cheap_bypasses_blocked():
    blocked("ls *.py | xargs python loader.py --case obesity-coke")
    blocked("python load*.py --case obesity-coke --refresh")
    blocked("python loader.p? --rebuild")
    blocked("python \"$(echo loader).py\" --case obesity-coke")
    blocked("echo `python loader.py --refresh --case obesity-coke`")


def test_substituted_name_and_odd_redirects_blocked():
    """Reviewer round 2 (Oct 17). A loader name produced by $(...) or `...` runs the loader; round 1 blocked the
    backtick form. `>|` (clobber) and `> >(...)` are redirections, so words after them still go to the loader."""
    blocked("python $(echo loader.py) --refresh --case obesity-coke")
    blocked("python `echo loader.py` --refresh --case obesity-coke")
    blocked("python $(echo loader.py) --rebuild")                       # no --case either
    blocked("python loader.py --case obesity-coke >| out.txt --refresh")
    blocked("python loader.py --case obesity-coke > >(tee log) --refresh")


def test_round3_whole_text_rules():
    blocked("git -c alias.x='!python loader.py --refresh' x")
    blocked("git bisect run python loader.py --case obesity-coke --refresh")
    blocked("git bisect run python loader.py")                     # no --case
    blocked("python - < loader.py")
    blocked("cat loader.py | python -")
    blocked("cat <<EOF | python\nimport loader\nloader.main()\nEOF")
    blocked("uv run python -c 'import loader'")
    blocked("bash <<EOF\npython loader.py --rebuild\nEOF")         # loader inside a heredoc, no --case
    allowed("bash <<EOF\nuv run python loader.py --case obesity-coke --rebuild\nEOF")
    # documented false positives (CLAUDE.md, Guard hook): run these in your own terminal
    blocked("uv run ruff check loader.py")
    blocked("uv run python -m py_compile loader.py")
    allowed("grep -rn '[.]env' docs")                               # how to search for the name


def test_depth_limit():
    import shlex
    cmd = "echo a b"
    for _ in range(3):
        cmd = "bash -c " + shlex.quote(cmd)
    allowed(cmd)                                           # 3 levels: still checked
    assert "too deeply" in blocked("bash -c " + shlex.quote(cmd))  # a 4th level is refused


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print("PASS", name)
        except Exception:
            FAILED.append(name)
            print("FAIL", name)
            traceback.print_exc(limit=3)
    print(f"\n{len(tests) - len(FAILED)}/{len(tests)} passed")
    sys.exit(1 if FAILED else 0)
