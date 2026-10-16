# TieTrace checkpoints

Tick a box when it is **done and verified**, not just written. Each checkpoint ends in a gate: don't start the next one until the gate passes, or take the fallback.
Deadline: **submit by Oct 18, 6 PM IST.** Specs: `tietrace-v2-redesign.md`, `docs/mcp-spec.md`.

| # | Checkpoint | Target (IST) | Status |
|---|---|---|---|
| 0 | Setup | Oct 15 morning | 🟨 |
| 1 | Toy graph: assumptions tested | Oct 15, hours 1-2 | ✅ |
| 2 | Loader: research graph built | Oct 15, by hour 8 | ✅ |
| 3 | Research queries Q1-Q4 + Verifier | Oct 16 morning | ✅ |
| 4 | Evaluation E1, E2, E3, E7 | Oct 16 afternoon | ✅ |
| 5 | MCP server: Track 1 tools | Oct 16 evening | ✅ |
| 5b | Persistence fix (AOF) | Oct 16 | ✅ |
| 5c-5f | Any company/topic: case config, case-aware tools, discover, Philip Morris demo | Oct 16-17 | ✅ |
| 6 | Memory graph: Track 2 tools | Oct 17 morning | ✅ |
| 7 | Verdicts, notes, Writer | Oct 17 midday | ✅ |
| 8 | Remaining evaluation + golden tests | Oct 17 afternoon | ✅ |
| 9 | README, video, submit | Oct 18, by 6 PM | ⬜ |

**Team: Abdul and Aaryan.** Tag each checkbox with who did it: `- [x] item (Abdul)` or `(Aaryan)`; use `(Abdul + Aaryan)` for pairing. Unticked items carry `(→ Aaryan)` to claim them. Add the date when you tick, e.g. `(Abdul, Oct 16)`. Checkpoints 0-2 were built in Abdul's sessions, so they are attributed to Abdul; Aaryan can correct that.

Status key: ⬜ not started · 🟨 in progress · ✅ done · ✂️ cut

---

## 0. Setup
- [x] GitHub repo created (Abdul, Oct 15)
- [x] Spec written (`docs/mcp-spec.md`) and `CLAUDE.md` added (Abdul, Oct 15)
- [ ] Registered on WeMakeDevs and joined the FalkorDB Discord
- [x] FalkorDB running in Docker; Browser opens at http://localhost:3000 (Abdul, Oct 15)
- [x] OpenAlex API key obtained (Abdul, Oct 15)
- [x] Python env with `falkordb` and `mcp` installed (Abdul, Oct 15)
- [x] `AI_USE.md` started (Abdul, Oct 15)

**Gate:** `GRAPH.QUERY test "RETURN 1"` works from Python.

## 1. Toy graph: assumptions tested
- [x] 10-node toy graph built (Person, Work, Org, Funder) (Abdul, Oct 15)
- [x] `COAUTH` in both directions (finding: undirected matching is inconsistent, so always use `->`) (Abdul, Oct 15)
- [x] Bounded variable-length path `[:PARENT_OF*0..3]` works (Abdul, Oct 15)
- [x] `algo.BFS` runs and returns reached nodes (Abdul, Oct 15)
- [x] `algo.maxFlow` direction and parameters understood (Abdul, Oct 15)
- [x] `GRAPH.RO_QUERY` rejects a write (Abdul, Oct 15)
- [x] OpenAlex funder filter and `select` param confirmed (redesign section 11) (Abdul, Oct 15)

**Gate:** every item above has a one-line note in `AI_USE.md` saying what was learned.

## 2. Loader: research graph built
- [ ] `loader.py` loads works for a configurable target organization, funder, or domain/URL
- [ ] Loads works matching the configured topic(s) since 2010; topic filters are optional and configurable
- [ ] Person, Work, Org, Funder, Topic nodes plus `AUTHORED`, `AFFILIATED`, `FUNDED_BY`, `ABOUT` edges
- [ ] `PARENT_OF` relationships for the configured organization and its related entities, when applicable
- [ ] `COAUTH` derived with `n_papers`, `w`, `recency_w`, `small_papers`, `dois[]`
- [ ] `profile_flag` set on Person
- [ ] Node and edge counts recorded

Counts (after checkpoint 3 loader changes): 85,511 Person, 31,291 Work, 1,326,194 COAUTH, 50,436 COAUTH_B (reviewed: PASS in round 2, 49/49 tests).

**Gate (hour 8, passed):** one Cypher query returns a dated Tier-A path with a DOI. **If not, switch to the backup (LayoverGuard).**

## 3. Research queries Q1-Q4 + Verifier

