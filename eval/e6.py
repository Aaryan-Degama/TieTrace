"""Last updated by Abdul, Oct 16 2026: wrote E6.
E6: mega-paper ablation (redesign section 7). Tier A and Tier B counts with vs without the author cap.

Rule (fixed before running, not tuned): the cap is Meta.small_paper (25 authors). A paper with more authors is
"large". With the cap, a large company-funded paper gives only A2 and a large paper does not count towards a
Tier B link; without the cap both count in full. Report the share of ties that depend on large papers.
  * Tier A membership (A1 or A2) does not depend on the cap; the split A1 / A2 does.
  * Tier B = not Tier A, and 2+ recent (since Meta.recent_year) papers together with a Tier-A person
    (with the cap: small papers only, which is the stored COAUTH_B link; without: COAUTH, any size).
Read-only (GRAPH.RO_QUERY through research.ro_query); no rebuild.
Run:  FALKORDB_PORT=6381 uv run python eval/e6.py
"""
import time

import common as c
import research

start = time.time()
meta = research._meta([])
SMALL, RECENT = meta["small_paper"], meta["recent_year"]
NO_CAP = 10 ** 9


def tier_a(small):
    """{person: 'A1'|'A2'} with the same two queries as research._tier_a, but with the cap `small` (no verdicts)."""
    funded = c.rq("MATCH (p:Person)-[:AUTHORED]->(w:Work)-[:FUNDED_BY]->(f:Funder {target:true}) WHERE NOT w.junk "
                  "RETURN p.oa_id, max(CASE WHEN f.kind = 'company' AND w.n_authors <= $small THEN 1 ELSE 0 END)",
                  {"small": small}, row_cap=research.BIG)
    affil = c.rq("MATCH (p:Person)-[a:AFFILIATED]->(o:Org {target:true}) WITH p, a, o "
                 "MATCH (w:Work) WHERE w.oa_id = a.work AND NOT w.junk "
                 "OPTIONAL MATCH (:Org)-[po:PARENT_OF]->(o) "
                 "RETURN p.oa_id, max(CASE WHEN coalesce(po.rel,'') <> 'bottler' AND w.n_authors <= $small THEN 1 ELSE 0 END)",
                 {"small": small}, row_cap=research.BIG)
    best = {}
    for pid, is_a1 in funded + affil:
        best[pid] = max(best.get(pid, 0), is_a1)
    return {pid: "A1" if v else "A2" for pid, v in best.items()}


def tier_b(anchors, capped):
    """People with a link of 2+ recent papers to one of the anchor (Tier A) people; still includes Tier A people."""
    if capped:  # the stored COAUTH_B link: 2+ recent small papers
        q = ("MATCH (a:Person) WHERE a.oa_id IN $ids MATCH (a)-[c:COAUTH_B]->(e:Person) "
             "RETURN DISTINCT e.oa_id")
    else:       # COAUTH keeps every shared paper's year, so count the recent ones whatever the paper size
        q = ("MATCH (a:Person) WHERE a.oa_id IN $ids MATCH (a)-[c:COAUTH]->(e:Person) "
             "WITH e, size([y IN c.years WHERE y >= $recent]) AS n WHERE n >= 2 RETURN DISTINCT e.oa_id")
    found, ids, size = set(), sorted(anchors), 100
    i = 0
    while i < len(ids):
        try:
            found |= {r[0] for r in c.rq(q, {"ids": ids[i:i + size], "recent": RECENT}, timeout_ms=300000)}
            i += size
        except research.ToolError as e:  # a page reached the row cap: use a smaller slice
            if e.code != "ROW_LIMIT" or size == 1:
                raise
            size = max(1, size // 4)
    return found


capped, uncapped = tier_a(SMALL), tier_a(NO_CAP)
assert capped == research._tier_a([]), "the capped copy of the Tier A queries must equal research._tier_a"
assert set(capped) == set(uncapped), "Tier A membership must not depend on the cap"
assert all(uncapped[p] == "A1" for p in capped if capped[p] == "A1"), "capped A1 must stay A1 without the cap"
a1_cap = {p for p, t in capped.items() if t == "A1"}
a1_no = {p for p, t in uncapped.items() if t == "A1"}
print(f"cap = {SMALL} authors (Meta.small_paper); recent = {RECENT} onwards; graph {research.GRAPH}, {meta['data_version']}")
print(f"Tier A people (A1 + A2): {len(capped)}  (same with and without the cap)")
print(f"  A1 with cap {len(a1_cap)}, without cap {len(a1_no)}; A2 with cap {len(capped) - len(a1_cap)}, "
      f"without cap {len(capped) - len(a1_no)}")
print(f"  A1 that exist only because the cap is off (their company evidence is all on large papers): {len(a1_no - a1_cap)}"
      f" = {100 * len(a1_no - a1_cap) / len(a1_no):.1f}% of the A1 people found without the cap")

anchors = set(capped)
b_cap = tier_b(anchors, True) - anchors
b_no = tier_b(anchors, False) - anchors
assert b_cap <= b_no, "a capped Tier B link must also exist without the cap"
print(f"Tier B people (not Tier A; 2+ recent papers with a Tier-A person): with cap {len(b_cap)}, without cap {len(b_no)}")
print(f"  Tier B people that exist only because the cap is off: {len(b_no - b_cap)} = "
      f"{100 * len(b_no - b_cap) / len(b_no):.1f}% of the Tier B people found without the cap")
ties_no, ties_cap = len(a1_no) + len(b_no), len(a1_cap) + len(b_cap)
print(f"A1 + B people together: with cap {ties_cap}, without cap {ties_no}; "
      f"{100 * (ties_no - ties_cap) / ties_no:.1f}% of the no-cap ties depend on large papers")
print(f"(took {time.time() - start:.0f}s)")
