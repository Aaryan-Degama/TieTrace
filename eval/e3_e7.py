"""E7 (coverage and load scope) and E3 (known case: GEBN leaders) on research:obesity-coke.
Run:  FALKORDB_PORT=6380 uv run python eval/e3_e7.py
"""
import time
from collections import Counter, defaultdict

import common as c
import loader
import research

start = time.time()
print("# E7: coverage and load scope\n")
meta = research._meta([])
works = c.load_works()
topic_ids = [w for w, v in works.items() if v["topic"]]

# Separate recount from the graph (the loader's own numbers are in Meta).
q_funder = "MATCH (w:Work) WHERE w.oa_id IN $ids MATCH (w)-[:FUNDED_BY]->() RETURN DISTINCT w.oa_id"
has_funder = {r[0] for r in c.by_ids(q_funder, topic_ids)}
# an affiliation recorded on that work: the AFFILIATED edge carries the id of the work it came from
q_aff = ("MATCH (w:Work) WHERE w.oa_id IN $ids MATCH (p:Person)-[:AUTHORED]->(w) WITH w, p "
         "MATCH (p)-[a:AFFILIATED]->() WHERE a.work = w.oa_id RETURN DISTINCT w.oa_id")
has_aff = {r[0] for r in c.by_ids(q_aff, topic_ids, size=300)}
n = len(topic_ids)
fa = len(has_funder | has_aff)
print(f"topic works (ABOUT edge): graph recount {n}, Meta {meta['topic_works']}")
print(f"with funder data: graph recount {len(has_funder)} = {100*len(has_funder)/n:.1f}%, "
      f"Meta {meta['topic_with_funder']} = {100*meta['topic_with_funder']/meta['topic_works']:.1f}%")
print(f"with funder or affiliation data: graph recount {fa} = {100*fa/n:.1f}%, "
      f"Meta {meta['topic_with_funder_or_affiliation']} = "
      f"{100*meta['topic_with_funder_or_affiliation']/meta['topic_works']:.1f}%")
print(f"with affiliation data only (no funder): {len(has_aff - has_funder)}; with neither: {n - fa}")

NON_PAPER = loader.NON_PAPER_TITLES
is_np = lambda v: (v["title"] or "").strip().lower() in NON_PAPER
print("\nWork records in the graph:", len(works), dict(Counter(v["scope"] for v in works.values())))
print("junk (F1000 report / correction / retraction notice):",
      sum(1 for v in works.values() if not v["paper"] and not is_np(v)),
      "| Index/Front Matter (non_paper):", sum(1 for v in works.values() if is_np(v)),
      "| untitled:", sum(1 for v in works.values() if v["untitled"]),
      f"| Meta: junk {meta['junk_works']}, non_paper {meta['non_paper_works']}, untitled {meta['title_unknown_works']}")
print("truncated author lists (topic works, n_authors is a lower bound): graph",
      sum(1 for v in works.values() if v["truncated"]), "| Meta", meta["truncated_works"])
groups = defaultdict(list)  # same normalised title and year = possible duplicate record
for w, v in works.items():
    t = loader.norm_title(v["title"])
    if t:
        groups[(t, v["year"])].append(w)
dups = {k: g for k, g in groups.items() if len(g) > 1}
print(f"possible duplicate groups (same title+year): graph {len(dups)} groups, "
      f"{sum(len(g) for g in dups.values())} records | Meta {meta['dup_groups']} groups, {meta['dup_records']} records; "
      f"loader removed {meta['duplicates_removed']} exact duplicates; {meta['dup_links']} Tier B/C links depend on a leftover duplicate")

print("\nLoad scope (Meta):", meta["load_scope"])
print(f"topic load = {meta['topic_per_year']} most-cited works per year, a SAMPLE. Per year: loaded/total "
      "(primary topic, Meta), works with an ABOUT edge in the graph, and funder / funder-or-affiliation coverage:")
for y, lo, to in zip(meta["years"], meta["years_loaded"], meta["years_total"]):
    ty = [w for w in topic_ids if works[w]["year"] == y]
    fy = sum(1 for w in ty if w in has_funder)
    fay = sum(1 for w in ty if w in has_funder or w in has_aff)
    print(f"  {y}: {lo}/{to} ({100*lo/to:.1f}%)  ABOUT-works {len(ty)}  funder {100*fy/len(ty):.1f}%  "
          f"funder-or-affil {100*fay/len(ty):.1f}%")
