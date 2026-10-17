"""Phase 2, P2-3: corporate-family candidates for a case. No LLM, not an MCP tool. Written by Aaryan, Oct 17 2026.

Run:  uv run python family.py propose --case <slug> --seed-ror 00yc8qw13      (or --seed-wikidata Q3295867)
      uv run python family.py review  --case <slug>
      uv run python family.py set     --case <slug> --row 3 --status approved --rel subsidiary
      uv run python family.py add     --case <slug> --name "Some trade group" --note "why it might matter"
      uv run python family.py apply   --case <slug> --by Aaryan

propose resolves the seed by ID (never by name) and writes cases/<slug>/candidates.json, every row
status "proposed" (or "same_name_collision" / "needs_human_source"). Sources: ROR relationships,
Wikidata P355/P749/P127 (organisations only), GLEIF direct children plus a same-name check, and SEC
Exhibit 21 only when env SEC_USER_AGENT is set (SEC wants a contact there; we never put anyone's email in
the code). A human sets each row to approved or rejected; apply copies only approved rows that have an OpenAlex id into case.json, so loader.py is unchanged.
"""
import argparse
import html
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import httpx

import loader
import verifier
from loader import norm_name, short

CASES = Path(__file__).parent / "cases"
UA = "TieTrace-family/0.1 (FalkorDB Graph Hacks research tool; corporate-family lookup)"  # no personal email
ROR_API, GLEIF_API = "https://api.ror.org/v2/organizations/", "https://api.gleif.org/api/v1/lei-records"
SEC_SUBMISSIONS, SEC_ARCHIVE = "https://data.sec.gov/submissions/CIK", "https://www.sec.gov/Archives/edgar/data/"
WD_ENTITY, WD_SPARQL = "https://www.wikidata.org/wiki/Special:EntityData/", "https://query.wikidata.org/sparql"
LEGAL = {"inc", "incorporated", "ltd", "limited", "sa", "gmbh", "ag", "plc", "llc", "corp", "corporation", "co",
         "company", "pty", "pvt", "private", "nv", "bv", "ab", "as", "spa", "srl", "sas", "kk", "lp", "llp", "publ"}
FOUNDATION_WORDS = {"foundation", "trust", "institute", "fund"}  # a funding vehicle keeps its own kind (D4)
SHAREHOLDER = "shareholder (P127 owned-by), not corporate family"  # approving one needs --source (round 2)
STAKE = "owned-by (P127), size of stake unknown"  # a P127-only child can be a minority stake (Monster ~19%)
STATUSES = {"proposed", "approved", "rejected", "needs_human_source", "same_name_collision"}

# Children and parents of the seed. The class filter keeps only items that are an instance of
# (a subclass of) organization Q43229, so brands, patents, drinks and buildings drop out.
SPARQL = """SELECT ?item ?itemLabel ?dir ?prop ?classLabel ?ror ?lei ?isOrg WHERE {
  { SELECT ?item ?dir ?prop WHERE {
      VALUES ?seed { wd:%s }
      { ?seed wdt:P355 ?item BIND("child" AS ?dir) BIND("P355" AS ?prop) }
      UNION { ?item wdt:P749 ?seed BIND("child" AS ?dir) BIND("P749" AS ?prop) }
      UNION { ?item wdt:P127 ?seed BIND("child" AS ?dir) BIND("P127" AS ?prop) }
      UNION { ?seed wdt:P749 ?item BIND("parent" AS ?dir) BIND("P749" AS ?prop) }
      UNION { ?seed wdt:P127 ?item BIND("parent" AS ?dir) BIND("P127" AS ?prop) } } }
  OPTIONAL { ?item wdt:P31 ?class }
  OPTIONAL { ?item wdt:P6782 ?ror }
  OPTIONAL { ?item wdt:P1278 ?lei }
  BIND(EXISTS { ?item wdt:P31 ?c . ?c wdt:P279* wd:Q43229 } AS ?isOrg)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }"""


def http_json(url, params=None):
    """One GET to ROR, Wikidata or GLEIF (no key). Tests replace this function with recorded replies."""
    r = httpx.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json, application/sparql-results+json"},
                  timeout=90)
    if r.status_code == 429:  # Wikidata limits hard during outages (1 request per minute on Oct 17)
        wait = r.headers.get("retry-after", "60")
        raise SystemExit(f"{url.split('/')[2]} says 429 (rate limit): wait {wait} s before running again "
                         "(retrying sooner can restart the wait)")
    r.raise_for_status()
    return r.json()


