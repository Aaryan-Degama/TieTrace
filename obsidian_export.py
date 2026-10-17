"""Export one newsroom memory graph (newsroom:<id>) to an Obsidian vault, so Graph view shows it live.

Run:  uv run python obsidian_export.py [--vault PATH] [--newsroom ID] [--watch SECONDS]
Reads only newsroom:<id> with GRAPH.RO_QUERY (memory.ro: 3 s timeout, row cap). Never writes to FalkorDB,
never reads a research graph, never calls OpenAlex. Writes only inside <vault>/TieTrace/<newsroom>/. A note
that no longer matches the data is moved to <vault>/.trash/ (Obsidian's trash), and only if it starts with the
`tietrace: true` marker (a note this exporter made); a file without the marker is never overwritten or moved. Known limit (review S5): an expert checked in a public case
keeps that name in the note's title and aliases, so a private case linking to that note shows the name too.
"""
import argparse
import hashlib
import json
import os
import re
import signal
import sys
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import memory

DEFAULT_VAULT = Path.home() / "Documents" / "Obsidian Vault"
MARKER = "---\ntietrace: true\n"
ROW_CAP = 5000  # each query asks for ROW_CAP + 1 rows, so memory.ro raises ROW_LIMIT instead of cutting silently
CASES_Q = ("MATCH (c:Case) RETURN c.id, c.story, c.company, c.topic, c.expert_ids, c.public_case, "
           "c.research_case, c.created_at ORDER BY c.created_at LIMIT 5001")
CHECKS_Q = ("MATCH (c:Case)-[:HAS_CHECK]->(k:Check) OPTIONAL MATCH (newer:Check)-[:SUPERSEDES]->(k) "
            "RETURN c.id, k.id, k.expert_id, k.label, k.tier, k.score, k.data_version, k.ts, k.snapshot_json, "
            "newer.id ORDER BY k.ts LIMIT 5001")
COUNTS_Q = "MATCH (n) RETURN labels(n)[0] AS kind, count(n) ORDER BY kind"
README = """# TieTrace in Graph view

The notes in this folder are rewritten by `obsidian_export.py`; edit them and your change is overwritten.
Notes without the `tietrace: true` line (like this one) are never touched. A TieTrace note that no longer
matches the memory graph is moved to the vault's `.trash` folder, so it can be restored.

To colour experts by tier: Graph view, Settings (gear), Groups, New group, and enter one query per group:
`tag:#tier/A1`, `tag:#tier/A2`, `tag:#tier/B`, `tag:#tier/C`, `tag:#tier/NONE`.
Other tags: `tag:#tietrace/case`, `tag:#tietrace/paper`, `tag:#tietrace/funder`, `tag:#tietrace/org`.
"""


def safe(text):
    """A file name Obsidian and every OS accept: no /\\:*?"<>|#^[] and no control characters."""
    return re.sub(r'[/\\:*?"<>|#^\[\]\x00-\x1f\x7f]', "", str(text)).strip(" .") or "untitled"


def plain(text):
    """Text safe inside a [[link|label]] or a heading: no | [ ] or control characters."""
    return re.sub(r"[|\[\]\x00-\x1f\x7f]", " ", str(text)).strip()


