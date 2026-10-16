"""Checkpoints 2 and 5c: build the research graph `research:<slug>` for one case from OpenAlex.

Run:  uv run python loader.py --case obesity-coke --dry-run   # counts, calls, cost, RAM; writes nothing
      uv run python loader.py --case obesity-coke --rebuild   # build (default case: obesity-coke)
      uv run python loader.py --rebuild --refresh             # ignore the raw cache and call the API again

The case (company, topics, thresholds) comes from cases/<slug>/case.json. The graph is built from
scratch as staging:<slug>, self-checked, then swapped over research:<slug> with Redis RENAME, so a
failed build leaves the old graph untouched (--rebuild is required if it already exists). Raw API
pages are cached in cases/<slug>/raw/ (obesity-coke: data/raw/) as JSONL (line 1 = query params +
fetch date), so a re-run costs zero API calls. cases/<slug>/status.json records the last build.
"""
import argparse
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx
from falkordb import FalkorDB

API = "https://api.openalex.org/"
WORK_FIELDS = "id,doi,title,publication_year,authorships,funders,topics,primary_topic"
ROOT = Path(__file__).parent
CASES = ROOT / "cases"
DEFAULT_CASE = "obesity-coke"
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")  # graph name is research:<slug>

# ---------- the case config: cases/<slug>/case.json (checkpoint 5c) ----------
# One file per case says which company, topics and thresholds a graph is built for. The model may
# propose a file; check_config decides what is legal. Every decision names who made it and when.
DECISIONS = ("funders", "institutions", "topics", "years", "topic_cap", "public_ids", "small_paper",
             "recent_year", "works_count_anomaly", "max_api_calls")
CASE_KEYS = {"slug", "company", "decided_by", *DECISIONS}
OPTIONAL_KEYS = {"notes", "rejected", "cache_from"}  # free text; candidates turned down, each with a reason;
# cache_from: a demo copy of another case that reads that case's raw cache and never calls the API (checkpoint 6)
ITEM_KEYS = {"funders": {"id", "name", "kind"}, "institutions": {"id", "name", "rel"},
             "topics": {"id", "name"}, "rejected": {"id", "name", "reason"}}
ID_RE = {"funders": r"F\d+", "institutions": r"I\d+", "topics": r"T\d+", "public_ids": r"A\d+",
         "rejected": r"[FITA]\d+"}
KINDS = {"company", "foundation"}            # Funder.kind: company money is A1, foundation money A2
RELS = {"parent", "subsidiary", "bottler"}   # PARENT_OF.rel; a bottler affiliation is A2
MAX_CONFIG_BYTES, MAX_IDS = 20_000, 20


def check_config(cfg, slug):
    """Plain dict checks. Raises ValueError naming the first problem."""
    def need(ok, msg):
        if not ok:
            raise ValueError(f"case {slug}: {msg}")

    def is_int(v, low, high):
        return isinstance(v, int) and not isinstance(v, bool) and low <= v <= high

    need(isinstance(cfg, dict), "case.json must be an object")
    need(not set(cfg) - CASE_KEYS - OPTIONAL_KEYS, f"unknown keys {sorted(set(cfg) - CASE_KEYS - OPTIONAL_KEYS)}")
    need(not CASE_KEYS - set(cfg), f"missing keys {sorted(CASE_KEYS - set(cfg))}")
    need(cfg["slug"] == slug, "slug must equal the folder name")
    need(isinstance(cfg["company"], str) and cfg["company"].strip(), "company must be a non-empty string")
    need(isinstance(cfg.get("notes", ""), str), "notes must be a string")
    src = cfg.get("cache_from", "")
    need(src == "" or (isinstance(src, str) and SLUG_RE.fullmatch(src) and src != slug),
         "cache_from must be another case's slug")
    for key in ("funders", "institutions", "topics", "rejected"):
        items = cfg.get(key, [])
        need(isinstance(items, list) and len(items) <= MAX_IDS, f"{key} must be a list of at most {MAX_IDS}")
        for it in items:
            need(isinstance(it, dict) and set(it) == ITEM_KEYS[key], f"each {key} item needs exactly {sorted(ITEM_KEYS[key])}")
            need(isinstance(it["id"], str) and re.fullmatch(ID_RE[key], it["id"]), f"bad {key} id {it['id']!r}")
            need(all(isinstance(v, str) and v.strip() for v in it.values()), f"{key} values must be non-empty strings")
        need(len({it["id"] for it in items}) == len(items), f"duplicate ids in {key}")
    need(all(f["kind"] in KINDS for f in cfg["funders"]), f"funder kind must be one of {sorted(KINDS)}")
    need(all(i["rel"] in RELS for i in cfg["institutions"]), f"institution rel must be one of {sorted(RELS)}")
    parents = [i for i in cfg["institutions"] if i["rel"] == "parent"]
    need(len(parents) == (1 if cfg["institutions"] else 0), "institutions need exactly one rel=parent")
    need(cfg["funders"] or cfg["institutions"], "need at least one funder or institution")
    need(cfg["topics"], "need at least one topic")
    ids = cfg["public_ids"]
    need(isinstance(ids, list) and len(ids) <= MAX_IDS and len(set(ids)) == len(ids)
         and all(isinstance(i, str) and re.fullmatch(ID_RE["public_ids"], i) for i in ids),
         f"public_ids must be at most {MAX_IDS} distinct author ids (A...)")
    years = cfg["years"]
    need(isinstance(years, list) and len(years) == 2 and all(is_int(y, 1900, 2100) for y in years)
         and years[0] <= years[1], "years must be [first, last]")
    need(is_int(cfg["recent_year"], years[0], years[1]), "recent_year must lie within years")
    need(is_int(cfg["topic_cap"], years[1] - years[0] + 1, 200_000), "topic_cap must be an integer, at least 1 per year")
    need(is_int(cfg["small_paper"], 2, 1000), "small_paper must be an integer from 2 to 1000")
    need(is_int(cfg["works_count_anomaly"], 1, 10**6), "works_count_anomaly must be a positive integer")
    need(is_int(cfg["max_api_calls"], 1, 100_000), "max_api_calls must be an integer from 1 to 100000")
    who = cfg["decided_by"]
    need(isinstance(who, dict) and set(who) == set(DECISIONS), f"decided_by needs exactly {list(DECISIONS)}")
    need(all(isinstance(v, str) and re.fullmatch(r"[A-Za-z][A-Za-z .'-]*, [A-Z][a-z]{2} \d{1,2}( \d{4})?", v)
             for v in who.values()), "each decided_by value is 'Name, Mon D' (for example 'Aaryan, Oct 16')")


