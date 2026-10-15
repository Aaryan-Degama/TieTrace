# TieTrace checkpoints

Tick a box when it is **done and verified**, not just written. Each checkpoint ends in a gate: don't start the next one until the gate passes, or take the fallback.
Deadline: **submit by Oct 18, 6 PM IST.** Specs: `tietrace-v2-redesign.md`, `docs/mcp-spec.md`.

| # | Checkpoint | Target (IST) | Status |
|---|---|---|---|
| 0 | Setup | Oct 15 morning | 🟨 |
| 1 | Toy graph: assumptions tested | Oct 15, hours 1-2 | ⬜ |
| 2 | Loader: research graph built | Oct 15, by hour 8 | ⬜ |
| 3 | Research queries Q1-Q4 + Verifier | Oct 16 morning | ⬜ |
| 4 | Evaluation E1, E2, E3, E7 | Oct 16 afternoon | ⬜ |
| 5 | MCP server: Track 1 tools | Oct 16 evening | ⬜ |
| 6 | Memory graph: Track 2 tools | Oct 17 morning | ⬜ |
| 7 | Verdicts, notes, Writer | Oct 17 midday | ⬜ |
| 8 | Remaining evaluation + golden tests | Oct 17 afternoon | ⬜ |
| 9 | README, video, submit | Oct 18, by 6 PM | ⬜ |

Status key: ⬜ not started · 🟨 in progress · ✅ done · ✂️ cut

---

## 0. Setup
- [x] GitHub repo created
- [x] Spec written (`docs/mcp-spec.md`) and `CLAUDE.md` added
- [ ] Registered on WeMakeDevs and joined the FalkorDB Discord
- [x] FalkorDB running in Docker; Browser opens at http://localhost:3000
- [ ] OpenAlex API key obtained
- [x] Python env with `falkordb` and `mcp` installed
- [x] `AI_USE.md` started

**Gate:** `GRAPH.QUERY test "RETURN 1"` works from Python.

## 1. Toy graph: assumptions tested
- [ ] 10-node toy graph built (Person, Work, Org, Funder)
- [ ] `COAUTH` in both directions; undirected matching behaves as expected
- [ ] Bounded variable-length path `[:PARENT_OF*0..3]` works
- [ ] `algo.BFS` runs and returns reached nodes
- [ ] `algo.maxFlow` direction and parameters understood
- [ ] `GRAPH.RO_QUERY` rejects a write
- [ ] OpenAlex funder filter and `select` param confirmed (redesign section 11)

**Gate:** every item above has a one-line note in `AI_USE.md` saying what was learned.

## 2. Loader: research graph built
- [ ] `loader.py` loads works for a configurable target organization, funder, or domain/URL
- [ ] Loads works matching the configured topic(s) since 2010; topic filters are optional and configurable
- [ ] Person, Work, Org, Funder, Topic nodes plus `AUTHORED`, `AFFILIATED`, `FUNDED_BY`, `ABOUT` edges
- [ ] `PARENT_OF` relationships for the configured organization and its related entities, when applicable
- [ ] `COAUTH` derived with `n_papers`, `w`, `recency_w`, `small_papers`, `dois[]`
- [ ] `profile_flag` set on Person
- [ ] Node and edge counts recorded

**Gate (hour 8):** one Cypher query returns a dated Tier-A path with a DOI. **If not, switch to the backup (LayoverGuard).**

## 3. Research queries Q1-Q4 + Verifier
- [ ] Q1 `find_experts` query
- [ ] Q2 tier and score query (A1 / A2 / B / C / NONE)
- [ ] Q3 `algo.BFS` no-tie proof with scope
- [ ] Q4 bridge paths with DOIs
- [ ] Name lookup for `resolve_experts` (MATCHED / AMBIGUOUS / NOT_FOUND)
- [ ] Verifier: DOI check, scope check, banned words

**Gate:** each query runs in Browser in under 3 s, and you can explain it in plain words.

## 4. Evaluation E1, E2, E3, E7
- [ ] **E3** known case: all five GEBN scientists show as Tier A with DOIs
- [ ] **E7** coverage: share of topic works with funder and affiliation data
- [ ] **E1** placebo lift for Tier B, with a 95% interval
- [ ] **E2** temporal hold-out AUC against the baselines (or "underpowered")
- [ ] Results written to `eval/results.md`, losses included

**Gate:** E3 passes and the E1 number is known. If E1 and E2 both fail, cut the Tier B claims and keep the evidence-viewer framing.

## 5. MCP server: Track 1 tools
- [ ] `server.py` with the shared response format and the RO guard (timeout, row cap)
- [ ] `resolve_experts`, `find_experts`, `tie_report`, `check_no_tie`, `bridges`
- [ ] `check_case` (batch run)
- [ ] Registered with `claude mcp add`; tools are visible in Claude Code
- [ ] Orchestrator test: paste a draft story and get tiers back

**Gate:** a full question works end to end from Claude Code.

## 6. Memory graph: Track 2 tools
- [ ] `TIETRACE_NEWSROOM` env var picks the tenant graph
- [ ] Event logger: every call writes an `Event` under a `Session`
- [ ] `open_case` with `prior_memory`
- [ ] `recall` timeline
- [ ] Staged load: works up to year Y, save a check, load the newer slice
- [ ] `recheck` shows the diff

**Gate:** a new Claude Code session recalls an earlier case (E11).

## 7. Verdicts, notes, Writer
- [ ] `record_verdict` (PAPER_EXCLUDED) with an impact report
- [ ] `PROFILE_MERGE` verdict
- [ ] `draft_note` runs the Verifier and returns verified or rejected
- [ ] `approve_note` exports markdown and is blocked unless verified
- [ ] `.claude/agents/writer.md` subagent
- [ ] Stretch: `review_queue` + Review Desk subagent, `set_policy`

**Gate:** a demo run works end to end: input, check, verdict, note, approval.

## 8. Remaining evaluation + golden tests
- [ ] **E8** golden tests (about 15 fixed checks) pass
- [ ] **E9** concurrency: two reporter sessions, nothing lost
- [ ] **E10** tenant isolation: alpha cannot see beta
- [ ] **E6** mega-paper ablation
- [ ] **E5** profile hand-check (cut if behind)
- [ ] **E4** max flow vs aggregate (cut if behind; drop the claim if it fails)

**Gate:** all must-ship items work, or the stretch items are cut.

## 9. README, video, submit
- [ ] README: problem, tiers, data model, queries, evaluation with losses, human gates, ethics, setup
- [ ] Mermaid diagrams render on GitHub
- [ ] AI-use disclosure and data/licence note (OpenAlex CC0; check FalkorDB's licence)
- [ ] 90-second demo video, using real numbers only
- [ ] Repo public
- [ ] Submitted, with both tracks named

**Gate:** submission confirmed on WeMakeDevs before 6 PM IST.