**Carried forward from the checkpoint 2 review (handle in checkpoint 3):**
- [x] Tier B evidence: COAUTH_B `dois[]` lists all shared papers, so filter to recent, small ones through the Work nodes (or add `recent_small_dois` to the loader) (Abdul, Oct 15)
- [x] Exclude F1000 peer-review reports and errata from Tier A evidence (Abdul, Oct 15)
- [x] Label the bottler affiliations (2 edges) as A2 (Abdul, Oct 15)
- [x] The coverage note must mention the 52 leftover duplicate groups (228 COAUTH_B edges depend on them) and the 25 author lists cut off at 100 (Abdul, Oct 15)
- [x] Loader nits: the dedupe tie-break should prefer the record with a DOI; speed up `test_coauth_b_matches_filter` (268 s; use OPTIONAL MATCH) and make it compare all properties (Abdul, Oct 15)
- [ ] E1: the placebo circles must match the 3,041 Tier A people; fix the bootstrap interval method before running it

- [x] Q1 `find_experts` query (Abdul, Oct 15)
- [x] Q2 tier and score query (A1 / A2 / B / C / NONE) (Abdul, Oct 15)
- [x] Q3 `algo.BFS` no-tie proof with scope (Abdul, Oct 15)
- [x] Q4 bridge paths with DOIs (Abdul, Oct 15)
- [x] Name lookup for `resolve_experts` (MATCHED / AMBIGUOUS / NOT_FOUND) (Abdul, Oct 15)
- [x] Verifier: DOI check, scope check, banned words (Abdul, Oct 15)

Result: all queries under 3 s cold (depth-3 bridges on the biggest hub is the slowest, about 1.7 s cold); `tests/test_research.py` 51/51 and `tests/test_loader.py` 49/49; Reviewer PASS in round 3. Carried to checkpoint 4: the E1 item above, and the Verifier does not check a no-tie claim's scope against the stored result (nit).

**Gate (passed):** each query runs in Browser in under 3 s, and you can explain it in plain words.

## 4. Evaluation E1, E2, E3, E7
- [x] **E3** known case: the four GEBN leaders named in public sources (Hill, Blair, Hand, Peters) show as Tier A with DOIs; no public source names a fifth, so "all five" is not testable (Abdul, Oct 15)
- [x] **E7** coverage: share of topic works with funder and affiliation data (Abdul, Oct 15)
- [x] **E1** placebo lift for Tier B, with a 95% interval: **FAIL**, 0.96x (0.84-1.09) (Abdul, Oct 15)
- [x] **E2** temporal hold-out AUC against the baselines (or "underpowered") (Abdul, Oct 15)
- [x] Results written to `eval/results.md`, losses included (Abdul, Oct 15)

Result: Reviewer fixes applied; `tests/test_eval.py` 13/13. Details in `eval/results.md`.

**Gate (E3 passes for the four public names, E1 number known):**  E3 passes and the E1 number is known. If E1 and E2 both fail, cut the Tier B claims and keep the evidence-viewer framing.

## 5. MCP server: Track 1 tools
- [x] `server.py` with the shared response format and the RO guard (timeout, row cap) (Abdul, Oct 15)
- [x] `resolve_experts`, `find_experts`, `tie_report`, `check_no_tie`, `bridges` (Abdul, Oct 15)
- [x] `check_case` (batch run) (Abdul, Oct 15)
- [x] Registered with `claude mcp add`; tools are visible in Claude Code (Abdul, Oct 15)
- [x] Orchestrator test: paste a draft story and get tiers back (Aaryan, Oct 16): new Claude Code session, server `tietrace-alpha` connected, FalkorDB 4.22.1. Steven Blair → A1 and James Hill → A2 with DOIs; 5 "no ties found" experts as Expert #n with scope (3 hops, 2021-2026, funder coverage 56%); flagged the ambiguous "James Hill". Follow-ups: the reply said "clean source" (editorialising: add to the Verifier's banned words in checkpoint 7); decide how a journalist sees names of anonymised experts. Re-run after the 5c/5d review fixes.

Hand-off (Abdul, Oct 16): everything is built, reviewed and registered. Only the orchestrator test is left: Abdul runs it in a new Claude Code session.

Result: `tests/test_server.py` 26/26 (Reviewer PASS in round 2); server registered and `claude mcp list` shows it Connected.

**Gate:** a full question works end to end from Claude Code.

## 5b. Persistence fix (from `IMPROVEMENT.md`)
- [x] AOF on (`appendonly yes`, `appendfsync everysec`) via `REDIS_ARGS`; `run.sh` passes it to `redis-server` (Aaryan, Oct 16)
- [x] `--stop-timeout 120` so Docker waits for the final save (Aaryan, Oct 16)
- [x] Verified: `CONFIG GET appendonly` = yes after recreate; research graph intact (85,511 Person) (Aaryan, Oct 16)
- [x] Crash test: write a node, `docker kill` 2 s later (no save), start: node is back (Aaryan, Oct 16)
- [ ] Abdul: migrate your container the same way (steps in `CLAUDE.md` Commands; uses your ports 6380/3001) (→ Abdul)