def escaped(text):
    """The journalist's words on one line, with Markdown that could make links, tags or comments escaped."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text))
    return re.sub(r"([#`\[\]%\\])", r"\\\1", text)


def doi_url(doi):
    return "https://doi.org/" + urllib.parse.quote(doi, safe="/:;")


def paper_names(dois):
    """{doi: 'Paper 10.1016_j.x'}. Only when two DOIs give the same name do both get a short hash, so no two
    DOIs ever share a file."""
    base = {d: "Paper " + re.sub(r"[^A-Za-z0-9.-]+", "_", d) for d in dois}
    taken = defaultdict(int)
    for name in base.values():
        taken[name.lower()] += 1  # lower case: Windows and macOS file names ignore case
    return {d: n if taken[n.lower()] == 1 else f"{n} {hashlib.sha1(d.encode()).hexdigest()[:6]}"
            for d, n in base.items()}


def note(meta, body):
    """YAML frontmatter (json.dumps gives valid YAML strings) plus Markdown."""
    lines = ["---", "tietrace: true"] + [f"{k}: {json.dumps(v)}" for k, v in meta.items()] + ["---", ""]
    return "\n".join(lines) + body.rstrip() + "\n"


def build(log):
    """Read the newsroom graph and return {file name: note text}."""
    cases = {r[0]: dict(zip(("id", "story", "company", "topic", "expert_ids", "public", "research", "created"), r))
             for r in memory.ro(CASES_Q, {}, log, ROW_CAP)}
    for c in cases.values():
        c["file"] = f"Case - {safe(c['story'])[:40].strip()} ({c['id'][-6:]})"
    checks = defaultdict(list)  # (case id, expert id) -> checks, oldest first
    for cid, kid, eid, label, tier, score, dv, ts, snap, newer in memory.ro(CHECKS_Q, {}, log, ROW_CAP):
        checks[(cid, eid)].append({"id": kid, "label": label, "tier": tier, "score": score, "dv": dv, "ts": ts,
                                   "snap": json.loads(snap), "current": newer is None})
    notes, papers, groups, coauthors = {}, defaultdict(set), defaultdict(set), set()
    paper = paper_names({e["doi"] for ks in checks.values() for k in ks for e in k["snap"]["evidence"]
                         if e["doi"]}).get

    def alias(case, eid, chk):
        # A name only when the case is public (memory stores a name only for its public_ids); else Expert #n.
        return plain(chk["label"]) if case["public"] and chk else f"Expert #{case['expert_ids'].index(eid) + 1}"

    experts = sorted({e for c in cases.values() for e in c["expert_ids"] if memory.EXPERT_RE.fullmatch(e)})
    for eid in experts:
        names, tiers, parts = [], set(), []
        for c in cases.values():
            if eid not in c["expert_ids"]:
                continue
            ks = checks.get((c["id"], eid), [])
            cur = next((k for k in reversed(ks) if k["current"]), None)
            name = alias(c, eid, cur)
            names.append(name)
            parts.append(f"\n## [[{c['file']}|{plain(c['story'])[:60]}]] as {name}")
            if not cur:
                parts.append("- Not checked yet in this case.")
                continue
            tiers.add(cur["tier"])
            s = cur["snap"]
            parts.append(f"- Tier **{cur['tier']}**, score {cur['score']} (context only, not validated), data {cur['dv']}, "
                         f"checked {cur['ts'][:16].replace('T', ' ')} UTC ({cur['id']})")
            if cur["tier"] == "NONE" and s.get("no_tie"):
                n = s["no_tie"]
                parts.append(f"- No tie found in the research record ({n['result']}) within this scope: query "
                             f"`{n['query_name']}`, hops {n['hops']}, years {n['years']}, coverage {n['coverage']}"
                             + (f", funder coverage {n['funder_coverage']}" if n.get("funder_coverage") is not None else "")
                             + (f" (this expert: {n['expert_funder_coverage']})"
                                if n.get("expert_funder_coverage") is not None else ""))
            cited = [e for e in s["evidence"] if e["doi"]]
            if cited:
                parts.append(f"- Ties found in the research record ({len(cited)} evidence items with a DOI):")
            for e in cited:
                group = ("Funder - " if e["kind"] == "FUNDED_BY" else "Org - ") + safe(e["via"])
                groups[group].add(e["kind"])
                papers[e["doi"]].add((e["year"], e["kind"], e["tier"], e["topic_label"], group))
                parts.append(f"  - {e['kind']} [[{paper(e['doi'])}]] {e['year']}, {e['topic_label']}, "
                             f"tier {e['tier']}, via [[{group}]]")
            for e in s["evidence"]:
                if not e["doi"]:
                    parts.append(f"- Record without a DOI (not citable): {e['kind']} work {e['work_id']}, "
                                 f"via {plain(e['via'])}")
            if s["coauth_ties"]:
                parts.append(f"- Tier B links: co-author of {len(s['coauth_ties'])} Tier-A people:")
            for t in s["coauth_ties"]:
                if not memory.EXPERT_RE.fullmatch(str(t["to_expert_id"])):
                    continue  # never a file name from an unexpected id
                coauthors.add(t["to_expert_id"])
                dois = ", ".join(f"[{plain(d)}]({doi_url(d)})" for d in t["dois"])
                parts.append(f"  - [[{t['to_expert_id']}|Tier-A co-author]]: {t['n_papers']} shared papers: {dois}")
            old = [k for k in ks if not k["current"]]
            if old:
                parts.append("- Check history (replaced by a recheck):")
                parts += [f"  - {k['ts'][:16].replace('T', ' ')} UTC: tier {k['tier']}, score {k['score']} (context only), "
                          f"data {k['dv']} ({k['id']})" for k in old]
        names = list(dict.fromkeys(names))
        meta = {"type": "expert", "expert_id": eid, "aliases": names,
                "tags": ["tietrace/expert"] + [f"tier/{t}" for t in sorted(tiers)]}
        notes[eid] = note(meta, f"# {names[0]}\nOpenAlex author `{eid}`. Ties found in the research record, "
                                f"one section per case.\n" + "\n".join(parts))
    for c in cases.values():
        links = []
        for eid in [e for e in c["expert_ids"] if e in experts]:
            cur = next((k for k in reversed(checks.get((c["id"], eid), [])) if k["current"]), None)
            links.append(f"- [[{eid}|{alias(c, eid, cur)}]]: " + (f"tier {cur['tier']}" if cur else "not checked yet"))
        meta = {"type": "case", "case_id": c["id"], "story": c["story"], "company": c["company"], "topic": c["topic"],
                "research_case": c["research"], "public_case": c["public"], "created": c["created"],
                "tags": ["tietrace/case"]}
        notes[c["file"]] = note(meta, f"# {escaped(plain(c['story']))}\nStory (journalist's words): "
                                      f"{escaped(c['story'])}\n\nCompany: {escaped(c['company'])}, "
                                      f"topic: {escaped(c['topic'])}, research graph: research:{c['research']}\n\n"
                                      "## Experts\n" + "\n".join(links))
    for doi, uses in papers.items():
        year = min((u[0] for u in uses if u[0] is not None), default=None)
        lines = [f"- {k}, tier {t}, {lbl}, via [[{g}]]" for _, k, t, lbl, g in sorted(uses, key=str)]
        notes[paper(doi)] = note({"type": "paper", "doi": doi, "year": year, "tags": ["tietrace/paper"]},
                                 f"# {escaped(doi)}\n[{plain(doi)}]({doi_url(doi)}), year {year}\n\n"
                                 "## Evidence in the research record\n" + "\n".join(lines))
    for g, kinds in groups.items():
        kind = "funder" if g.startswith("Funder") else "org"
        notes[g] = note({"type": kind, "tags": [f"tietrace/{kind}"]},
                        f"# {g}\nNamed in the research record as {', '.join(sorted(kinds))}.")
    for t in sorted(coauthors - set(experts)):
        notes[t] = note({"type": "tier-a-coauthor", "expert_id": t, "aliases": ["Tier-A co-author"],
                         "tags": ["tietrace/tier-a"]}, "# Tier-A co-author\nA Tier-A person in the research "
                                                       "record (no name shown). See backlinks for who links here.")
    counts = memory.ro(COUNTS_Q, {}, log, ROW_CAP)
    lines = [f"- {kind}: {n}" for kind, n in counts] or ["- (this newsroom has no memory yet)"]
    lines.append(f"- notes: {len(notes)} ({len(papers)} papers, {len(groups)} funders/orgs)")
    lines.append(f"\nlast_export: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    notes["TieTrace index"] = note({"type": "index", "newsroom": memory.GRAPH[9:]},
                                   f"# TieTrace {memory.GRAPH}\n" + "\n".join(lines))
    return notes


WARNED = set()


def warn(message):
    """Print a warning once per run (the watch loop would repeat it every few seconds)."""
    if message not in WARNED:
        WARNED.add(message)
        print("warning: " + message, file=sys.stderr, flush=True)


def strip_time(text):
    return re.sub(r"^last_export: .*$", "", text, flags=re.M)


def owned(path):
    """True only for a plain file (not a link or folder) that starts with the exporter's marker."""
    if path.is_symlink() or not path.is_file():
        return False
    with open(path, "rb") as f:
        return f.read(len(MARKER)) == MARKER.encode()


