"""PreToolUse hook for the Bash tool (phase 2, P2-1; IMPROVEMENT.md 5g, D12). Last updated by Aaryan, Oct 17 2026.

Claude Code sends the tool call as JSON on stdin. This script blocks (exit 2, reason on stderr) when a
model-issued Bash command would:
  1. run loader.py with --refresh (it re-downloads from OpenAlex and spends the API budget), or
  2. run loader.py without --case <slug> where cases/<slug>/case.json exists, or
  3. name the .env file (it holds OPENALEX_API_KEY), e.g. `cat .env`, `source .env`, python open('.env').
Anything else exits 0 and goes through the normal permission flow. Unreadable input also exits 2
(fail closed). Humans running loader.py in their own terminal are not affected: hooks only see Claude's
tool calls.

This is a text check and a seatbelt, not a sandbox. The real limit on the API budget is the OpenAlex key.

Whole-text rules first (they hold however the command is split): when the text mentions loader outside a
pure reader part, any `--ref` anywhere is refused; when a loader run is found, or loader sits inside
$(...), backticks or a heredoc body, the last --case in the text must be a real case; python reading
code from -c, - or a heredoc is refused while the text mentions loader.

Then part by part. How a command is read: a newline is a command separator like `;` (a `\\`-newline joins two lines).
shlex then splits it into words and shell operators (; && || | & ( )), so each part of a chain is
checked on its own. `> file` and `>> file` are dropped from the part (their target is checked only for
.env); `< file` keeps the file as a word, so `python < loader.py` still counts as running the loader.
A part "runs the loader" when one of its words is loader.py (any path: ./loader.py, /x/loader.py, or a
glob such as load*.py), or the word after `-m` is loader. Wrappers such as `env X=y`, `uv run`,
`timeout 60` do not hide it, because every word is looked at. A quoted word with a space (the "..." in
`bash -c "..."` or `eval "..."`) and any word with $(...) or backticks is checked again as a command of
its own, up to 3 levels deep. A loader part with xargs, or a loader name built from $(...) or a glob, is
refused outright.
Parts that start with a read-only or file tool (grep, cat, head, git, cp, ...) may mention loader.py,
unless a word holds $(...) or backticks, or it is `git -c` / `git bisect`. sed and awk are not in that list,
because they can run commands.
The message of `git commit -m` may mention .env.
ponytail: text checks, not a shell. Known gaps (CLAUDE.md, Guard hook): `x=loader.py; python $x`,
`cat loader.py | python -`, `cp loader.py y.py` then run it, `printf` building a command, a `python -c`
string that builds the name from pieces. The real limit is the OpenAlex key budget; this hook stops the
obvious mistakes.
"""
import fnmatch
import json
import os
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")  # same rule as loader.SLUG_RE
ENV_RE = re.compile(r"(?<![\w.-])\.env(?![\w.-])")  # .env, ./.env, '.env'; not .env.example or x.env
REF_RE = re.compile(r"(?<![\w-])--ref")  # --refresh and its argparse abbreviations
CASE_RE = re.compile(r"(?<![\w-])--cas?e?(?:=|\s+)([^\s;&|<>()`'\"]*)")  # --c is not enough: --ca, --cas, --case
SUBST_RE = re.compile(r"\$\([^)]*loader|`[^`]*loader")
OPS = set(";&|()<>")
READERS = {"grep", "rg", "cat", "head", "tail", "less", "more", "wc", "ls", "git", "diff",
           "file", "stat", "echo", "printf", "cp", "mv", "touch", "chmod"}  # not sed/awk: both can run commands


def deny(reason):
    print(f"guard_loader: {reason}", file=sys.stderr)
    sys.exit(2)


def parts(command):
    """Split a command into (words, out_targets), one pair per simple command."""
    # `x` runs x like $(x) does, even inside double quotes; one spelling keeps the rest simple
    command = command.replace("\\\n", " ").replace("\n", ";").replace("`", " $( ")
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    out, words, targets, redirect = [], [], [], None
    for tok in lex:
        if redirect == ">":  # the file after > or >>: not a word of the command
            targets.append(tok)
            redirect = None
            continue
        redirect = None
        if not set(tok) <= OPS:
            words.append(tok)
        elif ">" in tok and not set(tok) & set("();"):  # >, >>, >|, 2>, >&, &>
            redirect = ">"
        elif set(tok) & set(";|()"):  # also <( and >( : process substitution starts a new command
            out.append((words, targets))
            words, targets = [], []
        elif "<" not in tok:  # & and &&; for < file and <<EOF the next word stays in the part
            out.append((words, targets))
            words, targets = [], []
    return [p for p in out + [(words, targets)] if p[0] or p[1]]


def is_loader_word(w):
    w = w.strip("`")
    base = os.path.basename(w)
    if base == "loader.py":
        return True
    if any(c in base for c in "*?[") and fnmatch.fnmatch("loader.py", base):  # load*.py, loader.p?
        return True
    return "loader" in w and ("$" in w or "`" in w)  # "$(echo loader).py" and similar


def loader_args(words):
    """The words after the loader in this part, or None when this part does not run the loader."""
    for i, w in enumerate(words):
        if is_loader_word(w):
            return words[i + 1:]
        if w == "-m" and i + 1 < len(words) and words[i + 1] == "loader":
            return words[i + 2:]
        if w == "-mloader":
            return words[i + 1:]
    return None


def case_of(args):
    """The value of the last --case (argparse also accepts --c, --ca, --cas and --case=x)."""
    slug = None
    for i, a in enumerate(args):
        name, eq, value = a.partition("=")
        if len(name) >= 3 and "--case".startswith(name):
            slug = value if eq else (args[i + 1] if i + 1 < len(args) else "")
    return slug