## 5c-5f. Any company and topic (from `IMPROVEMENT.md`)
- [x] 5c `cases/<slug>/case.json`, `loader.py --case`, staging + RENAME swap, `--dry-run`, build lock, `Work.junk` precomputed (Aaryan, Oct 16)
- [x] 5d `case` argument on tools, `list_cases`, `NO_GRAPH`, per-graph caches, cache dropped after a rebuild (Aaryan, Oct 16). Review: round 1 FAIL (1 must-fix), round 2 PASS; suites on v4.22.1: cases 23/23, loader 49/49, research 51/51, server 26/26, eval 13/13, discover 26/26
- [x] 5e `discover.py` CLI (funders, institutions, topics, authors): Reviewer FAIL (5 must-fix) → fixed → PASS; 26/26 tests offline and live; never auto-picks, shows split-profile siblings (strong/weak) (Aaryan, Oct 16)
- [x] 5f Philip Morris × lung cancer case built (Aaryan, Oct 16): GATE 0 approved by Aaryan (PMI + FSFW as A2; Altria, Vectura, Swedish Match rejected; T10202 cap 20,000). 21,756 works, 68,723 people, 726 MB, 915 API calls, 13 min. Tier A: 3,649 (A1 3,176, A2 473)
- [x] 5f end-to-end: Philip Morris prompt run in Claude Code, recorded in `eval/results.md` (Aaryan, Oct 16): scope stated honestly (PMI only, other tobacco companies named as not checked); 5 no-tie experts; funder coverage 37.5%; finding: Tier C covers 52 of 73 top authors

Decision (Aaryan, Oct 16): D9 resolved as the plan recommends: tools take a `case` slug (validated against `cases/`), default `TIETRACE_CASE` env, fallback `obesity-coke`; the newsroom stays env-only.

## 6. Memory graph: Track 2 tools
Claimed (→ Aaryan, Oct 16).
- [x] `TIETRACE_NEWSROOM` env var picks the tenant graph (Aaryan, Oct 16)
- [x] Event logger: every call writes an `Event` under a `Session` (Aaryan, Oct 16): ~0.3 ms per call; 4 processes × 50 calls = 200/200 Events
- [x] `open_case` with `prior_memory` (Aaryan, Oct 16)
- [x] `recall` timeline (Aaryan, Oct 16)
- [x] Staged load: works up to year Y, save a check, load the newer slice (Aaryan, Oct 16): `cases/obesity-coke-staged`, `loader.py --until-year 2023`, 0 API calls; steps in spec 9d
- [x] `recheck` shows the diff (Aaryan, Oct 16): e.g. an expert went C → A1 after the 2024-26 slice (a 2026 Coca-Cola-funded paper). Review: round 1 FAIL (5 must-fix: NONE checks without scope, public-case name leak, forked SUPERSEDES chains, recall on unknown ids, tests writing into newsroom:alpha) → round 2 PASS; suites: loader 49, research 51, server 26, eval 13, cases 27, discover 26, memory 24
- [x] Gate E11 in real Claude Code (Aaryan, Oct 16): session 1 opened "Soda tax story" and checked Blair (A1 105.05) and Hill (A2 50.11); a fresh session's `open_case` on Hill showed the earlier check (tier A2, chk_…cb). Follow-up for checkpoint 7: session 2 wrongly explained A2 as "bottler affiliation" (it is Coca-Cola Foundation funding) because `prior_memory` carries only tier and score; add the evidence kind to the summary so the model never guesses

**Gate:** a new Claude Code session recalls an earlier case (E11).
- [x] Extra: `obsidian_export.py` shows the newsroom memory live in Obsidian's Graph view (Aaryan, Oct 16). Review: round 1 FAIL (5 must-fix) → round 2 FAIL (2 in the `.trash` move) → round 3 PASS; `test_obsidian` 22/22. Minor open: case-only Funder/Org name clashes, empty placeholder left in `.trash` if a move fails
- [x] Extra: hardening after an external review (Aaryan, Oct 16): Python 3.13 fix, funder-only no-tie coverage, Tier B/C context only, `duration_ms`, README rewrite, localhost-only FalkorDB. Reviewer PASS. Open: LICENSE (decide with Abdul), `requirepass`, CI

