# TieTrace v2: redesign (Graph Hacks, Oct 15-18 2026)

Written Oct 14 2026, about 5 hours before the event starts. Notes and sketches only; all code is written during the event.
Facts marked **(verified Oct 14)** were checked against live pages today. Everything else is a day-1 test, listed in section 11.

---

## 1. What changed and why

| Problem in v1 | Fix in v2 |
|---|---|
| The validation case (GEBN scientists) is a direct funder lookup, so it doesn't prove the graph matters | The headline evidence is now a **temporal hold-out test**: do ties computed from the past predict real direct ties in the future better than a "just publishes a lot" baseline? (E2) |
| "69% of experts are tied" may just mean everyone in a busy field is within two hops | A **placebo test** with degree-matched random circles gives a **lift** number. If lift is near 1x, the score is noise and we say so (E1) |
| Max flow as tie strength may equal a plain count (it does at 2 hops, see 5.3) | Max flow is **not** a headline claim. It runs only at depth 3, and we publish whether it changed rankings (E4) |
| Co-authorship treated as conflict of interest | Ties are split into **three tiers** with different wording. Only explicit funding/employment records (Tier A) are "findings" |
| Scope too big for one beginner in four days | One topic-company pair, 6 tools, 2 verdict types, no custom UI. Cut lines in section 8 |
| Demo overclaimed ("found what the NYT found") | Demo says "consistent with public reporting". Live demo expert names are **anonymised**; names appear only in the documented public case |
| Self-scored 27-28/30 from a rubric built on guesses | No numeric self-score. Section 10 maps deliverables to the published rules |
| Risk that a beginner can't explain AI-built code (rules 12-13) | Section 9: explain-it checklist and an AI-use log |
| Review pass (v2.1): Tier A ignored the mega-paper cap, so one 40-author funded paper made everyone a "finding" | Large-paper rule now applies to Tier A too; E6 ablates both tiers |
| Review pass: Tier A mixed company vs foundation money and per-paper affiliation vs employment | Tier A split into A1 / A2 with different wording (section 3) |
| Review pass: "score" was never defined, yet E2 and Q5 compare scores | Score formula fixed before data loads (section 4) |
| Review pass: topic-only loading hides ties through non-topic papers; "recheck" can't show anything new on static data | Load scope stated (section 4); staged year-slice loading for the recheck demo (section 8) |
| Review pass: Verifier rule ("every claim needs a DOI") can't apply to "no tie" claims | Separate rule for negative proofs (section 3) |
| "No code before Oct 15" vs earlier throwaway research scripts | Earlier measurements are **re-run during the event** (E1/E2). Old scripts stay out of the repo |

---

## 2. One-line pitch

**TieTrace checks how connected an expert is to the company in a story: explicit funding or employment first, repeated collaboration second, wider network proximity last. Every tie shows its paper and DOI, "no tie found" is a scoped proof, and a human reviewer's ruling re-scores every affected case.**

User: a health or science reporter (lead) or fact-checker who must quote independent experts by a deadline.
Today: Googling names and reading disclosure sections by hand. Existing tools are human matchmaking (SciLine), money paid to US doctors (Open Payments), or paid publisher reviewer-conflict tools (Prophy).
Tracks: **1 (primary) and 2** (per-newsroom memory, verdicts that change later checks).

---

## 3. What a "tie" means (the part judges and journalists will test)

| Tier | Definition | Wording in output | Used in a sourcing note? |
|---|---|---|---|
| **A1: explicit record, company** | Authored a work (**25 or fewer authors**) whose funder is the company itself, or lists an affiliation with the company or its corporate family on a work, on this topic | "OpenAlex records X as author of a work funded by Y (DOI)" or "lists affiliation Z on a work (DOI)". Affiliations are per paper, not an employment record | Yes, after human approval |
| **A2: explicit record, foundation or large paper** | Funder is the company's **foundation**, or the only A1 evidence is a work with more than 25 authors | Same wording plus "foundation grant" or "large multi-author paper" | Only with human approval and the label shown |
| **B: repeated collaboration** | Co-authored **2+ papers** with a Tier-A person in the last 5 years, counting only papers with **25 or fewer authors** | "Co-authored 3 papers with a Tier-A person since 2021" | Only with human approval |
| **C: network proximity** | Path of 2-3 co-authorship hops to a Tier-A person | "Within 3 hops (context only)" | Never |