def load_config(slug):
    """Read and check cases/<slug>/case.json. Raises ValueError (bad slug or config) or OSError (no file)."""
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise ValueError(f"bad case slug {slug!r}: lower case letters, digits and -, 2 to 40 characters")
    path = CASES / slug / "case.json"
    if path.stat().st_size >= MAX_CONFIG_BYTES:
        raise ValueError(f"case {slug}: case.json must be under {MAX_CONFIG_BYTES} bytes")
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"case {slug}: case.json is not valid JSON ({e.msg})") from None
    check_config(cfg, slug)
    return cfg


def config_hash(cfg):
    """Short hash of the config content (key order and spacing do not matter); stored in Meta and status.json."""
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


def use_case(slug):
    """Set the module constants below from one case. Called once here for the default case, and by
    main() for --case. The names are the checkpoint-2 ones (tests import them); COKE_* now mean
    "the company family of the current case"."""
    global CONFIG, SLUG, GRAPH, RAW, FUNDER_KIND, COMPANY_FUNDER, FOUNDATION_FUNDER, TOPICS, TOPIC_YEARS
    global TOPIC_CAP, PER_YEAR, COKE_PARENT, COKE_FAMILY, COKE_CHILD_REL, SMALL_PAPER, RECENT_YEAR
    global WORKS_COUNT_ANOMALY, MAX_API_CALLS, BORROWED
    CONFIG = load_config(slug)
    SLUG, GRAPH = slug, f"research:{slug}"
    source = CONFIG.get("cache_from", slug)  # a demo copy reads the other case's cache (and never writes it)
    RAW = CASES / source / "raw"
    if source == DEFAULT_CASE and not RAW.exists():
        RAW = ROOT / "data" / "raw"  # the checkpoint-2 cache, so obesity-coke rebuilds with zero API calls
    FUNDER_KIND = {f["id"]: f["kind"] for f in CONFIG["funders"]}
    # Kept for tests and old scripts: the first company and the first foundation funder (None if absent).
    COMPANY_FUNDER = next((f for f, k in FUNDER_KIND.items() if k == "company"), None)
    FOUNDATION_FUNDER = next((f for f, k in FUNDER_KIND.items() if k == "foundation"), None)
    TOPICS = {t["id"]: t["name"] for t in CONFIG["topics"]}
    TOPIC_YEARS = range(CONFIG["years"][0], CONFIG["years"][1] + 1)
    # ponytail: topic works capped at topic_cap (30,000 of ~260k for obesity-coke): the cap // years most
    # cited per year, so recent years are not crowded out. Raise topic_cap (1 API call per 100 works).
    TOPIC_CAP = CONFIG["topic_cap"]
    PER_YEAR = TOPIC_CAP // len(TOPIC_YEARS)
    # The company family. OpenAlex lineage is partial, so the list is hand-checked and approved per case.
    COKE_FAMILY = {i["id"]: i["name"] for i in CONFIG["institutions"]}
    COKE_PARENT = next((i["id"] for i in CONFIG["institutions"] if i["rel"] == "parent"), None)
    # PARENT_OF.rel: "subsidiary" (owned) or "bottler" (franchise bottler, the company a minority owner).
    COKE_CHILD_REL = {i["id"]: i["rel"] for i in CONFIG["institutions"] if i["rel"] != "parent"}
    SMALL_PAPER = CONFIG["small_paper"]   # Tier rules: papers with more authors are "large"
    RECENT_YEAR = CONFIG["recent_year"]   # "recent" = this year onwards (Meta.recent_year)
    # ponytail: works_count above this is treated as a likely merged profile; a guess, not a study.
    WORKS_COUNT_ANOMALY = CONFIG["works_count_anomaly"]
    MAX_API_CALLS = CONFIG["max_api_calls"]  # get() stops the run past this many calls
    BORROWED = "cache_from" in CONFIG


use_case(DEFAULT_CASE)

LIST_TRUNCATION = 100  # OpenAlex list endpoints return at most 100 authorships per work
# Records that are not research papers. One shared rule: used here (not counted as a second paper in
# COAUTH) and by research.py (not counted as Tier A evidence). A correction repeats the authors of
# the paper it corrects, so it would otherwise look like a second joint paper.
JUNK_DOI_PREFIX = "10.5256/"  # F1000 peer-review reports
JUNK_TITLE_PREFIXES = ("peer review report", "correction:", "corrigendum", "erratum", "notice of retraction")
NON_PAPER_TITLES = {"index", "front matter"}  # kept as Works, flagged non_paper: never evidence, never in COAUTH
BATCH = 5000
TIMEOUT = httpx.Timeout(30, connect=10)  # per connect/read/write/pool step; stalls fail fast
TRIES = 5                                # per request, then fail loudly