## 7. Verdicts, notes, Writer
Claimed (→ Aaryan, Oct 16).
- [x] `record_verdict` (PAPER_EXCLUDED) with an impact report (Aaryan, Oct 16): gate run: excluding 10.1016/j.foodhyd.2022.107657 moved 2 experts A1 101.6 → NONE; Hill unchanged
- [x] `PROFILE_MERGE` verdict (Aaryan, Oct 16): S. N. Blair profile NONE → A1 when merged with Steven N. Blair
- [x] `draft_note` runs the Verifier and returns verified or rejected (Aaryan, Oct 16): the server writes every printed sentence; every case expert gets a disclosure line; sources say kind, source, tier and on/off-topic per DOI
- [x] `approve_note` exports markdown and is blocked unless verified (Aaryan, Oct 16): also blocked after a later verdict or a graph rebuild; `.claude/settings.json` forces a prompt for `record_verdict`/`approve_note` even in auto mode
- [x] `.claude/agents/writer.md` subagent (Aaryan, Oct 16): tools limited to recall, tie_report, check_no_tie, draft_note
- [ ] ✂️ Stretch cut: `review_queue` + Review Desk subagent, `set_policy` (marked cut in the spec)

Review: round 1 FAIL (5 must-fix) → round 2 FAIL (no-tie text could state a tie) → round 3 FAIL (duplicate DOIs inflated a count) → round 4 PASS. Suites: loader 49, research 54, server 26, eval 13, cases 27, discover 26, memory 32, obsidian 22, notes 40.
- [x] Gate in real Claude Code (Aaryan, Oct 16): case "Soda tax schools" (Blair, Hill) → check_case → record_verdict excluding 10.1016/j.mhpa.2017.12.001 (Blair 6 → 5 direct works, still A1; the older "Soda tax story" check was superseded too) → writer agent draft verified first try → approve_note → markdown. Aaryan confirmed Claude Code prompted him; no allow rule was saved. Follow-up: a DOI with two evidence items prints both labels in one bracket

**Gate:** a demo run works end to end: input, check, verdict, note, approval.

## 8. Remaining evaluation + golden tests
Claimed (→ Abdul, Oct 16). Test DB: second container `falkordb-v4` (v4.22.1, AOF on) on host port 6381 (use `FALKORDB_PORT=6381`); the old `falkordb` container (`:latest`) is kept untouched.
- [x] **E8** golden tests (15 fixed checks, `tests/test_golden.py`) pass (Abdul, Oct 16): the "five" GEBN scientists are four public names (Hill A2, Blair A1, Hand A1, Peters A2)
- [x] **E9** concurrency: two server processes, `check_case` and `record_verdict` at the same time, overlap proven, nothing lost, all current Checks equal a fresh rerun (`tests/test_concurrency.py`) (Abdul, Oct 16)
- [x] **E10** tenant isolation: all 13 tools tried with the other newsroom's ids, 0 rows / NOT_FOUND (`tests/test_isolation.py`) (Abdul, Oct 16)
- [x] **E6** mega-paper ablation (`eval/e6.py`, `eval/results.md`): without the 25-author cap A1 goes 949 → 1,069 and Tier B 1,309 → 1,889; 23.7% of A1+B people exist only through big papers; cap kept (Abdul, Oct 16)
- [ ] ✂️ **E5** profile hand-check (cut if behind): the sample of 50 is drawn (`eval/e5_sample.md`, no names); only a person can fill the verdict columns, so no error rate is claimed
- [x] **E4** max flow vs aggregate (`eval/e4.py`, Reviewer PASS) (Abdul, Oct 17): Spearman 0.86 (0.73 within 3 links), top-10 overlap 6 of 10, so the rule keeps the claim, but flow is not depth-limited, needs whole-number capacities, and neither ranking is validated; max flow is not used for any finding

Review: round 1 PASS with 2 must-fix (attribution; E9 overlap not proven) → fixed. New suites: eval 15/15 (with E4), golden 15/15, concurrency 1/1, isolation 1/1, eval 14/14. Regression on the new container: loader 49/49, server 26/26, cases 27/27, notes 40/40 pass. **Failing, not caused by checkpoint 8 and not yet fixed:** research 53/54 (`bridges3_hub` 4.2 s cold vs the 3 s limit, fresh container); discover 20/26 (Windows `charmap` error reading a fixture without `encoding="utf-8"`); memory 31/32 (needs the `research:philip-morris-lung` graph, not loaded on this machine); obsidian 16/22 (Windows: `signal.SIGINT` unsupported, plus path checks).

**Gate (passed, Oct 17):** all must-ship items work (E8, E9, E10, E6, E4); E5 cut, no error rate claimed.

## 9. README, video, submit
- [ ] README: problem, tiers, data model, queries, evaluation with losses, human gates, ethics, setup
- [ ] Mermaid diagrams render on GitHub
- [ ] AI-use disclosure and data/licence note (OpenAlex CC0; check FalkorDB's licence)
- [ ] 90-second demo video, using real numbers only
- [ ] Repo public
- [ ] Submitted, with both tracks named

**Gate:** submission confirmed on WeMakeDevs before 6 PM IST.