def sec_get(url):
    """One GET to SEC EDGAR, as text. SEC asks for a contact in the User-Agent, so it comes from env
    SEC_USER_AGENT (set by the person running this); without it the SEC source is skipped. Tests replace this."""
    r = httpx.get(url, headers={"User-Agent": os.environ["SEC_USER_AGENT"]}, timeout=60)
    r.raise_for_status()
    return r.text


def openalex(path, params):
    """One OpenAlex GET through loader.get (key sent, never printed). Tests replace this too."""
    return loader.get(path, params)


def norm(name):
    """'The Coca-Cola Company (Australia)' -> 'coca cola'; 'Nestlé S.A.' -> 'nestle'."""
    name = re.sub(r"(\s*\([^)]*\))+\s*$", "", name or "").replace(".", "")
    words = norm_name(name).split()
    while words and words[-1] in LEGAL:
        words.pop()
    return " ".join(words[1:] if words[:1] == ["the"] else words)


def match_type(name, seed_name, id_linked):
    if id_linked:
        return "id"
    a, b = set(norm(name).split()), set(norm(seed_name).split())
    if norm(name) == norm(seed_name):
        return "exact_norm"
    return "fuzzy" if a and b and (b <= a or a <= b) else "none"


def guess_kind(name):
    return "foundation" if FOUNDATION_WORDS & set(norm_name(name).split()) else "company"


def quote(url, text):
    """The only way a source's own description enters a row: as a quoted phrase with its URL."""
    return f'described in {url} as "{text}"'


def row(id_source, source_id, name, seed_name, rel, evidence, url, status="proposed", ror=None, lei=None, linked=True):
    """An institution row; map_openalex turns some into funder rows, and only those get a kind."""
    return {"id_source": id_source, "source_id": source_id, "name": name, "norm_name": norm(name),
            "match_type": match_type(name, seed_name, linked), "entity": "institution",
            "rel_suggested": rel, "rel": None, "kind": None, "human_source": None, "flag": None,
            "source_url": url, "evidence": evidence, "ror": ror, "lei": lei,
            "openalex_id": None, "openalex_name": None, "status": status, "reason": None}


def mark_shareholder(r):
    """Status and reason a human may change with set; the flag field set never writes, and apply checks it."""
    r.update(status="needs_human_source", reason=SHAREHOLDER, flag="shareholder")


def resolve_seed(ror=None, qid=None):
    """Seed by ID only. Returns name, ror, qid, lei, cik (SEC) and the ROR record (or None)."""
    rec = http_json(ROR_API + ror) if ror else None
    if rec and not qid:
        qid = next((x["preferred"] or x["all"][0] for x in rec["external_ids"] if x["type"] == "wikidata"), None)
    name, lei, cik = None, None, None
    if qid:
        ent = http_json(f"{WD_ENTITY}{qid}.json")["entities"]
        e = next(iter(ent.values()))
        claim = lambda p: next((c["mainsnak"]["datavalue"]["value"] for c in e["claims"].get(p, [])), None)
        name, lei, ror = e["labels"].get("en", {}).get("value"), claim("P1278"), ror or claim("P6782")
        cik = claim("P5531")  # SEC Central Index Key
        if ror and not rec:
            rec = http_json(ROR_API + ror)
    if rec and not name:
        name = next(n["value"] for n in rec["names"] if "ror_display" in n["types"])
    return {"name": name, "ror": ror, "qid": qid, "lei": lei, "cik": cik, "ror_record": rec}


def from_ror(seed):
    rec, out = seed["ror_record"], []
    for r in (rec or {}).get("relationships", []):
        rid = short(r["id"])
        rel = "parent" if r["type"] == "parent" else "subsidiary" if r["type"] == "child" else None
        out.append(row("ror", rid, r["label"], seed["name"], rel, f"ROR {seed['ror']} lists {rid} as {r['type']}",
                       f"https://ror.org/{seed['ror']}", ror=rid))
    return out


