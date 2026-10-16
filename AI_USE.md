# AI-use log

Hackathon rules 12-13: each teammate (Abdul, Aaryan) must understand and explain all code. This log records who worked, what we decided, what the AI (Claude Code) wrote, and what I verified.

| Date (IST) | Who | Decided by us | Written by AI | Verified how |
|---|---|---|---|---|
| Oct 15 | Abdul | Claude Code as the MCP client (no API costs); tie-checking moves into the server, since it needs no LLM; no crawler | `docs/mcp-spec.md`, `CLAUDE.md`, `checkpoints.md` | Read through; tools mapped to Q1-Q5 and the Track 2 brief |
| Oct 15 | Abdul | Run FalkorDB in Docker with a named volume (`falkordb-data`), so memory graphs survive restarts | `pyproject.toml` via `uv` (falkordb, mcp, httpx) | `RETURN 1` query from Python returned `[[1]]` |
| Oct 15 | Abdul | Build each checkpoint with two agents: a Builder, and a strict Reviewer that writes the tests. Tier decisions: any-topic Tier A with on/off-topic labels; recency = 2021 onwards; bottlers count as A2, labelled; Tier C runs over filtered `COAUTH_B` edges only | `loader.py` (Builder); `tests/test_loader.py` (Reviewer, 41 tests) | Reviewer round 1 recomputed the COAUTH math independently and failed the loader on 4 must-fix issues, which were sent back to the Builder |
| Oct 15 | Abdul | Accepted the Reviewer's round-2 PASS; carried the should-fix items forward to checkpoint 3 (`checkpoints.md`) | Builder round 2: family fetch, per-year sampling, dedupe, COAUTH_B, hang fix | Reviewer re-ran 49/49 tests, recomputed COAUTH, read all 39 dedupe removals, and timed Q3 at under 60 ms |
| Oct 15 | Abdul | Evidence filter for non-papers (F1000 reports, corrections, retraction notices); Tier B evidence lists only recent small papers (loader stores them); coverage computed from the graph, never hard-coded | `research.py` (Q1-Q4 + name lookup), `verifier.py`, loader tweaks (`recent_small_dois`, DOI-preferring dedupe), spec section 9 (Builder, checkpoint 3) | Ran every function on real data; recomputed tier and score for 15 experts with independent Cypher; timed each query; Reviewer FAIL (small items) fixed: anonymised control, E1 headline interval = spread across the 100 placebo circles (0.84-1.09), paper counts in E3 |
| Oct 15 | Abdul | Accepted the Reviewer's checkpoint 3 FAIL; junk works skipped in COAUTH, Index/Front Matter/untitled records dropped at load, names folded into `Person.name_norm`, coverage and duplicate counts moved into `Meta` | Builder round 2: `loader.py`, `research.py`, `verifier.py`, spec fixes | Reviewer's `tests/test_research.py` and `tests/test_loader.py` re-run |
| Oct 16 | Abdul | Checkpoint 4 pass rules stay as fixed in redesign section 7 (no tuning); E2 cut year Y=2018, window Y-4..Y, a pass needs the weighted score to beat volume AND both plain-count variants; E1 matched on works_count bands fixed before running | `eval/common.py`, `eval/e3_e7.py`, `eval/e1.py`, `eval/e2.py`, `eval/results.md` (Builder, checkpoint 4) | Ran all scripts; E1 real hits (1,299) recounted with a separate Cypher; E2 Tier-A rebuild checked equal to `research._tier_a` (3,035 ids, same A1/A2); GEBN names checked against a cited public source; Reviewer FAIL (small items) fixed: anonymised control, E1 headline interval = spread across the 100 placebo circles (0.84-1.09), paper counts in E3 |
| Oct 16 | Abdul | Checkpoint 5 design: tools take no name or `public_case`; single-expert tools take `expert_number`; `check_case` takes `expert_ids` (max 25, 90 s budget) until `open_case` exists; spec section 9b records the deviations | `server.py` (6 tools over `research.py`, envelope, lock, warm-up) | Drove the server over real stdio with the mcp client: all tools on real data, error paths, 25-expert batch (about 4.5-5.5 s); after Reviewer FAIL: cypher collapsed per query (88 to 39 KB), `public_case` limited to a GEBN id allowlist, banned words removed from descriptions, stdout carries only JSON-RPC |