api_calls = 0
calls_lock = threading.Lock()  # get() runs in worker threads
fetch_dates = set()  # fetched_at of every cache used, for Meta.data_version


def api_key():
    text = (Path(__file__).parent / ".env").read_text()
    return re.search(r"OPENALEX_API_KEY=(\S+)", text).group(1).strip("\"'")


def get(path, params):
    """One OpenAlex GET with retry. Errors name the path only: the URL holds the API key."""
    global api_calls
    if BORROWED:  # a demo copy must never refetch into (and overwrite) the other case's cache
        raise SystemExit(f"case {SLUG} reads the cache of case {CONFIG['cache_from']} and never calls the API; "
                         f"a cache miss means its case.json no longer matches that case")
    for attempt in range(TRIES):
        with calls_lock:
            if api_calls >= MAX_API_CALLS:
                raise SystemExit(f"max_api_calls ({MAX_API_CALLS}) of case {SLUG} reached; stopping")
            api_calls += 1
        try:
            r = httpx.get(API + path, params={**params, "api_key": KEY}, timeout=TIMEOUT)
        except httpx.HTTPError as e:  # network trouble: retry, never show the URL
            print(f"OpenAlex {type(e).__name__} on {path}, retry {attempt + 1}/{TRIES}", flush=True)
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            body = r.text[:300].lower()
            if any(word in body for word in ("daily", "budget", "credit", "quota")):
                raise SystemExit(f"OpenAlex daily budget used up (429 on {path}); stopping")
            print(f"OpenAlex 429 on {path}, retry {attempt + 1}/{TRIES}", flush=True)
        elif r.status_code not in (500, 502, 503, 504):
            raise RuntimeError(f"OpenAlex {r.status_code} on {path}")
        time.sleep(2 ** attempt)
    raise RuntimeError(f"OpenAlex failed {TRIES} times on {path}")


def short(url):
    """'https://openalex.org/A123' -> 'A123'."""
    return url.rsplit("/", 1)[-1] if url else None


def bare_doi(doi):
    """'https://doi.org/10.1/AB' -> '10.1/ab'; a missing or empty DOI -> None."""
    return (doi or "").removeprefix("https://doi.org/").lower() or None


def is_junk_work(w):
    """Peer-review report, correction or retraction notice (kept as a Work, but never evidence)."""
    return ((bare_doi(w.get("doi")) or "").startswith(JUNK_DOI_PREFIX)
            or (w.get("title") or "").lower().startswith(JUNK_TITLE_PREFIXES))


def is_non_paper(w):
    return (w.get("title") or "").strip().lower() in NON_PAPER_TITLES


def title_unknown(w):
    return not (w.get("title") or "").strip()  # a real paper OpenAlex has no title for: counts as evidence


def norm_name(name):
    """'José Ramírez' -> 'jose ramirez': accents folded, lower case, punctuation to single spaces."""
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.lower()).split())


def author_slots(w):
    """Distinct author ids in listed order, and n_authors = those plus id-less slots."""
    ids, no_id = [], 0
    for a in w["authorships"]:
        pid = short(a["author"].get("id"))
        if not pid:
            no_id += 1
        elif pid not in ids:
            ids.append(pid)
    return ids, len(ids) + no_id


# Cache files: line 1 is a header {"params", "fetched_at"}; a cache whose params differ
# from the current query is ignored, so changing TOPICS, caps or filters refetches.
def read_cache(name, params, refresh):
    path = RAW / f"{name}.jsonl"
    if refresh or not path.exists():
        return None
    with path.open() as f:
        header = json.loads(f.readline() or "{}")
        if header.get("params") != params:
            return None
        fetch_dates.add(header["fetched_at"])
        return [json.loads(line) for line in f]


def header_line(params):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fetch_dates.add(now)
    return json.dumps({"params": params, "fetched_at": now}) + "\n"


def fetch_pages(name, params, limit, refresh):
    """Cursor-page a /works query into data/raw/<name>.jsonl; returns at most `limit` works."""
    key = {**params, "limit": limit}
    pages = read_cache(name, key, refresh)
    if pages is None:
        print(f"fetching {name}", flush=True)
        pages, cursor, n = [], "*", 0
        while cursor and n < limit:
            page = get("works", {**params, "per_page": 100, "cursor": cursor})
            pages.append(page)
            n += len(page["results"])
            cursor = page["meta"].get("next_cursor") if page["results"] else None
            time.sleep(0.05)  # stay well under 30 requests/second
        tmp = RAW / f"{name}.tmp"
        tmp.write_text(header_line(key) + "".join(json.dumps(p) + "\n" for p in pages))
        tmp.rename(RAW / f"{name}.jsonl")  # only a complete download becomes the cache
    return [w for p in pages for w in p["results"]][:limit]


def fetch_one(name, path, params, refresh):
    """A single cached GET (used for group_by counts)."""
    got = read_cache(name, {"path": path, **params}, refresh)
    if got is None:
        got = [get(path, params)]
        (RAW / f"{name}.jsonl").write_text(header_line({"path": path, **params}) + json.dumps(got[0]) + "\n")
    return got[0]