def wikidata_items(qid):
    """Raw SPARQL rows grouped by item: {qid: {name, dirs, props, classes, ror, lei, is_org}}."""
    items = {}
    for b in http_json(WD_SPARQL, {"query": SPARQL % qid, "format": "json"})["results"]["bindings"]:
        v = lambda k: b.get(k, {}).get("value")
        it = items.setdefault(short(v("item")), {"name": v("itemLabel"), "dirs": set(), "props": set(), "classes": set(),
                                                 "ror": None, "lei": None, "is_org": v("isOrg") == "true"})
        it["dirs"].add(v("dir")), it["props"].add(v("prop"))
        it["classes"].add(v("classLabel") or "?")
        it["ror"], it["lei"] = it["ror"] or v("ror"), it["lei"] or v("lei")
    return items


def from_wikidata(seed, items):
    out = []
    for q, it in items.items():
        if not it["is_org"]:
            continue  # brand, patent, product, place: not an organisation
        props = ",".join(sorted(it["props"]))
        shareholder = False
        if "child" in it["dirs"]:
            rel = "bottler" if "bottl" in it["name"].lower() else "subsidiary"
            ev = f"Wikidata: {q} linked to {seed['qid']} as subsidiary/owned ({props}); classes {sorted(it['classes'])}"
            if it["props"] == {"P127"}:
                ev += f"; {STAKE}"
        else:  # owner of the seed
            rel = "parent" if "P749" in it["props"] else None
            ev = f"Wikidata: {seed['qid']} is owned by / part of {q} ({props}); classes {sorted(it['classes'])}"
            if "P749" not in it["props"]:  # P127 only: a shareholder (BlackRock, Vanguard), not a parent
                shareholder = True
        r = row("wikidata", q, it["name"], seed["name"], rel, ev, f"https://www.wikidata.org/wiki/{q}",
                ror=it["ror"], lei=it["lei"])
        if shareholder:
            mark_shareholder(r)
        out.append(r)
    return out


def from_gleif(seed, warnings):
    out, seen = [], {seed["lei"]}
    if not seed["lei"]:
        warnings.append("GLEIF skipped: the seed has no LEI on Wikidata")
        return out
    page = http_json(f"{GLEIF_API}/{seed['lei']}/direct-children", {"page[size]": 100})
    if page["meta"]["pagination"]["total"] > 100:
        warnings.append(f"GLEIF: only the first 100 of {page['meta']['pagination']['total']} children read")
    for r in page["data"]:
        seen.add(r["id"])
        name = r["attributes"]["entity"]["legalName"]["name"]
        rel = "bottler" if "bottl" in name.lower() else "subsidiary"
        out.append(row("gleif", r["id"], name, seed["name"], rel, f"GLEIF: {r['id']} is a direct child of {seed['lei']}",
                       f"https://search.gleif.org/#/record/{r['id']}", lei=r["id"]))
    # Same-name check: an LEI with the seed's name but no parent-child link is a collision, never suggested.
    for r in http_json(GLEIF_API, {"filter[entity.legalName]": seed["name"], "page[size]": 50})["data"]:
        name = r["attributes"]["entity"]["legalName"]["name"]
        mt = match_type(name, seed["name"], False)
        if r["id"] in seen or mt == "none":
            continue
        country = r["attributes"]["entity"]["legalAddress"]["country"]
        status = "same_name_collision" if mt == "exact_norm" else "needs_human_source"
        out.append(row("gleif-name", r["id"], name, seed["name"], None,
                       f"GLEIF name search only ({country}); no ID link to {seed['lei']}",
                       f"https://search.gleif.org/#/record/{r['id']}", status=status, lei=r["id"], linked=False))
    return out


def exhibit21_names(page):
    """First cell of each table row in an Exhibit 21 page, tags and entities removed."""
    # ponytail: first-<td> heuristic; a plain-text (no table) Exhibit 21 gives 0 names and a warning
    names = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        cell = re.search(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)
        name = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell.group(1))).split()) if cell else ""
        if name and not re.match(r"(name|subsidiar\w*|entity)\b", name, re.I) and name not in names:
            names.append(name)
    return names


