# TieTrace checklist, phase 2: improvements after the MVP

*Last updated by Aaryan, Oct 17 2026: ticked W1-W6 (web app, Reviewer PASS after 3 rounds). Before that, by Abdul, Oct 17 2026: added section P2-W (web app, follow `WebApp.md`) before P2-0. Earlier the same day: first version, built from `IMPROVEMENT.md` and the state of `checkpoints.md` after checkpoint 9 started.*

**What this file is.** `checkpoints.md` is the plan to ship by **Oct 18, 6 PM IST**. This file is everything in `IMPROVEMENT.md` that is **not built yet**, plus loose ends found along the way. It is *not* part of the submission: do not start phase 2 work until the submission is confirmed, except the items marked **before submit**.

**Already built (do not redo; Aaryan, Oct 16):** `IMPROVEMENT.md` checkpoints 5b (AOF persistence), 5c (`cases/<slug>/case.json`, `loader.py --case`, staging swap, `--dry-run`), 5d (`case` argument, `list_cases`, `NO_GRAPH`, per-graph caches, graph-name guard `GRAPH_RE`), 5e (`discover.py`), 5f (Philip Morris × lung cancer case, run end to end). The "second topic" cut item is therefore already done.

**Rules (same as `CLAUDE.md`):** tick a box only when done and verified, with the date; claim an item with `(→ name)`; the Builder/Reviewer loop applies to every code item; tests are never weakened; the Coca-Cola case stays the default and the existing suites stay green.

Status key: ⬜ not started · 🟨 in progress · ✅ done · ✂️ cut

| Phase | What | When | Status |
|---|---|---|---|
| P2-W | **Web app** (follow `WebApp.md`) | before P2-0, after submit | ⬜ |
| P2-0 | Loose ends (some **before submit**) | Oct 17-18 | ⬜ |
| P2-1 | Hardening (`IMPROVEMENT.md` 5g) | after submit | ⬜ |
| P2-2 | Case-builder skill and agent (5e polish) | after submit | ⬜ |
| P2-3 | Corporate-family candidate pipeline (5h) | after submit | ⬜ |
| P2-4 | Data quality and scale | after submit | ⬜ |
| P2-5 | Future work, document only (5i) | later | ⬜ |

---

## P2-W. Web app (**follow `WebApp.md`**)
**All design decisions (layout, look, pages, build order, tests, open questions) are in [`WebApp.md`](WebApp.md). Read it first; if this list and `WebApp.md` disagree, `WebApp.md` wins.** Abdul chose: chat layout B (chat + live evidence panel), dark graph-motif look, a separate "How It Works" page, local first, the deployable free-tier version last.
- [x] Decide the open questions in `WebApp.md` section 8 (Aaryan, Oct 17): O1 FastAPI + `claude -p` + plain HTML/JS + SSE; O2 the model never gets `record_verdict`/`approve_note` on the web, an on-page Approve button with a confirm step calls them directly; O3 always "Expert #n" on the web; O4 `web/`
- [x] W1 static shell: chat page and "How It Works" page, dark theme, hero with the node-and-edge drawing, "Try an example" buttons (Aaryan, Oct 17)
- [x] W2 "How It Works" content with real numbers copied from `README.md` / `eval/results.md`; no banned words (Aaryan, Oct 17)
- [x] W3 backend: `POST /api/ask` runs `claude -p` with the MCP server and streams steps; clear message for an expired login or FalkorDB down (Aaryan, Oct 17)
- [x] W4 chat UI: expert cards, tier chips, scope on every no-tie, evidence panel showing the Cypher, ambiguous-name picker, "Expert #n" only (Aaryan, Oct 17)
- [x] W5 human gates: Approve buttons for `record_verdict` / `approve_note`; the model cannot trigger them (Aaryan, Oct 17)
- [x] W6 tests and Builder/Reviewer pass (tests listed in `WebApp.md` section 7); the Coca-Cola example matches `tests/test_golden.py` (Blair A1, Hill A2) (Aaryan, Oct 17)
- [ ] W7 deployable version with a free-tier hosted model, request limits, no write tools exposed: **built last**, cut first if short (→ unassigned)