print(f"  total topic: {sum(meta['years_loaded'])} loaded of {meta['topic_total']} "
      f"({100*sum(meta['years_loaded'])/meta['topic_total']:.1f}%)")
print("works in the 'funded' scope (company or foundation funder, any topic):",
      sum(1 for v in works.values() if v["scope"] == "funded"),
      "| 'family' scope (Coca-Cola-affiliated author):", sum(1 for v in works.values() if v["scope"] == "family"))
print("Tier A people:", len(research._tier_a([])))
print(f"(E7 part took {time.time()-start:.0f}s)")

# ---------------- E3 ----------------
print("\n# E3: known case (GEBN leaders)\n")
# Names confirmed in a public source (cited in results.md). The control is a person NOT named in any public GEBN source.
LEADERS = ["James O. Hill", "Steven N. Blair", "Gregory A. Hand", "John C. Peters"]
CONTROL = ["Control #1"]
for name in LEADERS:
    m = research.resolve_experts([name], "obesity")["data"]["matches"][0]
    print(f"resolve_experts('{name}'): {m['status']}, candidates "
          f"{[(k['expert_id'], k['works_on_topic'], k['last_known_org']) for k in m['candidates']]}")

# Hand-chosen profiles (where resolve_experts says AMBIGUOUS, see the split-profile check below for why).
CHOSEN = {"James O. Hill": "A5031318756", "Steven N. Blair": "A5105003630",
          "Gregory A. Hand": "A5046953968", "John C. Peters": "A5045252457", "Control #1": "A5088704695"}
print()
ok = 0
for name, pid in CHOSEN.items():
    d = research.tie_report(pid, label=name, public_case=name in LEADERS)["data"]
    ev = d["evidence"]
    with_doi = [e for e in ev if e["doi"]]
    print(f"{name} ({pid}): tier {d['tier']}, score {d['score']}, {len(ev)} evidence items on {len({e['work_id'] for e in ev})} papers, {len(with_doi)} with a DOI "
          f"({sum(e['tier']=='A1' for e in ev)} A1, {sum(e['tier']=='A2' for e in ev)} A2; "
          f"{sum(e['topic_label']=='on-topic' for e in ev)} on-topic); profile_flag {d['profile_flag']}")
    if ev:
        e = ev[0]
        print(f"   first: {e['tier']} {e['kind']} {e['doi']} ({e['year']}) via {e['via']} - {e['reason']}")
        print("   wording:", d["wording"])
    if name in LEADERS and d["tier"] in ("A1", "A2") and with_doi:
        ok += 1
print(f"\nLeaders named in a public source found as Tier A with DOIs: {ok} of {len(LEADERS)}")

# Split profiles: other OpenAlex records with the same name; do they share co-authors with the chosen one?
print("\nSplit-profile check:")
tier_a = research._tier_a([])
SPLIT = {"Steven N. Blair": ["A5112298044", "A5087575650"], "Gregory A. Hand": ["A5109223784"]}
nb_q = "MATCH (a:Person) WHERE a.oa_id = $i MATCH (a)-[:COAUTH]->(b:Person) RETURN b.oa_id"
for name, others in SPLIT.items():
    main = CHOSEN[name]
    main_nb = {r[0] for r in c.rq(nb_q, {"i": main})}
    for o in others:
        p = c.rq("MATCH (p:Person) WHERE p.oa_id = $i RETURN p.name, p.works_count, p.orcid", {"i": o})[0]
        wk = c.rq("MATCH (p:Person) WHERE p.oa_id = $i MATCH (p)-[:AUTHORED]->(w:Work) RETURN w.oa_id, w.doi, w.year", {"i": o})
        org = c.rq("MATCH (p:Person) WHERE p.oa_id = $i MATCH (p)-[a:AFFILIATED]->(g:Org) RETURN DISTINCT g.name", {"i": o})
        co = {r[0] for r in c.rq(nb_q, {"i": o})}
        print(f"  {name}: other record {o} '{p[0]}' works_count {p[1]}, orcid {p[2]}, loaded works "
              f"{[(w[1], w[2]) for w in wk]}, affiliations {[x[0] for x in org]}, co-authors {len(co)}, "
              f"shared with chosen profile {len(co & main_nb)}, Tier A: {o in tier_a}")
print(f"\ntotal runtime {time.time()-start:.0f}s")
