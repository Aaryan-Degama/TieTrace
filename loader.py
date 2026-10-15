"""Checkpoint 2: build the research graph `research:obesity-coke` from OpenAlex.

Run:  uv run python loader.py --rebuild
      uv run python loader.py --rebuild --refresh   # ignore data/raw/ and call the API again

The graph is dropped and rebuilt from scratch on every load (--rebuild is required if it
already exists), so re-running gives the same graph. Raw API pages are cached in data/raw/
as JSONL (line 1 = query params + fetch date), so a re-run costs zero API calls.
"""
import argparse
import json
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx
from falkordb import FalkorDB

GRAPH = "research:obesity-coke"
RAW = Path(__file__).parent / "data" / "raw"
API = "https://api.openalex.org/"
WORK_FIELDS = "id,doi,title,publication_year,authorships,funders,topics,primary_topic"

COMPANY_FUNDER = "F4320307807"     # Coca-Cola (the company): Tier A1
FOUNDATION_FUNDER = "F4320308599"  # Coca-Cola Foundation: Tier A2
FUNDER_KIND = {COMPANY_FUNDER: "company", FOUNDATION_FUNDER: "foundation"}

# Chosen from /topics?search=obesity and ?search=physical activity (see AI_USE.md / report).
TOPICS = {
    "T10010": "Obesity, Physical Activity, Diet",
    "T10352": "Physical Activity and Health",
}
TOPIC_YEARS = range(2010, 2027)  # topic works "since 2010", up to the current year 2026
# ponytail: topic works capped at 30,000 of ~260k: the TOPIC_CAP // 17 most cited per year,
# so recent years are not crowded out. Raise TOPIC_CAP (1 API call per 100 works) for coverage.
TOPIC_CAP = 30000
PER_YEAR = TOPIC_CAP // len(TOPIC_YEARS)

# Coca-Cola corporate family. OpenAlex gives I1312732213 no associated_institutions, so this
# list was hand-checked on 2026-10-15 from /institutions?search=coca-cola, ?search=coca cola
# and filter=display_name.search:coca / :cola. No Coca-Cola Foundation institution exists.
COKE_PARENT = "I1312732213"
COKE_FAMILY = {
    "I1312732213": "Coca Cola (United States)",
    "I4405274843": "Coca-Cola Bottlers Japan Inc. (Japan)",
    "I4405273583": "COCA-COLA WEST Company, Limited (Japan)",
}
# PARENT_OF.rel: "subsidiary" (owned) or "bottler" (franchise bottler, TCCC a minority owner).
COKE_CHILD_REL = {"I4405274843": "bottler", "I4405273583": "bottler"}

SMALL_PAPER = 25       # Tier rules: papers with more authors are "large"
RECENT_YEAR = 2021     # "last 5 years" relative to 2026; to be confirmed by the human
LIST_TRUNCATION = 100  # OpenAlex list endpoints return at most 100 authorships per work
# ponytail: works_count above this is treated as a likely merged profile; a guess, not a study.
WORKS_COUNT_ANOMALY = 2000
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
    for attempt in range(TRIES):
        with calls_lock:
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
            ids.sort(key=lambda i: (rank[scope[i]], i))
            for drop in ids[1:]:
                del works[drop]
                removed += 1
    return removed