Rules for every output:
- Never say "conflicted" or "not independent". Say "ties found in the research record".
- "No tie found" always carries its scope: hops, years, and **data coverage** (share of topic works that have funder and affiliation data). OpenAlex is incomplete, so absence of a tie is not proof of independence.
- Every **positive** tie maps to stored edges with a DOI. A deterministic Verifier blocks anything that doesn't.
- Every **"no tie found"** claim must carry the stored query name plus scope (hops, years, coverage). It has no DOI, so the Verifier checks the scope fields instead.
- Tier B and C are defined relative to the Tier A set (A1 and A2 together, labelled).

---

## 4. Graph model

### Research graph (`research:<topic>`, loaded once per topic-company pair)

| Node | Properties |
|---|---|
| `Person` | oa_id, name, orcid, works_count, `profile_flag` (ok / no_orcid / works_count_anomaly) |
| `Work` | oa_id, doi, year, n_authors, title |
| `Org` | oa_id, name, type, `target` (true for the company family) |
| `Funder` | oa_id, name, `target` |
| `Topic` | oa_id, name |

| Edge | Notes |
|---|---|
| `AUTHORED` Person to Work | author position |
| `AFFILIATED` Person to Org | year and the DOI it came from |
| `FUNDED_BY` Work to Funder | |
| `PARENT_OF` Org to Org | corporate family, walked with a bounded variable-length pattern |
| `ABOUT` Work to Topic | |
| `COAUTH` Person to Person (**derived at load, stored in both directions**) | `n_papers`, `w` = sum of 1/(n_authors-1) over shared papers (fractional counting, so a 40-author paper adds little), `recency_w`, `small_papers` (count with n_authors <= 25), `dois[]` |

Precompute everything time-based at load. FalkorDB has no temporal arithmetic.

**Load scope (decide now, count it in E7):** (1) **all** works funded by the company or its foundation, any topic (about 800 for Coca-Cola, cheap), so the Tier A set is complete; (2) all works on the topic since 2010; (3) for each expert in the top-N list, their co-authors' works only if budget allows. Ties made through non-topic papers are invisible otherwise, so say that in the coverage note.

**Score (fixed before data loads, used by E2 and Q5):** `score = tier_weight + sum of w over COAUTH edges to Tier-A people (small papers only, last 5 years)`, with `tier_weight` A1 = 100, A2 = 50, B = 0, C = 0. Rank by tier first, score second. Do not change after seeing E1/E2 results.

### Memory graph (`newsroom:<id>`, one graph per newsroom, multigraph)

| Node | Properties |
|---|---|
| `Case` | story name, company, topic |
| `Check` | expert oa_id, tier, score, bridge count, timestamp, JSON snapshot of top paths |
| `Verdict` | type (`PAPER_EXCLUDED` or `PROFILE_MERGE`), target oa_id, reason, reviewer, timestamp |

Two verdict types only. Verdicts are read at query time and passed as exclusion parameters to the scoring queries, so no graph copy is needed.

---

## 5. Graph work (what makes FalkorDB necessary)

### 5.1 Questions the agent asks

| # | Question | How (sketch, to be written and tested during the event) |
|---|---|---|
| Q1 | Who are the most active experts on topic T, excluding company staff? | Aggregate authors over `ABOUT` works; full-text index on `Work.title` as entry point |
| Q2 | What is this expert's tier and evidence? | Tier A: direct match on `FUNDED_BY`/`AFFILIATED` including `PARENT_OF*0..3`. Tier B: `COAUTH` edges to Tier-A people filtered by `small_papers >= 2` and recency |
| Q3 | Prove "no tie within k hops" | Use native **`algo.BFS`** from the expert with `COAUTH` filtered as above, check no Tier-A node is reached within k. Fallback: bounded path existence `(e)-[:COAUTH*1..k]-(a:TierA)`. Returns scope text, not just a boolean. This guarantees at least one native FalkorDB algorithm does real work even if max flow is dropped |
| Q4 | Who are the bridges? | The intermediate Persons and DOIs on the strongest paths (`algo.SPpaths` with `relDirection: 'both'`, or plain Cypher paths, whichever works on the toy graph) |
| Q5 | If this paper is ruled out, who changes? | Run Q2 for all case experts with and without the exclusion list; diff tiers and scores |

All model-reachable reads go through `GRAPH.RO_QUERY`, with a query timeout and a row limit. The model never writes freely and never holds database credentials. Writes (`save_check`, `record_verdict`) are fixed parameterised queries. **Every tool answer includes the executed Cypher text** so a judge can read it.

### 5.2 Why the graph is needed, stated honestly
Tier A alone is a lookup. The graph earns its place in: corporate-family recursion, Tier B/C multi-hop with per-edge filters, scoped negative proofs, bridge paths with DOIs, and verdicts that re-score every case. The removal test: without FalkorDB the product can't answer Q3-Q5 interactively across cases.