def fetch_by_id(name, ids, fetch_chunk, params, refresh):
    """Cache of {id: record} in data/raw/<name>.jsonl; only missing ids hit the API."""
    path = RAW / f"{name}.jsonl"
    cache = {}
    lines = read_cache(name, params, refresh)
    if lines is None and BORROWED:  # never truncate the other case's cache file
        raise SystemExit(f"case {SLUG}: no matching {name} cache in {CONFIG['cache_from']}; this demo copy never calls the API")
    if lines is None:
        path.write_text(header_line(params))
    for line in lines or []:
        cache.update(line)
    missing = [i for i in ids if i not in cache]
    chunks = [missing[i:i + 100] for i in range(0, len(missing), 100)]
    print(f"{name}: {len(cache)} cached, {len(missing)} to fetch in {len(chunks)} calls", flush=True)
    with ThreadPoolExecutor(max_workers=5) as pool, path.open("a") as f:
        for k, got in enumerate(pool.map(fetch_chunk, chunks), 1):
            f.write(json.dumps(got) + "\n")
            f.flush()  # a killed run keeps what it already fetched
            cache.update(got)
            if k % 50 == 0:
                print(f"  {name}: {k}/{len(chunks)}", flush=True)
    return cache


def fetch_full_works(chunk):
    # Single-work GETs return all authorships; list endpoints cut them at 100.
    return {w: get(f"works/{w}", {"select": WORK_FIELDS}) for w in chunk}


def fetch_works_counts(chunk):
    page = get("authors", {"filter": "ids.openalex:" + "|".join(chunk),
                           "select": "id,works_count", "per_page": 100})
    got = {short(a["id"]): a["works_count"] for a in page["results"]}
    return {a: got.get(a) for a in chunk}  # store misses too, so they are not re-asked


def norm_title(t):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).split())


def same_paper_key(w):
    title, ids = norm_title(w.get("title")), author_slots(w)[0]
    return (title, w.get("publication_year"), frozenset(ids)) if title and ids else None


def dedupe(works, scope):
    """Drop duplicate records of one paper: same bare DOI, or same title + year + author set.
    The kept record is the funded one, then family, then topic (then the lowest oa_id)."""
    rank = {"funded": 0, "family": 1, "topic": 2}
    removed = 0
    for key_of in (lambda w: bare_doi(w.get("doi")), same_paper_key):
        groups = defaultdict(list)
        for wid, w in works.items():
            k = key_of(w)
            if k:
                groups[k].append(wid)
        for ids in groups.values():
            # same scope: prefer the record that has a DOI, then the lowest oa_id
            ids.sort(key=lambda i: (rank[scope[i]], not bare_doi(works[i].get("doi")), i))
            for drop in ids[1:]:
                del works[drop]
                removed += 1
    return removed


def work_queries():
    """(cache name, /works params, limit) for every list download of the current case, in fetch order.
    The params are also the cache key, so they must not change for a case whose cache exists."""
    queries = []
    if FUNDER_KIND:
        queries.append(("funded", {"filter": "funders.id:" + "|".join(FUNDER_KIND), "select": WORK_FIELDS}, 10**9))
    if COKE_FAMILY:
        queries.append(("family", {"filter": "authorships.institutions.lineage:" + "|".join(COKE_FAMILY),
                                   "select": WORK_FIELDS}, 10**9))
    for year in TOPIC_YEARS:
        queries.append((f"topic_{year}", {"filter": f"primary_topic.id:{'|'.join(TOPICS)},publication_year:{year}",
                                          "select": WORK_FIELDS, "sort": "cited_by_count:desc"}, PER_YEAR))
    return queries


def topic_years_params():
    return {"filter": f"primary_topic.id:{'|'.join(TOPICS)},publication_year:{TOPIC_YEARS[0]}-{TOPIC_YEARS[-1]}",
            "group_by": "publication_year"}


def load_works(refresh):
    got = {name: fetch_pages(name, params, limit, refresh) for name, params, limit in work_queries()}
    funded, family = got.get("funded", []), got.get("family", [])
    topic = [w for name, recs in got.items() if name.startswith("topic_") for w in recs]
    groups = fetch_one("topic_years", "works", topic_years_params(), refresh)
    year_totals = {int(g["key"]): g["count"] for g in groups["group_by"]}

    works, scope = {}, {}
    for name, recs in (("topic", topic), ("family", family), ("funded", funded)):
        for w in recs:  # funded last, so its records win on overlap
            works[short(w["id"])] = w
            scope[short(w["id"])] = name
    # Tier A must be complete: re-fetch funded and family works whose author list was cut.
    cut = sorted(w for w, s in scope.items() if s != "topic" and len(works[w]["authorships"]) >= LIST_TRUNCATION)
    full = fetch_by_id("tier_a_full", cut, fetch_full_works, {"select": WORK_FIELDS}, refresh)
    works.update({w: full[w] for w in cut})
    removed = dedupe(works, scope)
    truncated = {w for w, rec in works.items()
                 if scope[w] == "topic" and len(rec["authorships"]) >= LIST_TRUNCATION}
    return works, scope, truncated, year_totals, removed