## P2-0. Loose ends (from checkpoints 5b-9)
- [ ] **Before submit.** Migrate Abdul's container the same way as Aaryan's (`checkpoints.md` 5b): done as a **side container** `falkordb-v4` (v4.22.1, AOF on, port 6381); still to do: stop using the old `falkordb` (`:latest`, AOF off) and move to one container on 6380/3001 (→ Abdul)
- [ ] **Before submit.** Re-register the MCP server for the new port: `claude mcp add tietrace-alpha -e TIETRACE_NEWSROOM=alpha -e FALKORDB_PORT=6381 -- uv run --directory <path> python server.py` (→ Abdul)
- [ ] **Before submit.** Load `research:philip-morris-lung` on Abdul's machine so `test_memory` passes: `uv run python loader.py --case philip-morris-lung --rebuild` needs `cases/philip-morris-lung/raw/` from Aaryan (the cache is not in git) (→ Abdul + Aaryan). Aaryan, Oct 17: zipped as `data/tietrace-pmi-raw.zip` (21 MB, no key inside); unzip it in the repo root
- [x] Fix the 6 `test_discover` failures (Aaryan, Oct 17): `encoding="utf-8"` on 43 text-mode reads/writes across the repo; discover saves its case file before printing. **Abdul: re-run on Windows to confirm**
- [x] Fix the 6 `test_obsidian` failures on Windows (Aaryan, Oct 17): CTRL_BREAK_EVENT + SIGBREAK handler, symlink sub-step skipped only on Windows with a `mklink /J` fallback; found and fixed a junctioned `.trash` / export-folder escape. **Abdul: re-run on Windows to confirm**
- [x] `test_research`: cold-start `bridges3_hub` (Aaryan, Oct 17): the Tier-A filter scanned a 3,035-id list per path; now a hash join. Biggest hub 1.2 s → 0.21 s cold; output identical (2,340 calls compared by the Reviewer); the order is now total so LIMIT is deterministic; limit unchanged
- [ ] Choose a licence for our own code and add `LICENSE` (decide together) (→ Abdul + Aaryan)
- [ ] Decision D9 recorded as decided (case slug is a tool argument, newsroom stays env-only): copy it into `docs/mcp-spec.md` section 3 and the redesign with `(decided by Aaryan)` if not there yet (→ unassigned)
- [ ] E5 profile hand-check: a person fills the verdict columns in `eval/e5_sample.md` (50 authors), then the error rate goes to `eval/results.md` and the README "limits" (→ unassigned)
- [x] Tier meaning follow-up (Aaryan, Oct 17): a DOI with two evidence items now prints them as separate groups split by " | " (DOIs split by "; "); test in `test_notes`