def export(vault, newsroom):
    """One export. Returns (written, removed). Reads everything first, so a failed read changes no file."""
    vault = Path(vault)
    if not (vault / ".obsidian").is_dir():
        raise SystemExit(f"{vault} is not an Obsidian vault (no .obsidian/ folder); nothing written")
    saved = memory.GRAPH, memory.SESSION_ID
    try:
        memory.use_newsroom(newsroom)  # validates the id; no database write
        notes = build([])
    finally:
        memory.GRAPH, memory.SESSION_ID = saved  # leave the memory module as it was (it may serve tools)
    folder = vault / "TieTrace" / newsroom
    for d in (vault / "TieTrace", folder):  # a linked folder would put every note (and removal) outside the vault
        if d.is_symlink() or d.is_junction():
            raise SystemExit(f"{d} is a link (symlink or junction); nothing written")
    folder.mkdir(parents=True, exist_ok=True)
    for tmp in folder.glob(".*.tmp"):  # left by an export that was killed mid-write (empty or marked only)
        if tmp.is_file() and not tmp.is_symlink() and (tmp.stat().st_size == 0 or owned(tmp)):
            tmp.unlink()
    readme = folder / "TieTrace README.md"
    if not readme.exists() and not readme.is_symlink():
        readme.write_text(README, encoding="utf-8")
    written, removed = 0, 0
    index = notes.pop("TieTrace index")
    for name, text in sorted(notes.items()) + [("TieTrace index", index)]:  # index last: it records the time
        path = folder / f"{name}.md"
        if path.exists() or path.is_symlink():
            if not owned(path):
                warn(f"{path} is not a TieTrace note (no `tietrace: true` marker); left as it is")
                continue
            old = path.read_bytes().decode("utf-8", "replace")
            if strip_time(old) == strip_time(text) and not (name == "TieTrace index" and written):
                continue
        tmp = folder / f".{name}.tmp"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)  # Obsidian never sees a half-written note
        written += 1
    stale = [p for p in folder.glob("*.md") if p.stem not in notes and p.stem != "TieTrace index" and owned(p)]
    if stale and not notes and len(stale) > 10:
        warn(f"newsroom:{newsroom} has no cases but {len(stale)} TieTrace notes exist; not removing them")
        return written, removed
    trash = vault / ".trash"  # Obsidian's own trash folder: a removed note can be restored
    if stale and (trash.is_symlink() or trash.is_junction() or (trash.exists() and not trash.is_dir())):
        warn(f"{trash} is a link or not a folder; old TieTrace notes are not removed")
        return written, removed
    for p in stale:
        trash.mkdir(exist_ok=True)
        for n in range(1000):
            dest = trash / (p.name if n == 0 else f"{p.stem} {n}.md")
            try:
                open(dest, "x", encoding="utf-8").close()  # claims the name: fails if any file already has it
                break
            except FileExistsError:
                continue
        else:
            warn(f"no free name in {trash} for {p.name}; left in place")
            continue
        os.replace(p, dest)  # replaces only the empty file claimed above
        removed += 1
    return written, removed


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--vault", default=str(DEFAULT_VAULT))
    p.add_argument("--newsroom", default=os.environ.get("TIETRACE_NEWSROOM", "alpha"))
    p.add_argument("--watch", type=float, metavar="SECONDS", help="export again every SECONDS until Ctrl-C")
    a = p.parse_args()
    if a.watch is not None and not a.watch > 0:
        p.error("--watch needs a number of seconds above 0 (leave it out to export once)")
    if not memory.NEWSROOM_RE.fullmatch(a.newsroom):
        raise SystemExit("newsroom id: lower case letters, digits, - or _, 1 to 40 characters")
    folder = Path(a.vault) / "TieTrace" / a.newsroom
    if hasattr(signal, "SIGBREAK"):  # Windows: Ctrl-Break (and CTRL_BREAK_EVENT) stops the loop like Ctrl-C
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    try:
        while True:
            try:
                w, r = export(a.vault, a.newsroom)
                if w or r or not a.watch:
                    print(f"{time.strftime('%H:%M:%S')} {w} notes written, {r} removed in {folder}", flush=True)
            except (memory.ToolError, OSError, UnicodeDecodeError) as e:
                why = f"{e.code}: {e.message}" if isinstance(e, memory.ToolError) else f"{type(e).__name__}: {e}"
                if not a.watch:
                    raise SystemExit(why)
                print(f"{time.strftime('%H:%M:%S')} {why} (retrying)", flush=True)
            if not a.watch:
                return
            time.sleep(a.watch)
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    sys.exit(main())