def build_rows(works, truncated, works_counts, scope=None):
    persons, orgs, funders = {}, {}, {}
    work_rows, authored, affiliated, funded_by, about = [], [], [], [], []
    authors_of = {}
    for wid, w in works.items():
        doi, year = bare_doi(w.get("doi")), w.get("publication_year")
        ids, n = author_slots(w)
        if wid in truncated:  # the list stopped at 100 slots, so there are at least 100 authors
            n = max(n, LIST_TRUNCATION)
        row = {"oa_id": wid, "doi": doi, "year": year, "n_authors": n, "title": w.get("title"),
               "scope": (scope or {}).get(wid),
               # never Tier A evidence: the one junk rule, precomputed here so queries need no toLower(title)
               "junk": is_junk_work(w) or is_non_paper(w)}
        if wid in truncated:
            row["authors_truncated"] = True  # n_authors is a lower bound (list API cap)
        if is_non_paper(w):
            row["non_paper"] = True  # "Index" / "Front Matter": not research, never evidence
        if title_unknown(w):
            row["title_unknown"] = True
        work_rows.append(row)
        seen = set()
        for a in w["authorships"]:
            pid = short(a["author"].get("id"))
            if not pid or pid in seen:  # first slot of each author only
                continue
            seen.add(pid)
            orcid = a["author"].get("orcid")
            persons[pid] = {"oa_id": pid, "name": a["author"].get("display_name"),
                            "name_norm": norm_name(a["author"].get("display_name")),
                            "orcid": orcid.removeprefix("https://orcid.org/") if orcid else None}
            authored.append({"p": pid, "w": wid, "position": a.get("author_position")})
            for inst in a.get("institutions") or []:
                oid = short(inst.get("id"))
                if not oid:
                    continue
                orgs[oid] = {"oa_id": oid, "name": inst.get("display_name"),
                             "type": inst.get("type"), "target": oid in COKE_FAMILY}
                affiliated.append({"p": pid, "o": oid, "year": year, "doi": doi, "work": wid})
        authors_of[wid] = (ids, n)
        for f in w.get("funders") or []:
            fid = short(f["id"])
            funders[fid] = {"oa_id": fid, "name": f.get("display_name"),
                            "target": fid in FUNDER_KIND, "kind": FUNDER_KIND.get(fid)}
            funded_by.append({"w": wid, "f": fid})
        for t in w.get("topics") or []:
            if short(t["id"]) in TOPICS:
                about.append({"w": wid, "t": short(t["id"])})

    for oid, name in COKE_FAMILY.items():
        orgs.setdefault(oid, {"oa_id": oid, "name": name, "type": "company", "target": True})
    for p in persons.values():
        wc = works_counts.get(p["oa_id"])
        p["works_count"] = wc
        if wc is not None and wc > WORKS_COUNT_ANOMALY:
            p["profile_flag"] = "works_count_anomaly"  # the stronger warning wins
        elif not p["orcid"]:
            p["profile_flag"] = "no_orcid"
        else:
            p["profile_flag"] = "ok"

    rows = {
        "persons": list(persons.values()), "works": work_rows, "orgs": list(orgs.values()),
        "funders": list(funders.values()),
        "topics": [{"oa_id": t, "name": name} for t, name in TOPICS.items()],
        "authored": authored, "affiliated": affiliated, "funded_by": funded_by, "about": about,
        "parent_of": [{"a": COKE_PARENT, "b": o, "rel": COKE_CHILD_REL[o]}
                      for o in COKE_FAMILY if o != COKE_PARENT],
        "coauth": coauth_rows(works, authors_of),
    }
    # Pre-filtered Tier B edge, because algo.BFS only takes a relationship type.
    rows["coauth_b"] = [r for r in rows["coauth"] if r["recent_small_papers"] >= 2]
    rows["stats"] = load_stats(works, rows)
    return rows


def load_stats(works, rows):
    """Numbers research.py quotes in its scope block, computed once here (not hard-coded there)."""
    topic = {a["w"] for a in rows["about"]}
    funded = {f["w"] for f in rows["funded_by"]}
    affiliated = {a["work"] for a in rows["affiliated"]}
    # Leftover duplicates: different records with the same normalised title and year.
    groups = defaultdict(list)
    for wid, w in works.items():
        t = norm_title(w.get("title"))
        if t:
            groups[(t, w.get("publication_year"))].append(wid)
    dup_ids = {wid for ids in groups.values() if len(ids) > 1 for wid in ids}
    return {"topic_works": len(topic), "topic_with_funder": len(topic & funded),
            "topic_with_funder_or_affiliation": len(topic & (funded | affiliated)),
            "dup_groups": sum(1 for ids in groups.values() if len(ids) > 1), "dup_records": len(dup_ids),
            # COAUTH_B links (one direction) whose counted papers include one of those records
            "dup_links": sum(1 for r in rows["coauth_b"]
                             if r["a"] < r["b"] and dup_ids & set(r["recent_small_works"])),
            "junk_works": sum(1 for w in works.values() if is_junk_work(w)),
            "non_paper_works": sum(1 for w in works.values() if is_non_paper(w)),
            "title_unknown_works": sum(1 for w in works.values() if title_unknown(w))}


def coauth_rows(works, authors_of):
    """One row per ordered pair (both directions), with the COAUTH weights.
    authors_of[wid] = (distinct author ids, n_authors). A missing year is stored as 0."""
    pairs = defaultdict(lambda: {"n_papers": 0, "w": 0.0, "small_papers": 0, "recency_w": 0.0,
                                 "recent_small_papers": 0, "recent_small_w": 0.0,
                                 "dois": [], "years": [], "works": [],
                                 "recent_small_dois": [], "recent_small_works": []})
    # No size cap: the full load gives ~0.9M pairs, so O(n^2) on big papers is affordable.
    for wid, (authors, n) in authors_of.items():
        if len(authors) < 2:
            continue
        w = works[wid]
        if is_junk_work(w) or is_non_paper(w):  # corrections / peer-review reports are not a second joint paper
            continue
        year, share = w.get("publication_year") or 0, 1 / (n - 1)  # FalkorDB arrays reject null
        small, recent = n <= SMALL_PAPER, year >= RECENT_YEAR
        for i, a in enumerate(authors):
            for b in authors[i + 1:]:
                e = pairs[(a, b) if a < b else (b, a)]
                e["n_papers"] += 1
                e["w"] += share
                e["small_papers"] += small
                e["recency_w"] += share if recent else 0
                e["recent_small_papers"] += small and recent
                e["recent_small_w"] += share if small and recent else 0
                e["dois"].append(bare_doi(w.get("doi")) or "")  # "" keeps lists parallel
                e["years"].append(year)
                e["works"].append(wid)
                if small and recent:  # evidence list for Tier B: only the papers that count
                    e["recent_small_dois"].append(bare_doi(w.get("doi")) or "")
                    e["recent_small_works"].append(wid)
    rows = []
    for (a, b), e in pairs.items():
        rows.append({"a": a, "b": b, **e})
        rows.append({"a": b, "b": a, **e})
    return rows