## P2-1. Hardening (`IMPROVEMENT.md` 5g, D11, D12)
- [ ] A Claude Code `PreToolUse` hook that denies `loader.py` calls with `--refresh`, or without a valid `--case` (so the model cannot spend the API budget); unit-test the hook (→ unassigned)
- [ ] Deny rule for reading `.env` in `.claude/settings.json`; check the exact rule syntax in the Claude Code settings docs first (not yet verified) (→ unassigned)
- [ ] Pre-flight RAM check before a build: `INFO memory` against the Docker limit (`maxmemory` is 0, so it is not a safe guard); refuse to start when the new graph would not fit (→ unassigned)
- [ ] Confirm `total_graph_sz_mb` is written to every `status.json` after a build (the loader code calls it; check a real file) (→ unassigned)
- [ ] Optional: Redis ACL user limited to `research:*` with `+graph.ro_query +graph.list`; test it first, cut it if short on time (→ unassigned)
- [ ] `requirepass` on FalkorDB, and CI (open items from the hardening round) (→ unassigned)
- [ ] Idle-graph policy: `GRAPH.DELETE` graphs not in use, keep the raw cache so a rebuild costs 0 API calls; add the commands to `CLAUDE.md` (6-8 graphs fit in Docker's ~7.75 GiB) (→ unassigned)

## P2-2. Case-builder skill and agent (`IMPROVEMENT.md` 5e polish)
- [ ] Check the current Claude Code format for skills / slash commands and sub-agents before writing anything (→ unassigned)
- [ ] A `case-builder` skill or command that runs the target workflow: extract company / topic / names, call `list_cases`, run `discover.py`, write `cases/<slug>/case.json`, stop at the human gates (→ unassigned)
- [ ] Gates written into the skill text: Gate 0 (approve family, topic, expert profiles), Gate 1 (confirm the `--dry-run` estimate), Gate 2 (permission prompt on the exact `--build` command; never allowlisted) (→ unassigned)
- [ ] A dry run with the Philip Morris prompt in a fresh Claude Code session; record the result honestly in `eval/results.md` (→ unassigned)

## P2-3. Corporate-family candidate pipeline (`IMPROVEMENT.md` 5h, D3, D4)
- [ ] New `family.py` that writes `cases/<slug>/candidates.json` rows with `status: proposed` from ROR relationships, Wikidata (class-filtered), GLEIF children and SEC Exhibit 21; resolve the seed by ROR or Wikidata ID, not by name (→ unassigned)
- [ ] Name normalisation (casefold, strip accents and legal suffixes, "(Country)") and show the match type; handle the GLEIF same-name collision (a different "Coca-Cola Company") (→ unassigned)
- [ ] A human sets `approved` / `rejected` and `rel`; the loader reads only `approved` rows (→ unassigned)
- [ ] Foundations and funding vehicles keep their own `rel`; labels like "front organization" appear only as a quoted source phrase with its URL; trade groups with no formal link are `needs_human_source` (→ unassigned)
- [ ] Fixture tests only; no live calls in the suite. The Wikidata SPARQL class filter was written but never run: run it once and record the result (→ unassigned)
- [ ] Measure the real human review time (the "under an hour" estimate is a guess) (→ unassigned)

## P2-4. Data quality and scale
- [ ] Measure what the notes only estimated: build time and API calls for a new case, full-graph load time, snapshot time; write them to `eval/results.md` (→ unassigned)
- [ ] Verify the `RENAME` swap with in-flight queries, and keep the pinned test (undocumented by FalkorDB) (→ unassigned)
- [ ] Author profile guard: replace the "2000-works anomaly" guess with a measured rule, using the E5 sample (→ unassigned)
- [ ] Per-expert funder-coverage denominator in every no-tie scope (works with and without `funders.id:!null`): check it is in all no-tie paths, including a second case (→ unassigned)
- [ ] Slim the COAUTH list properties (about 75% of the graph; a data-model change, so a team decision first) (→ unassigned)
- [ ] Re-run E1/E2 style checks on the second case so the tier claims are not Coca-Cola-only (→ unassigned)
- [ ] Persistence: write a `docker` helper note in `CLAUDE.md` for `redis-cli SAVE` before closing Docker Desktop; keep a `GRAPH.COPY` backup of each case (→ unassigned)

## P2-5. Future work (document only, build later)
- [ ] `start_build` / `build_status` tools, MCP Tasks, an elicitation gate: only after checking SDK support (`mcp` 2.3.0 has no Tasks server) and keeping the "no model-reachable write" rule (→ n/a)
- [ ] `--case` support for the E1-E8 scripts (they are Coca-Cola-specific today) (→ n/a)
- [ ] A registry graph instead of `cases/*/status.json` if the number of cases grows past about 10 (→ n/a)

---

**Gate for phase 2:** a journalist's story about a company we have not loaded goes from "no data" to a checked answer using only the human-approved steps, with tests green and results written honestly. If time is short, keep P2-0 and P2-1 and cut P2-3 first (`IMPROVEMENT.md` cut order: 5h, 5g, 5e polish).
