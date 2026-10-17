"""Checkpoint 5e: discovery CLI. Finds OpenAlex candidate IDs for a new case. No LLM, not an MCP tool.

Run:  uv run python discover.py funders "philip morris"
      uv run python discover.py institutions "philip morris"
      uv run python discover.py topics "lung cancer screening"
      uv run python discover.py authors "Maria Garcia" --topic T10202
Add --json to print the full candidate cards, --case <slug> to save them to
cases/<slug>/candidates_<kind>_<query>.json (one file per search, so earlier searches are kept).

It only lists candidates. It never picks one: Claude Code proposes, a human approves (IMPROVEMENT.md D2).
The API key is read from .env by loader.api_key() and sent by loader.get(); it is never printed.
Cost per run: one search call (~$0.001) plus a few list calls ($0.0001 each); see "cost_usd" in the output.
"""
import argparse
import contextlib
import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import loader
from loader import norm_name, short

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
TOPIC_RE = re.compile(r"^T\d+$")
CASES = Path(__file__).parent / "cases"
PER_PAGE = 10      # candidates shown per search
COVERAGE_MAX = 5   # funder-coverage counts (2 list calls each) only for the first 5 name matches
COVERAGE_NOTE = ("approximate: the denominator is every OpenAlex work of the profile, "
                 "including peer-review reports and errata")
cost = [0.0]       # sum of meta.cost_usd reported by OpenAlex this run


def get(path, params):
    r = loader.get(path, params)
    cost[0] += r["meta"].get("cost_usd") or 0
    return r


def count(filter_):
    """Number of works matching a filter (one list call, one row fetched)."""
    return get("works", {"filter": filter_, "per_page": 1, "select": "id"})["meta"]["count"]


def plain(q):
    """Remove the characters that have a meaning inside an OpenAlex filter value (, | ")."""
    return " ".join(re.sub(r'[,|"]', " ", q).split())


def funders(q):
    r = get("funders", {"search": q, "per_page": PER_PAGE,
                        "select": "id,display_name,country_code,description,works_count,ids,roles"})
    cards = [{"id": short(f["id"]), "name": f["display_name"], "type": "funder",
              "country": f.get("country_code"), "ror": (f.get("ids") or {}).get("ror"),
              "works_count": f.get("works_count"), "description": f.get("description"),
              # roles = the same organisation as institution/publisher; funders have no parents
              "roles": [{"role": x["role"], "id": short(x["id"])} for x in f.get("roles") or []]}
             for f in r["results"]]
    return {"total_hits": r["meta"]["count"], "candidates": cards}


def institutions(q):
    r = get("institutions", {"search": q, "per_page": PER_PAGE,
                             "select": "id,display_name,type,country_code,ror,works_count,lineage,associated_institutions"})
    cards = []
    for i in r["results"]:
        assoc = i.get("associated_institutions") or []
        rel = lambda kind: [{"id": short(a["id"]), "name": a["display_name"]} for a in assoc if a.get("relationship") == kind]
        cards.append({"id": short(i["id"]), "name": i["display_name"], "type": i.get("type"),
                      "country": i.get("country_code"), "ror": i.get("ror"), "works_count": i.get("works_count"),
                      "lineage": [short(x) for x in i.get("lineage") or []],
                      "parents": rel("parent"), "children": rel("child")})
    return {"total_hits": r["meta"]["count"], "candidates": cards}


def topics(q):
    t = get("topics", {"search": q, "per_page": PER_PAGE,
                       "select": "id,display_name,description,works_count,subfield,keywords"})
    k = get("keywords", {"search": q, "per_page": PER_PAGE, "select": "id,display_name,works_count"})
    phrase = plain(q)
    cards = [{"id": short(x["id"]), "name": x["display_name"], "type": "topic", "works_count": x.get("works_count"),
              "subfield": (x.get("subfield") or {}).get("display_name"), "description": x.get("description"),
              "keywords": [kw["display_name"] for kw in (x.get("keywords") or [])[:8]]} for x in t["results"]]
    return {"total_hits": t["meta"]["count"], "candidates": cards,
            "keywords": [{"id": short(x["id"]), "name": x["display_name"], "works_count": x.get("works_count")}
                         for x in k["results"]],
            # works whose title or abstract contains the phrase: much narrower than a topic ID
            "phrase": phrase, "phrase_works": count(f'title_and_abstract.search:"{phrase}"')}


def words(name):
    """Name -> list of plain words. Every Unicode punctuation mark (‐ ’ ' . -) becomes a space first,
    so a name pasted from a web page and the OpenAlex name normalise the same way."""
    spaced = "".join(" " if unicodedata.category(ch).startswith("P") else ch for ch in name or "")
    return norm_name(spaced).split()


def word_fits(q, n):
    """Same word, or one of the two is an initial of the other ("y" fits "yoni")."""
    return q == n or (len(q) == 1 and n.startswith(q)) or (len(n) == 1 and q.startswith(n))