QUERIES = {
    "persons": "UNWIND $rows AS r CREATE (:Person {oa_id:r.oa_id, name:r.name, name_norm:r.name_norm, orcid:r.orcid, "
               "works_count:r.works_count, profile_flag:r.profile_flag})",
    "works": "UNWIND $rows AS r CREATE (w:Work {oa_id:r.oa_id, doi:r.doi, year:r.year, "
             "n_authors:r.n_authors, title:r.title, scope:r.scope, junk:r.junk}) SET w.authors_truncated = r.authors_truncated, "
             "w.non_paper = r.non_paper, w.title_unknown = r.title_unknown",
    "orgs": "UNWIND $rows AS r CREATE (:Org {oa_id:r.oa_id, name:r.name, type:r.type, target:r.target})",
    "funders": "UNWIND $rows AS r CREATE (:Funder {oa_id:r.oa_id, name:r.name, target:r.target, kind:r.kind})",
    "topics": "UNWIND $rows AS r CREATE (:Topic {oa_id:r.oa_id, name:r.name})",
    "authored": "UNWIND $rows AS r MATCH (p:Person {oa_id:r.p}), (w:Work {oa_id:r.w}) "
                "CREATE (p)-[:AUTHORED {position:r.position}]->(w)",
    "affiliated": "UNWIND $rows AS r MATCH (p:Person {oa_id:r.p}), (o:Org {oa_id:r.o}) "
                  "CREATE (p)-[:AFFILIATED {year:r.year, doi:r.doi, work:r.work}]->(o)",
    "funded_by": "UNWIND $rows AS r MATCH (w:Work {oa_id:r.w}), (f:Funder {oa_id:r.f}) "
                 "CREATE (w)-[:FUNDED_BY]->(f)",
    "about": "UNWIND $rows AS r MATCH (w:Work {oa_id:r.w}), (t:Topic {oa_id:r.t}) CREATE (w)-[:ABOUT]->(t)",
    "parent_of": "UNWIND $rows AS r MATCH (a:Org {oa_id:r.a}), (b:Org {oa_id:r.b}) CREATE (a)-[:PARENT_OF {rel:r.rel}]->(b)",
    "coauth": "UNWIND $rows AS r MATCH (a:Person {oa_id:r.a}), (b:Person {oa_id:r.b}) "
              "CREATE (a)-[:COAUTH {n_papers:r.n_papers, w:r.w, small_papers:r.small_papers, "
              "recency_w:r.recency_w, recent_small_papers:r.recent_small_papers, "
              "recent_small_w:r.recent_small_w, dois:r.dois, years:r.years, works:r.works}]->(b)",
    "coauth_b": "UNWIND $rows AS r MATCH (a:Person {oa_id:r.a}), (b:Person {oa_id:r.b}) "
                "CREATE (a)-[:COAUTH_B {n_papers:r.n_papers, w:r.w, recent_small_papers:r.recent_small_papers, "
                "recent_small_w:r.recent_small_w, dois:r.dois, works:r.works, "
                "recent_small_dois:r.recent_small_dois, recent_small_works:r.recent_small_works}]->(b)",
}

INDEXES = [
    "CREATE INDEX FOR (p:Person) ON (p.oa_id)",
    "CREATE INDEX FOR (p:Person) ON (p.name_norm)",
    "CREATE INDEX FOR (w:Work) ON (w.oa_id)",
    "CREATE INDEX FOR (w:Work) ON (w.doi)",
    "CREATE INDEX FOR (o:Org) ON (o.oa_id)",
    "CREATE INDEX FOR (f:Funder) ON (f.oa_id)",
    "CREATE INDEX FOR (t:Topic) ON (t.oa_id)",
    "CREATE FULLTEXT INDEX FOR (w:Work) ON (w.title)",
]


def staging_name(graph):
    return graph.replace("research:", "staging:", 1)


def write_graph(rows, meta, rebuild, before_swap=None):
    """Build the graph aside as staging:<slug>, check it, then swap it in with Redis RENAME.
    If anything fails, staging is dropped and research:<slug> stays exactly as it was."""
    db = FalkorDB(port=int(os.environ.get("FALKORDB_PORT", 6379)))
    staging = staging_name(GRAPH)
    if GRAPH in db.list_graphs() and not rebuild:
        raise SystemExit(f"{GRAPH} already exists; pass --rebuild to build a new copy and swap it in")
    if staging in db.list_graphs():
        db.select_graph(staging).delete()  # left over from a failed build
    g = db.select_graph(staging)
    try:
        for q in INDEXES:  # before the edge MATCHes, so they use the oa_id indexes
            g.query(q)
        for name, q in QUERIES.items():  # dict order: nodes first, then edges
            data = rows[name]
            print(f"writing {name}: {len(data)} rows", flush=True)
            for i in range(0, len(data), BATCH):
                g.query(q, {"rows": data[i:i + BATCH]})
        counts = {}
        for label in ("Person", "Work", "Org", "Funder", "Topic"):
            counts[label] = g.query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]
        for rel in ("AUTHORED", "AFFILIATED", "FUNDED_BY", "PARENT_OF", "ABOUT", "COAUTH", "COAUTH_B"):
            counts[rel] = g.query(f"MATCH ()-[r:{rel}]->() RETURN count(r)").result_set[0][0]
        meta = {**meta, "counts": json.dumps(counts), "build_status": "complete", "work_junk_flag": True}
        g.query("CREATE (m:Meta) SET m = $meta", {"meta": meta})
        self_check(g, counts)
        # RENAME replaces research:<slug> in one step (pinned by a test; FalkorDB does not document it).
        if before_swap:
            before_swap(meta)  # main() records the new build in status.json first, so a server notices the swap
        db.connection.rename(staging, GRAPH)
    except BaseException:
        if staging in db.list_graphs():
            db.select_graph(staging).delete()
        raise
    return counts, graph_size_mb(db, GRAPH)


