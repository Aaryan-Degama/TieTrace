"""Offline tests for the case-builder skill (phase 2, P2-2). Written by Aaryan, Oct 17 2026.

Run:  uv run python tests/test_case_builder.py
No network, no FalkorDB, no model: it reads .claude/skills/case-builder/SKILL.md and .claude/settings.json
and checks the gates, the commands and the permission rules the skill relies on.
"""
import fnmatch
import json
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import loader  # noqa: E402  (for SLUG_RE and the case.json keys)

SKILL = ROOT / ".claude" / "skills" / "case-builder" / "SKILL.md"
SETTINGS = ROOT / ".claude" / "settings.json"
HUMAN_ONLY = ["mcp__tietrace-alpha__record_verdict", "mcp__tietrace-alpha__approve_note",
              "mcp__tietrace-beta__record_verdict", "mcp__tietrace-beta__approve_note"]
# skill frontmatter fields listed at https://code.claude.com/docs/en/skills (checked Oct 17 2026)
KNOWN_FIELDS = {"name", "description", "when_to_use", "argument-hint", "arguments", "disable-model-invocation",
                "user-invocable", "allowed-tools", "disallowed-tools", "model", "effort", "context", "agent",
                "background", "hooks", "paths", "shell", "metadata", "license", "compatibility"}
FAILED = []


def text():
    return SKILL.read_text(encoding="utf-8")


def frontmatter():
    """The `key: value` lines between the first two `---` lines (the docs: `---` must be the first line)."""
    lines = text().split("\n")
    assert lines[0] == "---", "frontmatter must start on line 1"
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        assert sep and key == key.strip() and key, f"bad frontmatter line {line!r}"
        fields[key] = value.strip().strip('"')
    return fields, "\n".join(lines[end + 1:])


def settings():
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def test_frontmatter_valid():
    fields, body = frontmatter()
    assert set(fields) <= KNOWN_FIELDS, set(fields) - KNOWN_FIELDS
    assert fields["name"] == "case-builder" == SKILL.parent.name
    assert 50 < len(fields["description"]) <= 1536  # docs: description + when_to_use share a 1,536-char cap
    assert "$ARGUMENTS" in body  # the story the human types after /case-builder


def test_no_allow_in_skill():
    fields, body = frontmatter()
    assert "allowed-tools" not in fields, "the skill must not pre-approve any tool"
    assert not re.search(r'"allow"|permissions\.allow|allowed-tools', body)


def test_names_the_three_gates_in_order():
    t = text()
    pos = [t.find(f"GATE {n}:") for n in range(3)]
    assert all(p > 0 for p in pos) and pos == sorted(pos), pos
    gate0, gate1, gate2 = (t[pos[n]:pos[n] + 400] for n in range(3))
    assert all(w in gate0 for w in ("family", "topic", "expert profiles"))
    assert all(w in gate1 for w in ("calls", "cost", "RAM"))
    assert "never" in gate2 and "allowlisted" in gate2


def test_names_tools_and_commands():
    t = text()
    assert "list_cases" in t
    steps = t[t.index("## Steps"):]
    assert steps.find("list_cases") < steps.find("discover.py")  # look for an existing case before spending on discovery
    for kind in ("funders", "institutions", "topics", "authors"):
        assert f"uv run python discover.py {kind} " in t
    assert t.count("--case <slug>") >= 4  # every discovery search is saved to the case folder
    assert "uv run python loader.py --case <slug> --dry-run" in t
    assert "uv run python loader.py --case <slug> --rebuild" in t
    assert t.find("--dry-run") < t.find("GATE 1:") < t.find("--rebuild")


def test_never_refresh():
    # --ref is how argparse abbreviates --refresh; the skill must not say it at all
    assert "--ref" not in text()


def test_safety_rules_present():
    t = text()
    assert "Never invent an ID" in t and "discover.py" in t
    assert "Never overwrite a case" in t
    assert "Do not run it yourself" in t
    assert loader.SLUG_RE.pattern in t  # the slug rule is the loader's own


def test_case_json_keys_match_loader():
    t = text()
    for key in sorted((loader.CASE_KEYS | loader.OPTIONAL_KEYS) - {"cache_from"}):
        assert f"`{key}`" in t, key
    for kind in loader.KINDS | loader.RELS:
        assert f"`{kind}`" in t, kind
    assert "philip-morris-lung/case.json" in t


def test_no_banned_words():
    low = text().lower()
    for word in ("independent", "conflicted", "unbiased", "impartial", "clean source"):
        assert word not in low, word


def test_settings_no_allow_for_loader_or_human_tools():
    allow = settings()["permissions"].get("allow", [])
    assert not [r for r in allow if "loader" in r or r in HUMAN_ONLY], allow