def commit_message_words(words, program):
    """Indexes of the -m/--message values of `git commit` (a commit message may mention .env).
    A message with $ or a backtick is not exempt: "$(cat .env)" would run inside it."""
    if program != "git" or "commit" not in words:
        return set()
    skip = set()
    for i, w in enumerate(words):
        if w in ("-m", "--message") and i + 1 < len(words):
            skip.add(i + 1)
        elif w.startswith("--message=") or (w.startswith("-m") and len(w) > 2 and not w.startswith("--")):
            skip.add(i)
    return {i for i in skip if "$" not in words[i] and "`" not in words[i]}


def inner(word):
    """A quoted word read as a command: `x` and $(x) become plain parts, so the next level never sees them again."""
    return word.replace("`", " ").replace("$(", " ( ")


def program_of(words):
    """(program name, pure reader?). A pure reader part only reads or edits files: it may name loader.py.
    git -c (aliases) and git bisect run can execute commands, and $(...) or `...` in a word always runs."""
    i = 0
    while i < len(words) and re.match(r"\w+=", words[i]):  # skip X=y before the program
        i += 1
    program = os.path.basename(words[i]) if i < len(words) else ""
    git_exec = program == "git" and ("-c" in words or "bisect" in words)
    pure = program in READERS and not git_exec and not any("$(" in w or "`" in w for w in words)
    return program, pure


def valid_case_in(text):
    """True when the last --case in the whole text names a case with cases/<slug>/case.json."""
    found = CASE_RE.findall(text)
    slug = found[-1] if found else ""
    return bool(SLUG_RE.fullmatch(slug)) and (ROOT / "cases" / slug / "case.json").is_file()


def whole_text_rules(command, found):
    """Rules on the whole command text, not part by part (Reviewer, round 3): they hold even when a
    $(...), a backtick, a heredoc or an odd redirect splits a loader call into pieces."""
    if "loader" not in command:
        return
    outside = any(not program_of(w)[1] and any("loader" in x for x in w) for w, _ in found)
    run = any(not program_of(w)[1] and (loader_args(w) is not None or
                                        (program_of(w)[0] == "git" and any("loader" in x for x in w)))
              for w, _ in found)  # a loader run, or git -c / bisect run near loader
    has_heredoc = "<<" in command
    body = command.split("<<", 1)[1].partition("\n")[2] if has_heredoc else ""  # lines after the << line
    hidden = bool(SUBST_RE.search(command)) or "loader" in body  # loader inside $(...), `...` or a heredoc
    if (outside or run or hidden) and (REF_RE.search(command) or REF_RE.search(re.sub(r"[\"'\\]", "", command))):
        deny("--refresh with loader.py calls the OpenAlex API; a human must run it in their own terminal")
    if (run or hidden) and not valid_case_in(command):
        deny("loader.py needs --case <slug> (cases/<slug>/case.json); the default case is not allowed here")
    for w, _ in found:
        python = any(os.path.basename(x).startswith("python") for x in w)  # also `uv run python ...`
        reads_stdin = not any(x.endswith(".py") for x in w) and "-m" not in w  # `... | python` with no script
        if python and ("-c" in w or "-" in w or (has_heredoc and reads_stdin)):
            deny("python reading code from -c, - or a heredoc while the command mentions loader cannot be checked")


def check(command, depth=0):
    if depth > 3:
        deny("command nests shells too deeply to check")
    try:
        found = parts(command)
    except ValueError:  # unbalanced quotes: cannot read it, so do not let a loader or .env call through
        if ENV_RE.search(command):
            deny(".env holds the OpenAlex key; Claude may not read or change it (only loader.py reads it)")
        if "loader" in command:
            deny("cannot parse a command that mentions loader")
        return
    whole_text_rules(command, found)
    for words, targets in found:
        program, pure = program_of(words)
        message = commit_message_words(words, program)
        if any(ENV_RE.search(w) for n, w in enumerate(words) if n not in message) or \
                any(ENV_RE.search(t) for t in targets):
            deny(".env holds the OpenAlex key; Claude may not read or change it (only loader.py reads it)")
        if pure:
            continue
        args = loader_args(words)
        if args is not None:
            if "xargs" in words:
                deny("loader.py through xargs cannot be checked")
            if any(is_loader_word(w) and any(c in w for c in "$`*?[") for w in words):
                deny("a loader name built from $(...), `...` or a glob cannot be checked")
            if any(a.strip("`").startswith("--ref") for a in args):  # argparse takes --ref.. for --refresh
                deny("loader.py --refresh calls the OpenAlex API; a human must run it in their own terminal")
            slug = case_of(args)
            if not slug:
                deny("loader.py needs --case <slug> (cases/<slug>/case.json); the default case is not allowed here")
            if not SLUG_RE.fullmatch(slug) or not (ROOT / "cases" / slug / "case.json").is_file():
                deny(f"loader.py --case {slug!r}: no cases/{slug}/case.json")
        for w in words:
            if " " in w or "$(" in w or "`" in w:  # bash -c "...", eval "...", $(...): check the inner command
                check(inner(w), depth + 1)


def main():
    try:
        event = json.loads(sys.stdin.read())
        tool, command = event["tool_name"], event.get("tool_input", {}).get("command")
    except Exception as e:
        deny(f"unreadable hook input ({type(e).__name__}); blocking to be safe")
    if tool != "Bash":
        return
    if not isinstance(command, str):
        deny("Bash call without a command string; blocking to be safe")
    check(command)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # a bug in this script must block, not let the command through
        deny(f"internal error {type(e).__name__}; blocking to be safe")