def self_check(g, counts):
    """The staging graph must have people, works and authorship, and one complete Meta node."""
    empty = [k for k in ("Person", "Work", "Topic", "AUTHORED") if not counts.get(k)]
    if empty:
        raise RuntimeError(f"self-check failed: no {', '.join(empty)} in the new graph")
    rows = g.ro_query("MATCH (m:Meta) RETURN m.build_status, m.data_version, m.recent_year").result_set
    if len(rows) != 1 or rows[0][0] != "complete" or not rows[0][1] or rows[0][2] is None:
        raise RuntimeError(f"self-check failed: Meta is {rows}")


def graph_size_mb(db, graph):
    """total_graph_sz_mb from GRAPH.MEMORY USAGE, or None if this FalkorDB has no such command."""
    try:
        reply = db.connection.execute_command("GRAPH.MEMORY", "USAGE", graph)
        keys = [k.decode() if isinstance(k, bytes) else k for k in reply]
        return reply[keys.index("total_graph_sz_mb") + 1]
    except Exception:
        return None


def write_status(status, **fields):
    """cases/<slug>/status.json: building / ready / failed. Fields of the last good build are kept,
    because a failed build leaves that graph in place."""
    path = CASES / SLUG / "status.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    new = {**old, "status": status, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           **fields}
    if status != "failed":
        new.pop("error", None)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(new, indent=2) + "\n")
    tmp.replace(path)


# Ratios measured on obesity-coke (Oct 16 2026, FalkorDB module v4.22.1): 85,511 people and 780 MB
# (GRAPH.MEMORY total_graph_sz_mb) for 31,291 works. The 6.0.2 module reported 1,453 MB for the same graph.
# ponytail: a linear guess; COAUTH grows faster than linearly with big papers.
PERSONS_PER_WORK, MB_PER_WORK = 85511 / 31291, 780 / 31291
RELEASE_LOCK = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"
BUILD_LOCK_TTL = 4 * 3600  # seconds; a crashed build's lock expires by itself
USD_PER_LIST_CALL = 0.0001  # OpenAlex: list and filter calls cost $0.10 per 1,000


def cached(name, key):
    """True when data/raw/<name>.jsonl exists for exactly these params (reads the header line only)."""
    path = RAW / f"{name}.jsonl"
    if not path.exists():
        return False
    with path.open() as f:
        return json.loads(f.readline() or "{}").get("params") == key


def dry_run():
    """Counts only: per_page=1 probes read meta.count, plus one group_by for the topic years.
    Writes nothing (no graph, no cache, no status). Prints the expected calls, works, cost and RAM."""
    def count(params):
        return get("works", {"filter": params["filter"], "per_page": 1, "select": "id"})["meta"]["count"]

    per_year = {int(g["key"]): g["count"] for g in get("works", topic_years_params())["group_by"]}
    rows, works, new_works, calls = [], 0, 0, 0
    for name, params, limit in work_queries():
        year = int(name[6:]) if name.startswith("topic_") else None
        n = min(per_year.get(year, 0) if year else count(params), limit)
        hit = cached(name, {**params, "limit": limit})
        pages = -(-n // 100) if n >= limit else n // 100 + 1  # pages of 100; an uncapped run ends on an empty page
        need = 0 if hit else pages
        rows.append((name, n, "cached" if hit else f"{need} calls"))
        works, calls = works + n, calls + need
        new_works += 0 if hit else n
    calls += 0 if cached("topic_years", {"path": "works", **topic_years_params()}) else 1
    author_calls = round(new_works * PERSONS_PER_WORK / 100)  # works_count lookups, 100 ids per call
    calls += author_calls
    for name, n, how in rows:
        print(f"  {name:12} {n:>7} works  {how}")
    print(f"case {SLUG}: about {works} works before duplicates are merged "
          f"(funded and family works may also be topic works)")
    print(f"expected API list calls: about {calls} (including ~{author_calls} author lookups; single-work "
          f"re-fetches of works with 100+ authors are extra and free); max_api_calls is {MAX_API_CALLS}")
    print(f"expected cost: about ${calls * USD_PER_LIST_CALL:.3f}; projected graph RAM: about "
          f"{works * MB_PER_WORK:.0f} MB (GRAPH.MEMORY; a rebuild needs this much again while the old graph lives)")
    print(f"this dry run used {api_calls} API calls; it wrote nothing")


def main():
    global KEY
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=DEFAULT_CASE, help="case slug: reads cases/<slug>/case.json")
    ap.add_argument("--refresh", action="store_true", help="ignore the raw cache and call the API")
    ap.add_argument("--rebuild", action="store_true", help="replace the graph if it exists (built aside, then swapped)")
    ap.add_argument("--dry-run", action="store_true", help="print counts, calls, cost and RAM; write nothing")
    ap.add_argument("--until-year", type=int, help="staged demo: load only works published up to this year "
                                                   "(only for a case with cache_from, so a real case is never cut)")
    args = ap.parse_args()
    try:
        use_case(args.case)
    except (ValueError, OSError) as e:
        raise SystemExit(f"cannot load case {args.case!r}: {e}")
    if args.until_year is not None and (not BORROWED or not RECENT_YEAR <= args.until_year < TOPIC_YEARS[-1]):
        raise SystemExit(f"--until-year needs a demo case with cache_from (this protects real case graphs) and a "
                         f"year from recent_year {RECENT_YEAR} to {TOPIC_YEARS[-1] - 1}")
    if args.refresh and BORROWED:
        raise SystemExit(f"case {SLUG} borrows the cache of {CONFIG['cache_from']}; refresh that case instead")
    KEY = api_key()
    if args.dry_run:
        return dry_run()
    db = FalkorDB(port=int(os.environ.get("FALKORDB_PORT", 6379)))
    if GRAPH in db.list_graphs() and not args.rebuild:
        raise SystemExit(f"{GRAPH} already exists; pass --rebuild to build a new copy and swap it in")
    # One build per case at a time: two builds would share staging:<slug> and status.json.
    lock, token = f"build-lock:{SLUG}", f"{os.getpid()}@{time.time():.0f}"
    if not db.connection.set(lock, token, nx=True, ex=BUILD_LOCK_TTL):
        raise SystemExit(f"another build of case {SLUG} is running (Redis key {lock}); wait for it, or if it "
                         f"crashed, delete the key or wait up to {BUILD_LOCK_TTL // 3600} h for it to expire")
    try:
        RAW.mkdir(parents=True, exist_ok=True)
        start = time.time()
        write_status("building")
        try:
            counts, size_mb, meta = build(args)
        except BaseException as e:
            write_status("failed", error=f"{type(e).__name__}: {str(e)[:200]}")
            raise
        write_status("ready", built_at=meta["built_at"], data_version=meta["data_version"],
                     config_hash=meta["config_hash"], counts=counts, total_graph_sz_mb=size_mb, graph=GRAPH)
    finally:
        db.connection.eval(RELEASE_LOCK, 1, lock, token)  # only our own lock, checked and deleted in one step
    print(f"graph size: {size_mb} MB (GRAPH.MEMORY USAGE)")
    print(f"API calls this run: {api_calls}; wall clock: {time.time() - start:.0f}s")
    print(f"*** {GRAPH} was swapped in. A running TieTrace MCP server drops its cached facts for this case on "
          f"its next call (it watches status.json); restart the server if in doubt. ***")


