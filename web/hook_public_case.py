"""Claude Code PreToolUse hook for the web app (web/app.py passes it with --settings; Aaryan's Builder, Oct 17).
Refuses any tietrace tool call with public_case=true BEFORE it runs: the web app always shows "Expert #n" (O3).
Contract: the call's JSON arrives on stdin; exit code 2 blocks the call and stderr is shown to the model.
Fails closed: input it cannot read is blocked too."""
import json
import sys

try:
    call = json.load(sys.stdin)
except (ValueError, UnicodeDecodeError):
    call = None
if not isinstance(call, dict) or not isinstance(call.get("tool_input", {}), dict):
    print("the web app's tool guard could not read this call, so it is blocked", file=sys.stderr)
    sys.exit(2)
if (call.get("tool_input") or {}).get("public_case") is True:
    print("public_case=true is not allowed in the web app: experts are always 'Expert #n'. Call again without it.",
          file=sys.stderr)
    sys.exit(2)
