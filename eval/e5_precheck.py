"""Last updated by Aaryan, Oct 17 2026: wrote the E5 AI pre-check.
E5 (profile guard), step 2: a pre-check that HELPS the human who marks eval/e5_sample.md. It is a
suggestion, never a verdict, and no error rate is computed from it.

For each of the 50 sampled authors it gathers evidence from the OpenAlex API and applies the small
written-down rule in `suggest()`:
  merged signals: publishing span > 60 years; two research domains (each >= 25% and >= 3 of the sampled
                  works) whose works share no co-author; >= 3 countries of affiliation in the same year.
  split signal:   another profile with the same surname and first initial that shares an institution with
                  this profile, or has a work together with one of its frequent co-authors.
  no signal -> ok; both kinds, missing evidence, or too many same-name profiles to check -> unclear.
Raw responses are cached under data/raw/e5/, so a re-run makes zero API calls. Names stay in the cache
(gitignored); the markdown shows only OpenAlex IDs.
Limits: the same-name search reads one page (per_page 100), so profiles past the first 100 are not counted;
cache files are keyed by name only, so after changing per_page or select, delete data/raw/e5/ first.
Run:  uv run python eval/e5_precheck.py   -> adds the column `AI suggestion (not a verdict)` to eval/e5_sample.md
"""
import json
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "eval" / "e5_sample.md"
CACHE = ROOT / "data" / "raw" / "e5"
API = "https://api.openalex.org/"
TRIES, SLEEP = 5, 0.2
COL = "AI suggestion (not a verdict)"
MAX_SPAN, MIN_SHARE, MIN_WORKS, MAX_COUNTRIES = 60, 0.25, 3, 3
MAX_CANDIDATES = 25  # same-name profiles checked per author; more than this -> unclear
api_calls = 0


def api_key():  # same as loader.py; the key is never printed, logged or written
    text = (ROOT / ".env").read_text(encoding="utf-8")
    return re.search(r"OPENALEX_API_KEY=(\S+)", text).group(1).strip("\"'")


def get(name, path, params):
    """One cached OpenAlex GET. Errors name the path only: the real URL holds the API key."""
    global api_calls
    f = CACHE / f"{name}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    for attempt in range(TRIES):
        time.sleep(SLEEP)
        api_calls += 1
        try:
            r = httpx.get(API + path, params={**params, "api_key": api_key()}, timeout=30)
        except httpx.HTTPError as e:
            print(f"OpenAlex {type(e).__name__} on {path}, retry {attempt + 1}/{TRIES}", flush=True)
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            CACHE.mkdir(parents=True, exist_ok=True)
            f.write_text(r.text, encoding="utf-8")
            return r.json()
        if r.status_code not in (429, 500, 502, 503, 504):
            raise RuntimeError(f"OpenAlex {r.status_code} on {path}")
        print(f"OpenAlex {r.status_code} on {path}, retry {attempt + 1}/{TRIES}", flush=True)
        time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"OpenAlex failed {TRIES} times on {path}")


def short(url):
    return url.rsplit("/", 1)[-1] if url else None


def name_key(name):
    """'Steven N. Blair' and 'S. N. Blair' -> ('blair', 's'): surname plus first initial."""
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    parts = re.findall(r"[a-z]+", plain)
    return (parts[-1], parts[0][0]) if len(parts) >= 2 else None