**Add a row for every AI-assisted change.** The `Who` column is the teammate whose session did it (Abdul or Aaryan). The `## Learned` lines end with the teammate's name in brackets.
| Oct 16 | Aaryan | Checkpoint 5b: turn on AOF persistence (`appendfsync everysec`) and `--stop-timeout 120`; migrate by enabling AOF live first, so the AOF file is built from the loaded graph | Container commands, `CLAUDE.md` persistence steps | Read the image's `run.sh` (passes `REDIS_ARGS`); `CONFIG GET appendonly` = yes after recreate; 85,511 Person still there; crash test: node written, `docker kill` 2 s later, node back after start |
| Oct 16 | Aaryan | Checkpoint 5e: discovery is a CLI, not an MCP tool, so the server never holds the key; it never auto-picks a profile and lists split-profile siblings | `discover.py`, `tests/test_discover.py` + recorded fixtures (Builder); 11 Reviewer tests | Reviewer FAIL (5 must-fix: wrong topic overlap, hidden split profiles, overwritten case files, asymmetric name normalisation, punctuation-only query) → fixed → PASS; mutation-tested; key never in fixtures |
| Oct 16 | Aaryan | Pin FalkorDB `v4.22.1`: 6.0.2 (pulled as `:latest` on Oct 8) errors on `toLower()` of a title with U+FFFD, then crashes; fresh volume, graph rebuilt from cache | `CLAUDE.md` commands | Same query works on 4.22.1; rebuilt graph row-for-row identical to HEAD's loader (Reviewer); 780 MB vs 1,554 MB on 6.0.2 |
| Oct 16 | Aaryan | Checkpoints 5c/5d: per-case config + staging build with RENAME swap; tools take a `case` slug (D9); `Work.junk` precomputed so queries never lower-case titles | `cases/obesity-coke/case.json`, `loader.py`, `research.py`, `server.py`, spec (Builder); `tests/test_cases.py` 13 Reviewer tests, `test_server.py` contract update (7 tools) | Reviewer round 1 FAIL (`list_cases` KeyError) → fixed → PASS; junk flag equals old Cypher rule on all 31,291 works; tier/score identical for 404 experts; 14 mutations each caught |
| Oct 16 | Aaryan | Orchestrator test run in a fresh Claude Code session | nothing new | Blair A1, Hill A2 with DOIs; 5 no-tie experts with scope; ambiguous "James Hill" flagged |
| Oct 16 | Aaryan | Checkpoint 6: one memory graph per newsroom (env only); every call logged as an Event; checks saved with DOIs and no-tie scope; `recheck` writes a new Check that SUPERSEDES the old one; staged load via a separate `obesity-coke-staged` case built from the same cache | `memory.py`, `server.py` Track 2 tools (`open_case`, `recall`, `recheck`), `loader.py --until-year`/`cache_from`, spec 9d (Builder); 13 Reviewer tests in `tests/test_memory.py` | Reviewer round 1 FAIL (5 must-fix) → fixed → round 2 PASS; 12 mutants killed; real 3-process recheck race leaves one current Check; staged demo re-run by the Reviewer |
| Oct 16 | Aaryan | Checkpoint 5f end-to-end run on the Philip Morris case | nothing new | Recorded in `eval/results.md`; Tier C covers 52 of 73 top authors (open question) |
| Oct 16 | Aaryan | Show the newsroom memory live in Obsidian: export only the memory graph (not the 1.3M-edge research graph), poll every few seconds, own one vault folder, never overwrite unmarked notes | `obsidian_export.py`, `tests/test_obsidian.py` (Builder); 14 Reviewer tests | 3 review rounds (FAIL, FAIL, PASS): silent 5,000-row cut, overwritten user notes, control characters in file names, `.trash` clobbering and symlink escape all fixed; mutants killed |
| Oct 16 | Aaryan | Acted on an external code review: uuid7 fallback for Python 3.13; no duplicate BFS; `duration_ms` on Events; no-tie scope quotes funder-only coverage (graph and per expert); Tier B/C and score labelled context only and no longer used for ranking (E1/E2 failed); README rewritten; FalkorDB bound to 127.0.0.1 | `memory.py`, `research.py`, `server.py`, `verifier.py`, README, spec 9e (Builder); 4 Reviewer tests | Reviewer PASS, 0 must-fix; every README number checked against `eval/results.md`; suites green on Python 3.14 and 3.13 |
| Oct 16 | Aaryan | Checkpoint 7: verdicts stored as newsroom memory and applied as query parameters (research graph stays read-only); the server writes every sentence of a sourcing note, so Writer text never reaches it; `permissions.ask` rules keep the human gate in auto mode | `record_verdict`, `draft_note`, `approve_note`, `.claude/agents/writer.md`, verifier changes, spec 9f (Builder); 18 Reviewer tests | 4 review rounds (FAIL ×3, PASS): story text echoed into notes, claim text not matched to its kind, stale Tier C after exclusion, missing experts, banned-word tricks, verdict race, duplicate-DOI counts; mutants killed each round |