### 5.3 Max flow, honestly
FalkorDB's `algo.maxFlow` takes `sourceNodes`, `targetNodes`, `relationshipTypes`, `capacityProperty`; it is **directed**, capacities are **per edge**, and multiple sources and sinks are supported with a virtual super-node **(verified Oct 14)**. Consequences:
- Co-authorship is undirected, so store `COAUTH` in both directions.
- Capacity is per edge, so node-disjoint paths need node splitting. At depth 2, node-disjoint flow equals the count of distinct bridging co-authors, so flow adds nothing there.
- Plan: compute a plain Cypher bridge count for depth 2. Use `algo.maxFlow` only for depth-3 "independent routes" with `w`-based capacities. E4 compares it to the Cypher aggregate. **If rankings don't change, we drop the max-flow claim and say we tested it.** That honesty is the point.

---

## 6. Agents and tools (MCP)

Build a thin Python MCP server (official MCP SDK) exposing fixed tools. Optionally run FalkorDB's own MCP server alongside for exploration; it supports read-only mode via `FALKORDB_DEFAULT_READONLY=true` **(verified Oct 14)**.

| Tool | Does |
|---|---|
| `find_experts(topic, n)` | Q1 |
| `tie_report(expert_id)` | Q2 plus tier, evidence DOIs, profile flag |
| `prove_independence(expert_id, k)` | Q3 with coverage stats in the answer |
| `bridges(expert_id)` | Q4 |
| `record_verdict(...)` | Writes a `Verdict`, returns Q5 impact ("changed N experts across M checks") |
| `recheck(expert_id)` | Compares a stored `Check` to a fresh run and reports what changed |

Agent roles (all one MCP client plus the deterministic Verifier):
- **Scout and Tie-Checker:** the LLM picks tools.
- **Verifier (no LLM):** rejects any claim without stored edge and DOI.
- **Writer:** drafts the sourcing note and disclosure line. **Human approves before anything is exported.**
- **Review Desk:** surfaces the 3 papers or profiles whose rulings would change the most scores (a count over current cases).

Client: Claude Desktop or any MCP-compatible client as the agent, plus FalkorDB Browser to show the graph. No custom UI unless there is spare time. Decide tonight which LLM and credits you will use.

---

## 7. Evaluation protocol (the main deliverable)

Pass or fail rules are fixed now, before any data is loaded.

| # | Test | Method | Pass rule | If it fails |
|---|---|---|---|---|
| **E1** | Placebo lift | Compute Tier-B/C rates for real experts. A **placebo circle** = a random set of non-company authors on the same topic, same size as the Tier-A set and matched on works_count band; repeat 100 times. Lift = real rate / mean placebo rate, with a bootstrap 95% interval | Lift >= 1.5x for Tier B **and** the interval excludes 1.0 | Report honestly; Tier C is labelled context only; headline becomes Tier A plus tier-labelled evidence |
| **E2** | Temporal hold-out | Build the Tier-A set **and** ties only from works up to year Y (e.g. 2018); no later data may leak in. Target = expert gains a new Tier-A link in Y+1..Y+5. Compare AUC for: volume-only, direct-only, **plain count of co-authors with Tier-A people**, and the weighted Tier B score. Need **at least 30 positives**, else report "underpowered" | Weighted Tier B beats both the volume baseline and the plain-count baseline by >= 0.05 AUC | Drop the "predicts" language; keep the tool as an evidence viewer |
| **E3** | Known-case sanity | The five GEBN scientists appear as Tier A with DOIs | All five found | Fix data issues (split profiles) or note the gap |
| **E4** | Flow vs aggregate | Depth-3 ranking from `algo.maxFlow` vs Cypher weighted aggregate | Spearman < 0.95 or top-10 changes | Remove max-flow claim from README |
| **E5** | Profile guard | Hand-check 50 random experts for merged or split profiles | Report the error rate | Flag more aggressively |
| **E6** | Mega-paper ablation | Tier A and Tier B counts with vs without the 25-author cap | Report the share of ties that depend on big papers | Keep cap, show the number |
| **E7** | Coverage | Share of topic works with funder and affiliation data, plus load scope (section 4) | Report it | Put it in every "no tie found" message |
| **E8** | Golden tests | About 15 fixed checks: the five GEBN scientists, Blair's split profile merges after a `PROFILE_MERGE`, a verdict changes a known score, the Verifier rejects a claim with no DOI, `GRAPH.RO_QUERY` rejects a write | All pass | Fix or show the failures in the README |

