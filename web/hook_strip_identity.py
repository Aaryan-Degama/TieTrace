"""Claude Code PostToolUse hook for the web app (web/app.py passes it with --settings; Aaryan's Builder, Oct 17).
Takes ORCIDs and institutions out of every tietrace tool result BEFORE the model reads it, so the web reply cannot
identify an "Expert #n" (round 4). It replaces the result through hookSpecificOutput.updatedMCPToolOutput.
server.py itself is unchanged: Claude Code users still get the full envelope.
Fails closed: a result it cannot read is replaced by an error envelope."""
import json
import re
import sys

HIDDEN = {"orcid", "last_known_org"}  # the identifying fields in research.py's outputs
# An ORCID standing alone or after "orcid.org/", but never inside a DOI: SciELO-style DOIs such as
# 10.47626/2237-6089-2021-0237 have the same digit shape (review round 5), so a match after "/", ".", "-" or a
# letter or digit is skipped, unless the text before it is "orcid.org/".
ORCID_RE = re.compile(r"(?:(?<=(?i:orcid\.org/))|(?<![\w/.-]))\d{4}-\d{4}-\d{4}-\d{3}[\dX](?![\w-])")
DOI_KEYS = {"doi", "dois"}  # never touched


def strip(x, key=None):
    if isinstance(x, dict):
        return {k: strip(v, k) for k, v in x.items() if k not in HIDDEN}
    if isinstance(x, list):
        return [strip(v, key) for v in x]
    if isinstance(x, str) and key not in DOI_KEYS:
        return ORCID_RE.sub("[ORCID hidden]", x)
    return x


def clean(response):
    """The tool's result (a JSON string, or content blocks holding one) with the identifying fields removed."""
    if isinstance(response, list):  # content blocks
        return [{**b, "text": clean(b["text"])} if isinstance(b, dict) and isinstance(b.get("text"), str) else b
                for b in response]
    if isinstance(response, str):
        try:
            return json.dumps(strip(json.loads(response)))
        except ValueError:
            return ORCID_RE.sub("[ORCID hidden]", response)
    return strip(response)


WITHHELD = json.dumps({"ok": False, "error": {"code": "INTERNAL", "message": "the web app could not check this tool "
                                             "result, so it is withheld"}})


def main():
    """Never crashes and always exits 0: on a crash (or a non-zero exit) Claude Code would pass the ORIGINAL result,
    ORCID and institution included, to the model (review round 4). Any failure withholds the result instead."""
    try:
        out = clean(json.load(sys.stdin)["tool_response"])
        if ORCID_RE.search(json.dumps(out)):  # belt and braces: nothing ORCID-shaped may pass
            out = WITHHELD
    except Exception:  # RecursionError on deep JSON, bad input, anything
        out = WITHHELD
    try:
        text = json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedMCPToolOutput": out}})
    except Exception:
        text = json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedMCPToolOutput": WITHHELD}})
    sys.stdout.write(text + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