def build(args):
    works, scope, truncated, year_totals, removed = load_works(args.refresh)
    years = list(TOPIC_YEARS)
    until = args.until_year
    if until is not None:  # staged demo slice: works up to `until` (a work with no year is kept)
        works = {w: rec for w, rec in works.items() if (rec.get("publication_year") or 0) <= until}
        truncated = truncated & set(works)
        years = [y for y in years if y <= until]
    person_ids = sorted({p for w in works.values() for p in author_slots(w)[0]})
    works_counts = fetch_by_id("authors", person_ids, fetch_works_counts,
                               {"select": "id,works_count"}, args.refresh)
    rows = build_rows(works, truncated, works_counts, scope)

    loaded = defaultdict(int)  # loaded works whose primary topic is a chosen topic, per year
    for w in works.values():
        if short((w.get("primary_topic") or {}).get("id")) in TOPICS:
            loaded[w.get("publication_year")] += 1
    meta = {
        "graph": GRAPH, "slug": SLUG, "config_hash": config_hash(CONFIG),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_version": "openalex@" + min(fetch_dates)[:10] + (f"+until{until}" if until else ""),  # oldest cache used
        "load_scope": (f"all works funded by {' or '.join(FUNDER_KIND) or 'no funder'} (any topic, any year); "
                       f"all works with an author affiliated to {'|'.join(COKE_FAMILY) or 'no institution'} "
                       f"(lineage, any topic); "
                       f"plus the {PER_YEAR} most-cited works per year {years[0]}-{years[-1]} with primary "
                       f"topic in {list(TOPICS)}; co-authors' other works not loaded; topic works list at "
                       f"most {LIST_TRUNCATION} authors each; duplicate records merged by DOI and by "
                       f"title+year+authors"
                       + (f"; STAGED DEMO SLICE: only works published up to {until}" if until else "")),
        **({"until_year": until} if until else {}),
        "topic_ids": list(TOPICS), "funder_ids": list(FUNDER_KIND),
        "topic_cap": TOPIC_CAP, "topic_per_year": PER_YEAR,
        "topic_total": sum(year_totals.get(y, 0) for y in years),
        "years": years, "years_loaded": [loaded[y] for y in years],
        "years_total": [year_totals.get(y, 0) for y in years],
        "small_paper": SMALL_PAPER, "recent_year": RECENT_YEAR,
        "truncated_works": len(truncated), "duplicates_removed": removed,
        **rows["stats"],
    }
    def record_new_build(m):  # status stays "building" until main() writes "ready"
        write_status("building", built_at=m["built_at"], data_version=m["data_version"], config_hash=m["config_hash"])
    counts, size_mb = write_graph(rows, meta, args.rebuild, before_swap=record_new_build)
    n_scope = {s: list(scope[w] for w in works).count(s) for s in ("funded", "family", "topic")}
    print(f"works loaded: {len(works)} {n_scope}; duplicates removed: {removed}")
    print(f"truncated author lists (topic works, n_authors is a lower bound): {len(truncated)}")
    print("per year loaded/total:", {y: f"{loaded[y]}/{year_totals.get(y, 0)}" for y in years})
    print("counts:", json.dumps(counts))
    return counts, size_mb, meta


if __name__ == "__main__":
    main()