Report all numbers, including losses. A README that says "E2 failed, so we don't claim prediction" beats a README that hides it.

---

## 8. Build plan (IST) with go/no-go gates

Event start: Oct 15 12:01 AM IST; "hour 1" below means the first hour after that. Do not stay up for it, sleep and start fresh.

**Tonight (Oct 14), non-code:** register on WeMakeDevs; pull the FalkorDB Docker image and open the Browser; get a free OpenAlex key; install Python, the FalkorDB client and the MCP SDK (installing is fine, no project code); pick your LLM client; join the FalkorDB Discord; look up OpenAlex topic IDs; sleep.
OpenAlex budget **(verified Oct 14)**: free key gives $1/day, about 10,000 list calls; singleton lookups are free; no key gives $0.10/day; limit 30 requests per second with a key; `per_page` max 100; cursor paging beyond 10,000 results. Use `per_page=100`.

| When | Task | Gate |
|---|---|---|
| **Oct 15, hours 1-2** | Toy 10-node graph: test `COAUTH` in both directions, `algo.maxFlow` direction, path queries, `GRAPH.RO_QUERY`. Confirm OpenAlex filters for funders and `select` (section 11) | Directions understood |
| Oct 15, hours 2-8 | Loader for **one pair**: obesity / physical activity x Coca-Cola. Works, authors, orgs, funders, `PARENT_OF`, `COAUTH` with `w` and `small_papers` | **If no Cypher query returns a dated Tier-A path with DOI by hour 8, switch to the backup (section 12)** |
| Oct 15, evening | Coverage stats (E7); mark Tier-A set | |
| **Oct 16 morning** | Tools Q1-Q4 and Verifier. Run **E3, E1** (E1 is cheap, E2 reuses the loaded data with a year cut, no reload) | E3 passes, E1 number known |
| **Oct 16 afternoon** | Run **E2, E5, E8 skeleton**. Also load the staged newer-year slice for the `recheck` demo | **If E1 and E2 both fail, keep the evidence-viewer framing and cut Tier B claims. Do not hide it** |
| Oct 16, evening | MCP server; test from the client | Full question works end to end |
| **Oct 17 morning** | Memory graph: `save_check`, `recheck`. OpenAlex data is static, so the demo `recheck` works by loading works **up to year Y first, saving the check, then loading the newer slice** and re-running | |
| Oct 17 midday | Verdicts and Q5 impact; Review Desk ranking; Writer note with approval step | |
| Oct 17 afternoon | E4, E6; README draft; second pair only if everything above works | **Demo flow works end to end or cut stretch items** |
| **Oct 18 morning** | Final numbers, README, architecture diagram | |
| Oct 18 midday | Record 90-second video; optional deployment | |
| **Oct 18, by 6 PM** | **Submit** | Leaves 6 hours of buffer before 11:59 PM |

**Cut order if behind:** second topic, deployment, blog post, Review Desk ranking, flow test E4.
**Must ship:** loader, Tier A/B, Q3 proof, Verifier, one verdict type with impact report, one saved check with `recheck`, E1/E2/E3/E7 numbers, README, video.

---

## 9. You must be able to explain this (rules 12-13, verified Oct 14)

The rules say participants must understand the code and explain the graph model, the queries, the architecture and the technical decisions. Entirely AI-generated work without real participant contribution may be rejected. Practise these in plain language:

1. **Node vs edge?** A node is a thing (person, paper, company); an edge is a relationship (authored, funded by).
2. **Why a graph here?** Ties are chains of relationships; following them is the core question.
3. **What does the agent do?** It picks a tool, the tool runs a fixed read-only query, the Verifier checks evidence, a human approves outputs.
4. **Why read-only?** The model suggests, a read-only query decides. It can't change or leak data.
5. **What is MCP?** A standard way to expose tools to an AI client.
6. **What is a tier?** A is an explicit record, B is repeated collaboration, C is network proximity. Only A is a finding.
7. **What is the placebo test?** Random matched groups show what "tied" looks like by chance.
8. **What is the hold-out test?** Use the past to predict the future and compare to a naive baseline.
9. **What is max flow and why did we limit it?** Max number of independent routes; at 2 hops it equals a count.
10. **What happens when a reviewer rules a paper out?** A `Verdict` is saved; scoring re-runs with it excluded; impact is reported.

Keep an **AI-use log** in the repo: what you decided, what the AI wrote, what you verified. Disclose it in the README.

---

## 10. Submission checklist (rules page, verified Oct 14)

