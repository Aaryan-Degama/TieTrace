"""Last updated by Abdul, Oct 17 2026: wrote E4.
E4: max flow vs the plain Cypher aggregate (redesign sections 5.3 and 7).
Pass rule (fixed before running): flow earns a claim only if Spearman rho < 0.95 OR the top 10 changes;
otherwise the max-flow claim is dropped and README says it was tested.
Population: people in the graph who are not Tier A and have at least one COAUTH_B link (2+ recent small papers
with someone). SAMPLE people are drawn with a fixed seed. For each person, over the COAUTH_B links (both directions stored):
  * aggregate = Cypher: sum over paths of 1 to 3 links to a Tier-A person of the path's smallest link strength
    (strength = recent_small_papers, a whole number);
  * flow = algo.maxFlow from the person to all Tier-A people, capacity = recent_small_papers (same strength).
algo.maxFlow has no depth limit (it runs on the whole graph), so the flow may use routes longer than 3 links; the
script counts how often. Strength is a whole number because algo.maxFlow refused the decimal weights
(recent_small_w, w: "capacity range too wide"), see eval/results.md.
Read-only (GRAPH.RO_QUERY through research.ro_query). Run:  FALKORDB_PORT=6381 uv run python eval/e4.py
"""
import random
import time

import common as c
import research

SEED, SAMPLE = 20261016, 200
TOP = 10


def ranks(values):
    """1-based ranks; tied values share the average of their ranks."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out, i = [0.0] * len(values), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(x, y):
    """Spearman rho = Pearson correlation of the ranks (ties get average ranks). None if one side is constant."""
    rx, ry = ranks(x), ranks(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx, syy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return None if sxx == 0 or syy == 0 else sxy / (sxx * syy) ** 0.5


AGG_Q = ("MATCH p=(e:Person {oa_id: $e})-[:COAUTH_B*1..3]->(a:Person) WHERE a.oa_id IN $ta "
         "RETURN count(p), sum(reduce(m = 1000000, r IN relationships(p) | "
         "CASE WHEN r.recent_small_papers < m THEN r.recent_small_papers ELSE m END))")
FLOW_Q = ("MATCH (s:Person {oa_id: $e}) MATCH (t:Person) WHERE t.oa_id IN $ta WITH s, collect(t) AS ts "
          "CALL algo.maxFlow({sourceNodes: [s], targetNodes: ts, capacityProperty: 'recent_small_papers', "
          "relationshipTypes: ['COAUTH_B']}) YIELD maxFlow RETURN maxFlow")

if __name__ == "__main__":
    start = time.time()
    ta = sorted(research._tier_a([]))
    in_ta = set(ta)
    people = sorted({r[0] for r in c.rq("MATCH (p:Person)-[:COAUTH_B]->(:Person) RETURN DISTINCT p.oa_id", row_cap=9999)} - in_ta)
    sample = sorted(random.Random(SEED).sample(people, SAMPLE))
    print(f"population: {len(people)} non-Tier-A people with a COAUTH_B link; sample {SAMPLE} (seed {SEED}); Tier A {len(ta)}")
    rows, failed, t_agg, t_flow = [], [], 0.0, 0.0
    for e in sample:
        try:
            t0 = time.time()
            paths, agg = c.rq(AGG_Q, {"e": e, "ta": ta}, timeout_ms=60000)[0]
            t1 = time.time()
            flow = c.rq(FLOW_Q, {"e": e, "ta": ta}, timeout_ms=120000)[0][0]
            t_agg, t_flow = t_agg + t1 - t0, t_flow + time.time() - t1
            rows.append((e, paths, agg or 0, flow))
        except research.ToolError as err:
            failed.append((e, err.code, str(err)[:120]))
    print(f"computed {len(rows)}, failed {len(failed)} {failed[:3]}; mean time per person: aggregate {t_agg / max(1, len(rows)):.3f}s, flow {t_flow / max(1, len(rows)):.3f}s")
    reach = [r for r in rows if r[1] > 0]
    beyond = [r for r in rows if r[1] == 0 and r[3] > 0]
    print(f"within 3 links of Tier A (aggregate > 0): {len(reach)}; aggregate 0 but flow > 0 (route longer than 3 links): {len(beyond)}; both 0: {len(rows) - len(reach) - len(beyond)}")

    def compare(name, subset):
        agg, flow = [r[2] for r in subset], [r[3] for r in subset]
        rho = spearman(agg, flow)
        order = lambda k: [r[0] for r in sorted(subset, key=lambda r: (-r[k], r[0]))[:TOP]]  # ties broken by id
        top_a, top_f = order(2), order(3)
        print(f"{name}: n {len(subset)}, Spearman rho {rho if rho is None else round(rho, 4)}, top-{TOP} overlap {len(set(top_a) & set(top_f))}/{TOP}, "
              f"flow equals aggregate for {sum(1 for r in subset if r[2] == r[3])}")
        return rho, len(set(top_a) & set(top_f))
    for name, subset in (("all computed", rows), ("within 3 links only", reach)):
        rho, ov = compare(name, subset)
        verdict = "flow adds information (claim kept)" if (rho is None or rho < 0.95 or ov < TOP) else "flow does NOT change the ranking (claim dropped)"
        print(f"  -> pass rule on '{name}': {verdict}")
    print(f"(took {time.time() - start:.0f}s)")