def from_sec(seed, warnings):
    """Subsidiaries listed in Exhibit 21 of the seed's latest 10-K. The entries carry no IDs, so they map to
    no OpenAlex id by themselves; a human must add one with --openalex-id."""
    if not os.environ.get("SEC_USER_AGENT"):
        warnings.append("SEC Exhibit 21 skipped: env SEC_USER_AGENT is not set (SEC asks for a contact in it)")
        return []
    if not seed["cik"]:
        warnings.append("SEC Exhibit 21 skipped: the seed has no SEC CIK (P5531) on Wikidata")
        return []
    cik = int(seed["cik"])
    recent = json.loads(sec_get(f"{SEC_SUBMISSIONS}{cik:010d}.json"))["filings"]["recent"]
    if "10-K" not in recent["form"]:
        warnings.append(f"SEC Exhibit 21 skipped: no recent 10-K for CIK {cik}")
        return []
    acc = recent["accessionNumber"][recent["form"].index("10-K")]  # newest first
    folder = f"{SEC_ARCHIVE}{cik}/{acc.replace('-', '')}/"
    files = [f["name"] for f in json.loads(sec_get(folder + "index.json"))["directory"]["item"]]
    ex21 = next((f for f in files if re.search(r"ex-?21", f, re.I)), None)
    if not ex21:
        warnings.append(f"SEC Exhibit 21 skipped: no Exhibit 21 file in 10-K {acc}")
        return []
    names = exhibit21_names(sec_get(folder + ex21))
    if not names:
        warnings.append(f"SEC Exhibit 21: no table rows read from {folder + ex21}")
    return [row("sec-ex21", acc, n, seed["name"], "bottler" if "bottl" in n.lower() else "subsidiary",
                f"SEC: Exhibit 21 of 10-K {acc} (CIK {cik}) lists {n!r}", folder + ex21) for n in names]


def map_openalex(rows, seed_name):
    """ROR -> OpenAlex institution id; every OpenAlex funder with that ROR becomes its own funder row (D4:
    the Coca-Cola company and foundation funders share one ROR but are different funders)."""
    funders = []
    for r in [r for r in rows if r["ror"]]:
        hits = openalex("institutions", {"filter": f"ror:{r['ror']}", "select": "id,display_name"})["results"]
        if hits:
            r["openalex_id"], r["openalex_name"] = short(hits[0]["id"]), hits[0]["display_name"]
        for f in openalex("funders", {"filter": f"ror:{r['ror']}", "select": "id,display_name,description"})["results"]:
            fid = short(f["id"])
            if any(x["openalex_id"] == fid for x in funders):
                if r["flag"] == "shareholder":  # also reached through a shareholder's ROR: the careful mark wins
                    mark_shareholder(next(x for x in funders if x["openalex_id"] == fid))
                continue
            ev = f"OpenAlex funder {fid} has ROR {r['ror']} (the ROR of row {r['name']!r}, from {r['id_source']})"
            if f.get("description"):
                ev += "; " + quote(f"https://api.openalex.org/funders/{fid}", f["description"])
            fr = row("openalex-funder", fid, f["display_name"], seed_name, None, ev,
                     f"https://api.openalex.org/funders/{fid}", ror=r["ror"])
            fr.update(entity="funder", openalex_id=fid, openalex_name=f["display_name"], kind=guess_kind(f["display_name"]))
            if r["flag"] == "shareholder":  # a shareholder's funder is a shareholder too
                mark_shareholder(fr)
            funders.append(fr)
    merged = {}
    for r in rows + funders:  # one row per OpenAlex id; the evidence of each source is kept
        key = (r["entity"], r["openalex_id"]) if r["openalex_id"] else id(r)
        if key in merged:
            merged[key]["evidence"] += " | " + r["evidence"]
            if r["flag"] == "shareholder":  # the more careful status wins
                mark_shareholder(merged[key])
        else:
            merged[key] = r
    return list(merged.values())


def own_text_problems(r):
    """Banned words in TieTrace's own labels. Quoted source phrases are the source's words, not ours."""
    own = re.sub(r'"[^"]*"', "", r["evidence"] or "")
    own += " " + " ".join(str(r[k] or "") for k in ("match_type", "rel_suggested", "rel", "kind", "status", "reason"))
    return verifier.text_problems(own)


def check_text(r):
    """A plain raise, not an assert: asserts vanish under python -O."""
    if own_text_problems(r):
        raise SystemExit(f"row {r['name']!r}: {own_text_problems(r)}")