def gather(aid):
    """All evidence for one author, straight from (cached) API responses."""
    a = get(f"author_{aid}", f"authors/{aid}", {})
    years = get(f"years_{aid}", "works", {"filter": f"author.id:{aid}", "group_by": "publication_year"})
    works = get(f"works_{aid}", "works", {"filter": f"author.id:{aid}", "per_page": 200,
                                          "select": "id,publication_year,primary_topic,authorships"})["results"]
    ev = {"works_count": a.get("works_count"), "orcid": bool(a.get("orcid")),
          "n_last_known": len(a.get("last_known_institutions") or []), "sample": len(works), "missing": []}
    ys = [int(g["key"]) for g in years.get("group_by", []) if str(g["key"]).isdigit()]
    ev["years"] = (min(ys), max(ys)) if ys else None
    if not ys:
        ev["missing"].append("no publication years")
    if not works:
        ev["missing"].append("no works returned")

    # works: fields, ORCID on the authorship, co-authors per domain
    fields, domains, coauth, orcid_listed = set(), {}, Counter(), 0
    for w in works:
        pt = w.get("primary_topic") or {}
        if pt.get("field"):
            fields.add(pt["field"]["display_name"])
        others = set()
        for au in w.get("authorships") or []:
            pid = short((au.get("author") or {}).get("id"))
            if pid == aid:
                orcid_listed += bool((au.get("author") or {}).get("orcid"))
            elif pid:
                others.add(pid)
        coauth.update(others)
        if pt.get("domain"):
            domains.setdefault(pt["domain"]["display_name"], []).append(others)
    ev["n_fields"], ev["orcid_listed"] = len(fields), orcid_listed
    big = {d: ws for d, ws in domains.items() if len(ws) >= MIN_WORKS and len(ws) >= MIN_SHARE * len(works)}
    names = sorted(big)
    ev["far_domains"] = [(d1, d2) for i, d1 in enumerate(names) for d2 in names[i + 1:]
                         if not set().union(*big[d1]) & set().union(*big[d2])]

    # affiliations: countries per year
    per_year = {}
    for af in a.get("affiliations") or []:
        cc = (af.get("institution") or {}).get("country_code")
        for y in af.get("years") or []:
            if cc:
                per_year.setdefault(y, set()).add(cc)
    worst = max(per_year.items(), key=lambda kv: (len(kv[1]), kv[0]), default=(None, set()))
    ev["same_year_countries"] = (worst[0], sorted(worst[1]))

    # same-name profiles: shared institution or a work with one of this profile's frequent co-authors
    my_inst = {short(af["institution"]["id"]) for af in a.get("affiliations") or [] if af.get("institution")}
    key = name_key(a.get("display_name"))
    ev["same_name"], ev["split_hits"], ev["unchecked"] = 0, [], 0
    if key is None:
        ev["missing"].append("name too short to search")
        return ev
    found = get(f"search_{aid}", "authors", {"search": a["display_name"], "per_page": 100})["results"]
    cands = [c for c in found if short(c["id"]) != aid and name_key(c.get("display_name")) == key]
    ev["same_name"] = len(cands)
    ev["unchecked"] = max(0, len(cands) - MAX_CANDIDATES)
    cands = cands[:MAX_CANDIDATES]
    for c in cands:
        shared = my_inst & {short(af["institution"]["id"]) for af in c.get("affiliations") or [] if af.get("institution")}
        if shared:
            ev["split_hits"].append((short(c["id"]), f"shares institution {sorted(shared)[0]}"))
    # frequent = in >= 2 sampled works; a profile with < 3 sampled works counts all its co-authors
    freq = [p for p, n in coauth.most_common(50) if n >= 2 or len(works) < 3]
    if cands and freq:
        ids = [short(c["id"]) for c in cands]
        both = get(f"cowork_{aid}", "works", {"filter": f"author.id:{'|'.join(ids)},author.id:{'|'.join(freq)}",
                                               "per_page": 200, "select": "id,authorships"})["results"]
        for w in both:
            on = {short((au.get("author") or {}).get("id")) for au in w.get("authorships") or []}
            for cid in ids:
                if cid in on and on & set(freq) and not any(h[0] == cid for h in ev["split_hits"]):
                    ev["split_hits"].append((cid, f"co-authored {short(w['id'])} with a frequent co-author"))
    return ev


def suggest(ev):
    """The written-down rule. Returns (word, reason); word is merged, split, ok or unclear."""
    merged = []
    if ev.get("years") and ev["years"][1] - ev["years"][0] > MAX_SPAN:
        merged.append(f"works span {ev['years'][0]}-{ev['years'][1]}")
    for d1, d2 in ev.get("far_domains") or []:
        merged.append(f"{d1} and {d2} works share no co-author")
    year, ccs = ev.get("same_year_countries") or (None, [])
    if len(ccs) >= MAX_COUNTRIES:
        merged.append(f"{len(ccs)} countries in {year} ({', '.join(ccs)})")
    hits = ev.get("split_hits") or []
    split = [f"{len(hits)} same-name profile(s) linked, e.g. " + "; ".join(f"{c} {why}" for c, why in hits[:2])] if hits else []
    if merged and split:
        return "unclear", "both kinds of signal: " + "; ".join(merged + split)
    if merged:
        return "merged", "; ".join(merged)
    if split:
        return "split", split[0]
    if ev.get("missing"):
        return "unclear", "not enough evidence: " + ", ".join(ev["missing"])
    if ev.get("unchecked"):
        return "unclear", f"{ev['same_name']} same-name profiles, only {ev['same_name'] - ev['unchecked']} checked"
    y = f"{ev['years'][0]}-{ev['years'][1]}"
    return "ok", (f"no signal (span {y}, {ev['n_fields']} fields in {ev['sample']} works, "
                  f"{ev['same_name']} same-name profiles share nothing)")


def main():
    lines = MD.read_text(encoding="utf-8").splitlines()
    counts, out = Counter(), []
    rerun = any(COL in line for line in lines)
    for line in lines:
        cells = line.split("|")[1:-1] if line.startswith("|") else None
        if cells and rerun:  # re-run: drop the old AI column
            cells = cells[:-1]
        if cells is None:
            out.append(line)
        elif cells[0].strip() == "#":
            out.append("|" + "|".join(cells) + f"| {COL} |")
        elif set(cells[0].strip()) == {"-"}:
            out.append("|" + "|".join(cells) + "|---|")
        else:
            aid = re.search(r"\[(A\d+)\]", cells[1]).group(1)
            word, reason = suggest(gather(aid))
            counts[word] += 1
            print(f"#{cells[0].strip()} {aid}: {word}: {reason}", flush=True)
            out.append("|" + "|".join(cells) + f"| {word}: {reason.replace('|', '/')} |")
    MD.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"suggestions: {dict(sorted(counts.items()))}; API calls this run: {api_calls}")


if __name__ == "__main__":
    main()
