# AI-use log

Hackathon rules 12-13: I must understand and explain all code. This log records what I decided, what the AI (Claude Code) wrote, and what I verified.

| Date (IST) | Decided by me | Written by AI | Verified how |
|---|---|---|---|
| Oct 15 | Claude Code as the MCP client (no API costs); tie-checking moves into the server, since it needs no LLM; no crawler | `docs/mcp-spec.md`, `CLAUDE.md`, `checkpoints.md` | Read through; tools mapped to Q1-Q5 and the Track 2 brief |
| Oct 15 | Run FalkorDB in Docker with a named volume (`falkordb-data`), so memory graphs survive restarts | `pyproject.toml` via `uv` (falkordb, mcp, httpx) | `RETURN 1` query from Python returned `[[1]]` |
| Oct 15 | Build each checkpoint with two agents: a Builder, and a strict Reviewer that writes the tests. Tier decisions: any-topic Tier A with on/off-topic labels; recency = 2021 onwards; bottlers count as A2, labelled; Tier C runs over filtered `COAUTH_B` edges only | `loader.py` (Builder); `tests/test_loader.py` (Reviewer, 41 tests) | Reviewer round 1 recomputed the COAUTH math independently and failed the loader on 4 must-fix issues, which were sent back to the Builder |
| Oct 15 | Accepted the Reviewer's round-2 PASS; carried the should-fix items forward to checkpoint 3 (`checkpoints.md`) | Builder round 2: family fetch, per-year sampling, dedupe, COAUTH_B, hang fix | Reviewer re-ran 49/49 tests, recomputed COAUTH, read all 39 dedupe removals, and timed Q3 at under 60 ms |

## Learned (checkpoint 1 notes)
<!-- One line per toy-graph test: what I expected, and what actually happened. -->
Script: `experiments/toy_graph.py`. Written by AI on Oct 15; results below were checked by running it.

- **COAUTH directions:** undirected matching is inconsistent. `()-[r]-()` counted 3 edges, but `(a:P)-[r]-(b:P)` counted 6, and a bound node saw its two-way partner twice. **Rule: store COAUTH both ways and always match it with `->`.**
- **`PARENT_OF*0..3`:** works; finds an affiliation to a child org of Coke Co.
- **Bounded path `[:COAUTH*1..2]`:** works; returns everyone within 2 hops.
- **`algo.BFS(node, depth, 'REL')`:**
  - follows **outgoing** edges only (fine, since COAUTH is two-way);
  - the result excludes the start node;
  - if nothing is reached it returns **no row**, not an empty list. Q3 must treat "no row" as "nothing reached".
- **`algo.maxFlow({sourceNodes, targetNodes, capacityProperty, relationshipTypes}) YIELD maxFlow`:** works. The dee -> ben -> ana path gave 0.5, the bottleneck edge, as expected.
- **`GRAPH.RO_QUERY`:** rejected a `CREATE` with "to be executed only on read-only queries", and nothing was written.
- **OpenAlex:**
  - the filter is `funders.id:F…` (`grants.funder` is invalid);
  - `select` works, but the field is called `awards`, not `grants`;
  - Coca-Cola funder IDs are `F4320307807` (company, 91 works) and `F4320308599` (foundation, 796 works); filtering on both returns **820 works**.
- **Checkpoint 2 FalkorDB quirks:**
  - The server silently caps results at 10,000 rows (`RESULTSET_SIZE`), so count in Cypher rather than counting returned rows.
  - After `UNWIND`, a node-only `MATCH (p:Person {oa_id:x})` uses the index. A pattern that traverses on from that node (`...{oa_id:x})-[:COAUTH]->(b)`) does not, even when it is split into two MATCH clauses; it timed out. `MATCH (a:Person) WHERE a.oa_id IN $ids` always uses the index (checked with EXPLAIN).
  - After a `PARENT_OF*0..3` pattern, a relationship property can't be used in the next MATCH ("'r' not defined"); pass it through `WITH r.work AS wid` first.
  - `algo.BFS` only accepts a relationship type, not a property filter, so filtered traversal needs a pre-filtered edge type (`COAUTH_B`).
- **OpenAlex list results cap author lists at 100** per work, with no truncation flag. Works with more authors have to be re-fetched one by one.
- **Corporate family:** the Coca-Cola institution `I1312732213` has no `associated_institutions` in OpenAlex, so the `PARENT_OF` family has to be seeded by hand from a list of Coca-Cola entities.