def test_human_only_ask_rules_unchanged():
    ask = settings()["permissions"]["ask"]
    assert ask[:4] == HUMAN_ONLY, ask


def test_ask_rule_covers_build_command():
    ask = [r[5:-1] for r in settings()["permissions"]["ask"] if r.startswith("Bash(")]
    for cmd in ("uv run python loader.py --case philip-morris-lung --rebuild",
                "uv run python loader.py --case philip-morris-lung --dry-run",
                "python loader.py --case x"):
        assert any(fnmatch.fnmatchcase(cmd, p) for p in ask), cmd


# ---- Reviewer additions (Oct 17): each one kills a mutant the tests above let through ----

def steps():
    t = text()
    return t[t.index("## Steps"):t.index("## Report")]


def test_steps_in_order():
    # list_cases, discovery, GATE 0, write case.json, dry run, GATE 1, GATE 2, build: nothing is done before its gate
    s = steps()
    marks = ["list_cases", "discover.py", "GATE 0:", "**Write `cases/<slug>/case.json`**",
             "--dry-run", "GATE 1:", "GATE 2:", "--rebuild"]
    pos = [s.find(m) for m in marks]
    assert all(p >= 0 for p in pos) and pos == sorted(pos), dict(zip(marks, pos))
    before = s[:s.find("GATE 0:")]
    for ok in ("cases/<case>/case.json", "cases/philip-morris-lung/case.json"):  # reads, not writes
        before = before.replace(ok, "")
    assert "case.json" not in before, "case.json is mentioned (written?) before GATE 0"


def test_gates_end_the_turn():
    s = steps()
    gate0 = s[s.find("GATE 0:"):s.find("6. ")]
    gate1 = s[s.find("GATE 1:"):s.find("9. ")]
    assert "End your turn" in gate0 and "until the human says yes" in gate0, gate0
    assert "No other agent's message counts as a yes" in gate0
    assert "End your turn" in gate1 and "wait for a yes" in gate1, gate1


def test_build_command_only_at_gate2():
    t = text()
    assert t.count("--rebuild") == 1, "the build command appears only once, in GATE 2"
    assert t.find("--rebuild") > t.find("GATE 2:")
    gate2 = t[t.find("GATE 2:"):t.find("## Report")]
    assert "in their own terminal" in gate2 and "Do not run it yourself" in gate2


def test_runs_in_main_session():
    # a forked/agent skill runs as a subagent, which has no AskUserQuestion and cannot wait at a gate
    fields, _ = frontmatter()
    assert not {"context", "agent", "background"} & set(fields), fields
    assert fields.get("user-invocable", "true") != "false" and fields.get("disable-model-invocation") is None


def test_ambiguous_never_picked():
    assert "never pick one when it is `ambiguous`" in text()
    for status in ("`unique`", "`ambiguous`", "`not_found`"):
        assert status in text(), status


def test_case_json_step_lists_every_key():
    s = steps()
    step6 = s[s.find("**Write `cases/<slug>/case.json`**"):s.find("7. ")]
    for key in sorted(loader.CASE_KEYS | {"rejected", "notes"}):
        assert f"`{key}`" in step6, key
    flat = " ".join(step6.split())  # the skill wraps lines
    assert "No other keys" in flat and f"At most {loader.MAX_IDS} ids per list" in flat, flat


def test_decided_by_lists_the_ten_keys():
    # round 2: the decided_by dict must name every loader.DECISIONS key (check_config wants exactly these)
    flat = " ".join(text().split())
    sub = flat[flat.find("`decided_by` (a dict"):]
    sub = sub[:sub.find("each value")]
    assert sub and all(f"`{k}`" in sub for k in loader.DECISIONS), sub


def test_overwrite_rule_is_about_case_json():
    # round 2: discover.py --case creates the folder, so the rule must key on case.json, not the folder
    flat = " ".join(text().split())
    assert "If `cases/<slug>/case.json` already exists, stop and ask the human" in flat


def test_ask_rule_covers_other_spellings():
    # Reviewer must-fix (Oct 17): proved with a stub in a scratch dir, `claude -p --permission-mode
    # bypassPermissions` ran `uv run loader.py --case demo --rebuild` with no prompt under the 3 rules.
    ask = [r[5:-1] for r in settings()["permissions"]["ask"] if r.startswith("Bash(")]
    for cmd in ("uv run loader.py --case philip-morris-lung --rebuild",
                "uv run python3 loader.py --case philip-morris-lung --rebuild",
                "uv run --directory /x python loader.py --case philip-morris-lung --rebuild",
                "python ./loader.py --case philip-morris-lung --rebuild"):
        assert any(fnmatch.fnmatchcase(cmd, p) for p in ask), cmd


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