def name_match(query, author):
    """True if every word of the query fits a word of the profile's name or of one of its alternative names."""
    want = words(query)
    names = [author.get("display_name")] + (author.get("display_name_alternatives") or [])
    return bool(want) and any(all(any(word_fits(q, n) for n in words(name)) for q in want) for name in names)


def share(part, whole):
    """part / whole, rounded, capped at 1: a filter count can run ahead of a profile's stored works_count."""
    return round(min(1, part / whole), 3) if whole else None


def topic_overlap(author_id, works_count, topic_ids):
    """Works of the profile in any of the chosen topics: one count call (a work in two topics counts once)."""
    works = count(f"authorships.author.id:{author_id},topics.id:{'|'.join(topic_ids)}")
    return {"topics": list(topic_ids), "works": works, "share": share(works, works_count)}


def author_status(total_hits, cards, siblings=(), sibling_truncated=False):
    """not_found: no profile at all. unique: exactly one name match, every hit seen, no sibling
    profile (a likely split of the same person) and the sibling search fully read. Otherwise ambiguous."""
    if total_hits == 0 and not siblings:
        return "not_found"
    matches = sum(c["name_match"] for c in cards)
    alone = not siblings and not sibling_truncated
    return "unique" if matches == 1 and total_hits <= len(cards) and alone else "ambiguous"


TITLES = {"dr", "prof", "professor", "mr", "mrs", "ms"}


def clean_name(q):
    """'Freedhoff, Yoni' -> 'Yoni Freedhoff'; 'Dr. Yoni Freedhoff' -> 'Yoni Freedhoff' (titles dropped)."""
    if q.count(",") == 1:
        surname, given = q.split(",")
        q = f"{given} {surname}"
    return " ".join(w for w in q.split() if w.lower().rstrip(".") not in TITLES)


def initial_form(q):
    """'Yoni S. Freedhoff' -> 'Y. Freedhoff' (finds split profiles listed under initials); None for one word."""
    w = q.split()
    return f"{w[0][0]}. {w[-1]}" if len(w) > 1 else None


def sibling_strength(q, author):
    """strong: same surname and same first name, or the name is exactly the query's initial form
    ('Y. Freedhoff' for 'Yoni Freedhoff'). weak: only fits by initials ('Keith Brownell' for 'Kelly Brownell')."""
    qw, pw = words(q), words(author.get("display_name"))
    if pw and pw[-1] == qw[-1] and (pw[0] == qw[0] or pw == words(initial_form(q) or "")):
        return "strong"
    # alternative names count only with the full first name: OpenAlex adds "K. Brownell" to every K. Brownell
    for name in author.get("display_name_alternatives") or []:
        aw = words(name)
        if aw and aw[0] == qw[0] and aw[-1] == qw[-1]:
            return "strong"
    return "weak"


AUTHOR_FIELDS = "id,display_name,display_name_alternatives,orcid,works_count,last_known_institutions,topics"


def author_card(q, a):
    inst = (a.get("last_known_institutions") or [{}])[0]
    return {"id": short(a["id"]), "name": a["display_name"], "type": "author", "orcid": a.get("orcid"),
            "works_count": a.get("works_count"), "name_match": name_match(q, a),
            "last_institution": inst.get("display_name"), "last_institution_id": short(inst.get("id")),
            "country": inst.get("country_code"),
            "top_topics": [t["display_name"] for t in (a.get("topics") or [])[:3]],
            "merged_profile_risk": (a.get("works_count") or 0) > loader.WORKS_COUNT_ANOMALY,
            "funder_coverage": None}


def authors(query, topic_ids=()):
    q = clean_name(query)
    r = get("authors", {"search": q, "per_page": PER_PAGE, "select": AUTHOR_FIELDS})
    cards = [author_card(q, a) for a in r["results"]]
    # Second search under the initial ("Y. Freedhoff"): name-compatible profiles not in the first page
    # are shown as siblings, because a split profile hides ties (D2).
    siblings, alt, truncated = [], initial_form(q), False
    if alt and words(alt) != words(q):
        seen = {c["id"] for c in cards}
        s = get("authors", {"search": alt, "per_page": PER_PAGE, "select": AUTHOR_FIELDS})
        truncated = s["meta"]["count"] > PER_PAGE  # only the first page was read
        siblings = [{**author_card(q, a), "match": sibling_strength(q, a)}
                    for a in s["results"] if short(a["id"]) not in seen and name_match(q, a)]
        siblings.sort(key=lambda c: c["match"] != "strong")  # strong first, search order kept within each
    if topic_ids:
        for c in cards:
            c["topic_overlap"] = topic_overlap(c["id"], c["works_count"], topic_ids)
    for c in [c for c in cards if c["name_match"]][:COVERAGE_MAX]:
        # share of this profile's works that carry any funder record: the denominator for a "no tie" claim
        every = count(f"authorships.author.id:{c['id']}")
        funded = count(f"authorships.author.id:{c['id']},funders.id:!null")
        c["funder_coverage"] = {"works": every, "with_funder": funded, "share": share(funded, every)}
    return {"name_searched": q, "total_hits": r["meta"]["count"],
            "status": author_status(r["meta"]["count"], cards, siblings, truncated),
            "topics": list(topic_ids), "candidates": cards, "siblings": siblings,
            "sibling_search": alt, "sibling_search_truncated": truncated, "funder_coverage_note": COVERAGE_NOTE}