def propose(slug, ror=None, qid=None, force=False, skip_wikidata=False):
    path = CASES / slug / "candidates.json"
    if path.exists() and not force:  # it may hold human decisions
        raise SystemExit(f"{path} exists (it may hold approved/rejected rows); add --force to replace it")
    warnings = []
    seed = resolve_seed(ror, qid)
    if not seed["name"]:
        raise SystemExit("seed not found by ID")
    me = row("seed", seed["ror"] or seed["qid"], seed["name"], seed["name"], "parent",
             f"seed, given by ID: ROR {seed['ror']}, Wikidata {seed['qid']}, LEI {seed['lei']}",
             f"https://ror.org/{seed['ror']}" if seed["ror"] else f"https://www.wikidata.org/wiki/{seed['qid']}",
             ror=seed["ror"], lei=seed["lei"])
    me["rel_suggested"] = "parent"
    rows, counts = [me], {}
    rows += (r := from_ror(seed)); counts["ror"] = len(r)
    if skip_wikidata:  # query.wikidata.org rate-limits hard; the other sources still run
        warnings.append("Wikidata subsidiaries/owners skipped (--skip-wikidata): no shareholder rows; re-run later without it")
    items = wikidata_items(seed["qid"]) if seed["qid"] and not skip_wikidata else {}
    rows += (r := from_wikidata(seed, items)); counts["wikidata_raw_items"], counts["wikidata"] = len(items), len(r)
    rows += (r := from_gleif(seed, warnings)); counts["gleif"] = len(r)
    rows += (r := from_sec(seed, warnings)); counts["sec"] = len(r)
    rows = map_openalex(rows, seed["name"])
    for r in rows:
        check_text(r)
    out = {"seed": {k: v for k, v in seed.items() if k != "ror_record"}, "counts": counts, "warnings": warnings,
           "mapped": sum(1 for r in rows if r["openalex_id"]), "rows": rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def read(slug):
    path = CASES / slug / "candidates.json"
    if not path.exists():
        raise SystemExit(f"{path} does not exist yet: run `family.py propose --case {slug} ...` first")
    return json.loads(path.read_text(encoding="utf-8"))


def review(slug):
    data = read(slug)
    for i, r in enumerate(data["rows"]):
        hint = " (foundation-like name)" if r["entity"] == "institution" and guess_kind(r["name"]) == "foundation" else ""
        print(f"{i:>3} {r['status']:<19} {r['entity']:<11} {r['match_type']:<10} rel={r['rel'] or '(' + str(r['rel_suggested']) + '?)':<14}{hint}"
              f" kind={str(r['kind']):<10} {r['openalex_id'] or 'unmapped':<12} {r['name'][:50]}  [{r['id_source']}]"
              + (f"  reason: {r['reason']}" if r["reason"] else "") + (f"  flag: {r['flag']}" if r.get("flag") else ""))
    print("warnings: " + "; ".join(data["warnings"]))


def set_row(slug, i, status, rel=None, kind=None, reason=None, oa_id=None, source=None):
    data = read(slug)
    r = data["rows"][i]
    if status == "rejected" and not reason:
        raise SystemExit("a rejected row needs --reason")
    if status == "approved" and r["status"] != "proposed" and not source:
        raise SystemExit(f"row {i} is {r['status']}: approving it needs --source <url> from a human")
    if rel and rel not in loader.RELS or kind and kind not in loader.KINDS:
        raise SystemExit(f"rel must be one of {sorted(loader.RELS)}, kind one of {sorted(loader.KINDS)}")
    if oa_id and not re.fullmatch(r"[IF]\d+", oa_id):
        raise SystemExit("--openalex-id must be I<digits> or F<digits>")
    r.update(status=status, rel=rel or r["rel"], kind=kind or r["kind"], reason=reason or r["reason"])
    if oa_id:
        r.update(openalex_id=oa_id, entity="funder" if oa_id[0] == "F" else "institution")
    if source:
        r["source_url"], r["human_source"] = source, source
        r["evidence"] += f" | human source: {source}"
    check_text(r)
    (CASES / slug / "candidates.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def add(slug, name, note):
    """A body with no formal link (a trade group, say): it waits for a human source (D4)."""
    data = read(slug)
    r = row("manual", None, name, data["seed"]["name"], None, f"added by hand: {note}", None,
            status="needs_human_source", linked=False)
    check_text(r)
    data["rows"].append(r)
    (CASES / slug / "candidates.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def apply(slug, by, force=False):
    if not by or not re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", by):
        raise SystemExit("--by <your name> is required (it goes into decided_by)")
    case = CASES / slug / "case.json"
    if (CASES / slug / "status.json").exists() and not force:
        raise SystemExit(f"case {slug} already has a built graph (status.json); add --force to change its case.json")
    cfg, rows = json.loads(case.read_text(encoding="utf-8")), read(slug)["rows"]
    stamp = f"{by}, {date.today():%b} {date.today().day}"
    added = {"funders": 0, "institutions": 0, "rejected": 0}
    for r in rows:
        if r["status"] == "approved" and r.get("flag") == "shareholder" and not r.get("human_source"):
            raise SystemExit(f"approved row {r['name']!r} is a {SHAREHOLDER}: approve it with --source <url>")
        if r["status"] == "approved" and r["openalex_id"]:
            key, field = ("funders", "kind") if r["entity"] == "funder" else ("institutions", "rel")
            if not r[field]:
                raise SystemExit(f"approved row {r['name']!r} has no {field}")
            if r["openalex_id"] not in {x["id"] for x in cfg[key]}:
                cfg[key].append({"id": r["openalex_id"], "name": r["openalex_name"] or r["name"], field: r[field]})
                added[key] += 1
                cfg["decided_by"][key] = stamp
        elif r["status"] == "rejected" and r["openalex_id"]:
            cfg.setdefault("rejected", [])
            if r["openalex_id"] not in {x["id"] for x in cfg["rejected"]}:
                cfg["rejected"].append({"id": r["openalex_id"], "name": r["openalex_name"] or r["name"],
                                        "reason": f"{r['reason']} ({by}, family.py review)"})
                added["rejected"] += 1
    both = {x["id"] for x in cfg["funders"] + cfg["institutions"]} & {x["id"] for x in cfg.get("rejected", [])}
    if both:
        raise SystemExit(f"ids both included and rejected: {sorted(both)}; fix candidates.json or case.json first")
    loader.check_config(cfg, slug)  # raises ValueError and writes nothing if the result is not a legal case
    case.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return added


def main(argv=None):
    p = argparse.ArgumentParser(description="Corporate-family candidates for a TieTrace case (a human decides).")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("propose", "review", "set", "add", "apply"):
        s = sub.add_parser(name)
        s.add_argument("--case", required=True)
    sub.choices["propose"].add_argument("--seed-ror")
    sub.choices["propose"].add_argument("--seed-wikidata")
    sub.choices["propose"].add_argument("--force", action="store_true", help="replace an existing candidates.json")
    sub.choices["propose"].add_argument("--skip-wikidata", action="store_true",
                                        help="skip the Wikidata SPARQL query (when it answers 429)")
    s = sub.choices["set"]
    s.add_argument("--row", type=int, required=True)
    s.add_argument("--status", required=True, choices=["approved", "rejected"])
    for flag in ("--rel", "--kind", "--reason", "--openalex-id", "--source"):
        s.add_argument(flag)
    sub.choices["add"].add_argument("--name", required=True)
    sub.choices["add"].add_argument("--note", required=True)
    sub.choices["apply"].add_argument("--by")
    sub.choices["apply"].add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    if not loader.SLUG_RE.fullmatch(a.case):
        p.error(f"bad case slug {a.case!r}")
    if a.cmd == "propose":
        if bool(a.seed_ror) == bool(a.seed_wikidata):
            p.error("give exactly one of --seed-ror or --seed-wikidata")
        if a.seed_wikidata and not re.fullmatch(r"Q\d+", a.seed_wikidata) or a.seed_ror and not re.fullmatch(r"0[a-z0-9]{8}", a.seed_ror):
            p.error("bad seed id (ROR like 00yc8qw13, Wikidata like Q3295867)")
        loader.KEY = loader.api_key()
        out = propose(a.case, a.seed_ror, a.seed_wikidata, a.force, a.skip_wikidata)
        print(json.dumps({k: out[k] for k in ("seed", "counts", "mapped", "warnings")}, indent=1, ensure_ascii=False))
        print(f"collisions: {sum(r['status'] == 'same_name_collision' for r in out['rows'])}, rows: {len(out['rows'])}")
    elif a.cmd == "review":
        review(a.case)
    elif a.cmd == "set":
        set_row(a.case, a.row, a.status, a.rel, a.kind, a.reason, a.openalex_id, a.source)
    elif a.cmd == "add":
        add(a.case, a.name, a.note)
    else:
        print(apply(a.case, a.by, a.force))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    main()