- [ ] Public repo
- [ ] README with setup instructions and the graph data model (use the diagram in section 4)
- [ ] The Cypher queries and graph algorithms the product depends on (list Q1-Q5 and `algo.maxFlow` if kept)
- [ ] Demo video
- [ ] Live deployment, or complete local setup instructions
- [ ] Tracks named: **Agents That Act on Connected Data** and **Agent Memory and Coordination**
- [ ] AI-use disclosure
- [ ] Data note: OpenAlex (CC0); FalkorDB licence (SSPLv1 per earlier notes, confirm on the repo before writing it)
- [ ] Ethics section (section 13)

README outline: problem, 90-second GIF, tiers, data model, queries, evaluation (E1-E7 with losses), human gates, ethics, setup, AI disclosure.

---

## 11. Day-1 unknowns to test (not assumed)

| Unknown | Test |
|---|---|
| OpenAlex works filter for funder (`funders.id` or `grants.funder`) and the `select` param to trim fields | One query each in hour 1 |
| Corporate-family fields for Coca-Cola entities in `associated_institutions` | Inspect records |
| Share of topic works with funder or affiliation data | E7 |
| Bounded variable-length path syntax and speed on the full graph | Toy graph, then real graph |
| Whether `algo.SPpaths` or plain Cypher is better for bridge paths | Toy graph |
| Graph size vs your machine; FalkorDB Cloud free tier is small, so plan for local Docker | Count nodes and edges after load |

---

## 12. Backup

If the loader isn't producing Tier-A paths by Oct 15 hour 8: switch to **LayoverGuard** (transit-visa route checker). Caveats from earlier notes: the only open-licence route data (OpenFlights) dates from 2014 and the graph role is weaker. Decide at the gate; don't drift.

---

## 13. Ethics and wording

- Ties are evidence for a human decision, never an accusation.
- Names appear only in the documented public case (GEBN). Live-case experts are shown as "Expert #1..#n".
- "No tie found" always includes hops, years and coverage.
- Right of reply: a disputed tie becomes a reviewer-queue item; accepted corrections become verdicts.
- Demo wording: "OpenAlex records these authors on Coca-Cola-funded papers, consistent with what journalists reported in 2015." Do not claim the tool reproduced the investigation.
- Data limits: OpenAlex funder and affiliation data are incomplete; merged and split author profiles exist; absence of a tie is not independence.

---

## 14. 90-second demo script

**All numbers below (5 authors, 61%, 7 scores, 2 checks, E1 lift) are placeholders. Replace them with real output from the E-runs before recording; never show a number you did not compute.**

1. **Problem (15 s):** "Reporters quote 'independent' experts. Ties are hidden in co-authors and funding."
2. **Documented case (20 s):** Coca-Cola x obesity. Five authors show Tier A with DOIs. "Consistent with 2015 reporting."
3. **Live check (30 s):** Anonymised experts ranked by tier. One has a Tier B path with bridges and DOIs. One shows "no Tier A tie, no Tier B tie, nobody within 3 co-author hops (BFS), data coverage 61%". Show the E1 lift number.
4. **Human verdict and memory (20 s):** Reviewer rules out one consortium paper. "Changed 7 scores across 2 checks." Saved to the newsroom graph. Later `recheck` shows a new tie.
5. **Close (5 s):** "Verify once, apply everywhere."

---

## 15. Review notes: open risks (v2.1)

- **Track 2 is thin and crowded.** The memory story is only `recheck` plus verdict impact. Keep both tracks named, but present Track 1 as the main entry and say plainly what the memory graph does.
- **Oct 16 is the heaviest day** (E1, E2, E3, E5, E8, tools, MCP). If it slips, drop E5 and E8 to a short list before touching E1/E2 or the Verifier.
- **Old numbers (16% direct vs 69% within 2 hops) came from throwaway scripts in v1.** Do not quote them anywhere until E1 reproduces them.
- **E4 can still drop max flow.** That is fine now, because Q3 uses `algo.BFS` and counts as the native algorithm.
- **Beginner rule:** if you cannot explain a query in section 5 in plain words, simplify it before you submit (section 9).

---

## Sources (verified Oct 14 2026)
- Event rules: https://www.wemakedevs.org/hackathons/falkordb/rules
- FalkorDB max flow: https://docs.falkordb.com/algorithms/maxflow
- FalkorDB MCP server: https://docs.falkordb.com/genai-tools/mcpserver
- OpenAlex authentication and limits: https://help.openalex.org/api/authentication/
- OpenAlex quick reference: https://help.openalex.org/api/llm-quick-reference/