def load_works(refresh):
    funded = fetch_pages("funded", {"filter": f"funders.id:{COMPANY_FUNDER}|{FOUNDATION_FUNDER}",
                                    "select": WORK_FIELDS}, 10**9, refresh)
    family = fetch_pages("family", {"filter": "authorships.institutions.lineage:" + "|".join(COKE_FAMILY),
                                    "select": WORK_FIELDS}, 10**9, refresh)
    topic_ids = "|".join(TOPICS)
    topic = []
    for year in TOPIC_YEARS:
        topic += fetch_pages(f"topic_{year}", {"filter": f"primary_topic.id:{topic_ids},publication_year:{year}",
                                               "select": WORK_FIELDS, "sort": "cited_by_count:desc"},
                             PER_YEAR, refresh)
    groups = fetch_one("topic_years", "works", {
        "filter": f"primary_topic.id:{topic_ids},publication_year:{TOPIC_YEARS[0]}-{TOPIC_YEARS[-1]}",
        "group_by": "publication_year"}, refresh)
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
               "scope": (scope or {}).get(wid)}
        if wid in truncated:
            row["authors_truncated"] = True  # n_authors is a lower bound (list API cap)
        work_rows.append(row)
        seen = set()
        for a in w["authorships"]:
            pid = short(a["author"].get("id"))
            if not pid or pid in seen:  # first slot of each author only
                continue
            seen.add(pid)
            orcid = a["author"].get("orcid")
            persons[pid] = {"oa_id": pid, "name": a["author"].get("display_name"),
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
    return rows


def coauth_rows(works, authors_of):
    """One row per ordered pair (both directions), with the COAUTH weights.
    authors_of[wid] = (distinct author ids, n_authors). A missing year is stored as 0."""
    pairs = defaultdict(lambda: {"n_papers": 0, "w": 0.0, "small_papers": 0, "recency_w": 0.0,
                                 "recent_small_papers": 0, "recent_small_w": 0.0,
                                 "dois": [], "years": [], "works": []})
    # No size cap: the full load gives ~0.9M pairs, so O(n^2) on big papers is affordable.
    for wid, (authors, n) in authors_of.items():
        if len(authors) < 2:
            continue
        w = works[wid]
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
    rows = []
    for (a, b), e in pairs.items():
        rows.append({"a": a, "b": b, **e})
        rows.append({"a": b, "b": a, **e})
    return rows


QUERIES = {
    "persons": "UNWIND $rows AS r CREATE (:Person {oa_id:r.oa_id, name:r.name, orcid:r.orcid, "
               "works_count:r.works_count, profile_flag:r.profile_flag})",
    "works": "UNWIND $rows AS r CREATE (w:Work {oa_id:r.oa_id, doi:r.doi, year:r.year, "
             "n_authors:r.n_authors, title:r.title, scope:r.scope}) SET w.authors_truncated = r.authors_truncated",
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
                "recent_small_w:r.recent_small_w, dois:r.dois, works:r.works}]->(b)",
}

INDEXES = [
    "CREATE INDEX FOR (p:Person) ON (p.oa_id)",
    "CREATE INDEX FOR (w:Work) ON (w.oa_id)",
    "CREATE INDEX FOR (w:Work) ON (w.doi)",
    "CREATE INDEX FOR (o:Org) ON (o.oa_id)",
    "CREATE INDEX FOR (f:Funder) ON (f.oa_id)",
    "CREATE INDEX FOR (t:Topic) ON (t.oa_id)",
    "CREATE FULLTEXT INDEX FOR (w:Work) ON (w.title)",
]


def write_graph(rows, meta, rebuild):
    db = FalkorDB()
    if GRAPH in db.list_graphs():
        if not rebuild:
            raise SystemExit(f"{GRAPH} already exists; pass --rebuild to drop and rebuild it")
        db.select_graph(GRAPH).delete()
    g = db.select_graph(GRAPH)
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
    meta = {**meta, "counts": json.dumps(counts)}
    g.query("CREATE (m:Meta) SET m = $meta", {"meta": meta})
    return counts


def main():
    global KEY
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="ignore data/raw/ and call the API")
    ap.add_argument("--rebuild", action="store_true", help="drop the graph if it exists")
    args = ap.parse_args()
    KEY = api_key()
    RAW.mkdir(parents=True, exist_ok=True)
    start = time.time()

    works, scope, truncated, year_totals, removed = load_works(args.refresh)
    person_ids = sorted({p for w in works.values() for p in author_slots(w)[0]})
    works_counts = fetch_by_id("authors", person_ids, fetch_works_counts,
                               {"select": "id,works_count"}, args.refresh)
    rows = build_rows(works, truncated, works_counts, scope)

    years = list(TOPIC_YEARS)
    loaded = defaultdict(int)  # loaded works whose primary topic is a chosen topic, per year
    for w in works.values():
        if short((w.get("primary_topic") or {}).get("id")) in TOPICS:
            loaded[w.get("publication_year")] += 1
    meta = {
        "graph": GRAPH,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_version": "openalex@" + min(fetch_dates)[:10],  # oldest cache used
        "load_scope": (f"all works funded by {COMPANY_FUNDER} or {FOUNDATION_FUNDER} (any topic, any year); "
                       f"all works with an author affiliated to {'|'.join(COKE_FAMILY)} (lineage, any topic); "
                       f"plus the {PER_YEAR} most-cited works per year {years[0]}-{years[-1]} with primary "
                       f"topic in {list(TOPICS)}; co-authors' other works not loaded; topic works list at "
                       f"most {LIST_TRUNCATION} authors each; duplicate records merged by DOI and by "
                       f"title+year+authors"),
        "topic_ids": list(TOPICS), "funder_ids": list(FUNDER_KIND),
        "topic_cap": TOPIC_CAP, "topic_per_year": PER_YEAR,
        "topic_total": sum(year_totals.get(y, 0) for y in years),
        "years": years, "years_loaded": [loaded[y] for y in years],
        "years_total": [year_totals.get(y, 0) for y in years],
        "small_paper": SMALL_PAPER, "recent_year": RECENT_YEAR,
        "truncated_works": len(truncated), "duplicates_removed": removed,
    }
    counts = write_graph(rows, meta, args.rebuild)
    n_scope = {s: list(scope[w] for w in works).count(s) for s in ("funded", "family", "topic")}
    print(f"works loaded: {len(works)} {n_scope}; duplicates removed: {removed}")
    print(f"truncated author lists (topic works, n_authors is a lower bound): {len(truncated)}")
    print("per year loaded/total:", {y: f"{loaded[y]}/{year_totals.get(y, 0)}" for y in years})
    print("counts:", json.dumps(counts))
    print(f"API calls this run: {api_calls}; wall clock: {time.time() - start:.0f}s")


if __name__ == "__main__":
    main()