## Learned (checkpoint 1 notes)
<!-- One line per toy-graph test: what I expected, and what actually happened. -->
Script: `experiments/toy_graph.py`. Written by AI on Oct 15 in Abdul's session; results below were checked by running it.

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
- **Checkpoint 3 FalkorDB quirks:**
  - `exists((a)-[:R]->())` fails ("Unable to resolve filtered alias"). Use the pattern directly as a predicate: `WHERE (a)-[:R]->()` or `WHERE NOT (a)-[:R]->()`. [Abdul]
  - A variable-length pattern with equal bounds, `[:COAUTH_B*2..2]`, returned 0 rows where the fixed two-edge pattern found tier-A people; `algo.BFS` and fixed patterns agreed. Use fixed patterns for exact depths. [Abdul]
  - `db.idx.fulltext.queryNodes` ANDs the words and returned all matches (6,741 for "obesity"), with no 10-row cap. [Abdul]
  - `ro_query` reports a slow query as "Query timed out" and a write as "graph.RO_QUERY is to be executed only on read-only queries"; research.py maps these to TIMEOUT and READ_ONLY. [Abdul]

- **Checkpoint 4 notes:**
  - FalkorDB `RO_QUERY` pages: reading all 31k works by year and all authorships by slices of 400 work ids stays far under the 10,000-row cap and takes about 5 s in total. [Abdul]
  - The first E1 result (Tier B lift 0.96x) is a FAIL; the Tier-A group is not better connected to topic authors than a matched random group. [Abdul]
  - Bootstrap without numpy: count every expert 0, 1, 2... times (Poisson with mean 1), the same counts for the real group and all placebo circles. [Abdul]

- **Checkpoint 5 notes (mcp 2.x SDK):**
  - `FastMCP` is gone: use `from mcp.server.mcpserver import MCPServer`; `@mcp.tool(...)` and `mcp.run("stdio")`. Client objects use snake_case (`input_schema`, `structured_content`, `is_error`). [Abdul]
  - Sync tool functions run in a worker thread, so calls can overlap: `research.py`'s single connection and cache need a lock. [Abdul]
  - A bare `-> dict` return is not allowed for structured output; use `dict[str, Any]`. A docstring followed by `+ TEXT` is no longer a docstring, so the description is set in the decorator. [Abdul]
  - Extra arguments the schema does not list (for example `public_case` on `tie_report`) are silently dropped; a wrong type is rejected by pydantic with `isError`, before our code runs. [Abdul]
  - `uv run --directory <repo> python server.py` takes about 4 s to start and answer `initialize`; loading the caches adds 0.6 s. [Abdul]
- **FalkorDB 4.22.1:** `OPTIONAL MATCH (d ...) WITH d WHERE d IS NULL CREATE ...` does not filter; use `OPTIONAL MATCH ... WITH count(d) AS n WHERE n = 0 CREATE ...` for a write-if-absent guard. [Aaryan]