def query_slug(q):
    """'Yoni S. Freedhoff' -> 'yoni-s-freedhoff'; a name with no ASCII letters gets a short hash instead."""
    return "-".join(words(q))[:60] or hashlib.sha1(q.encode()).hexdigest()[:10]


def table(result):
    lines = [f"{result['kind']} '{result['query']}': {result['total_hits']} hits, showing {len(result['candidates'])}"
             + (f", status {result['status']}" if "status" in result else "")]
    for c in result["candidates"]:
        extra = c.get("last_institution") or c.get("country") or c.get("subfield") or ""
        if "funder_coverage" in c and c["funder_coverage"]:
            extra += f" | funder share ~{c['funder_coverage']['share']}"
        if "topic_overlap" in c:
            extra += f" | topic works {c['topic_overlap']['works']}"
        flag = "" if c.get("name_match", True) else " (name differs)"
        lines.append(f"  {c['id']:<14} {(c['name'] or '')[:45]:<45} {c.get('type') or '':<10} works={c.get('works_count')}  {extra}{flag}")
    for c in result.get("siblings") or []:
        lines.append(f"  sibling? {c['match']:<6} {c['id']:<14} {(c['name'] or '')[:45]:<45} works={c.get('works_count')}  "
                     f"{c.get('last_institution') or ''}  (found by '{result['sibling_search']}')")
    if result.get("sibling_search_truncated"):
        lines.append(f"  sibling search '{result['sibling_search']}' has more than {PER_PAGE} hits: only the first page was read")
    if "funder_coverage_note" in result:
        lines.append(f"  funder share is {result['funder_coverage_note']}")
    if "keywords" in result:
        lines.append("  keywords: " + "; ".join(f"{k['name']} ({k['works_count']})" for k in result["keywords"]))
        lines.append(f"  works with phrase \"{result['phrase']}\" in title/abstract: {result['phrase_works']}")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="List OpenAlex candidates for a new TieTrace case (never auto-picks).")
    p.add_argument("kind", choices=["funders", "institutions", "topics", "authors"])
    p.add_argument("query")
    p.add_argument("--topic", action="append", default=[], help="topic ID for author overlap, e.g. T10202 (repeatable)")
    p.add_argument("--case", help="save the candidates to cases/<slug>/candidates_<kind>_<query>.json")
    p.add_argument("--json", action="store_true", help="print the full JSON instead of the table")
    args = p.parse_args(argv)
    if args.case and not SLUG_RE.match(args.case):
        p.error(f"bad case slug {args.case!r}: must match {SLUG_RE.pattern}")
    topic_ids = [short(t) for t in args.topic]
    if any(not TOPIC_RE.match(t) for t in topic_ids):
        p.error(f"bad topic ID in {args.topic}: expected T<digits>")
    if topic_ids and args.kind != "authors":
        p.error("--topic only works with the authors subcommand")
    if not re.search(r"\w", plain(clean_name(args.query) if args.kind == "authors" else args.query)):  # e.g. ", |": would search for "" and report a false 0
        p.error(f"query {args.query!r} has no letters or digits")

    loader.KEY = loader.api_key()
    cost[0], calls_before = 0.0, loader.api_calls
    # loader.get prints retry messages to stdout; send them to stderr so --json output stays valid JSON
    with contextlib.redirect_stdout(sys.stderr):
        if args.kind == "authors":
            found = authors(args.query, topic_ids)
        else:
            found = {"funders": funders, "institutions": institutions, "topics": topics}[args.kind](args.query)
    result = {"kind": args.kind, "query": args.query,
              "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              **found, "api_calls": loader.api_calls - calls_before, "cost_usd": round(cost[0], 5)}
    if args.case:  # save before printing: a console that cannot show a name must not lose the paid-for result
        out = CASES / args.case / f"candidates_{args.kind}_{query_slug(args.query)}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"saved {out}", file=sys.stderr)
    print(json.dumps(result, indent=1, ensure_ascii=False) if args.json else table(result))
    print(f"API calls: {result['api_calls']}, cost ${result['cost_usd']}", file=sys.stderr)
    return result


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # a Windows console (cp1252) cannot print a name like 张
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    main()
