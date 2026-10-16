# TieTrace

FalkorDB Graph Hacks entry (Tracks 1 and 2). **Submit by Oct 18 2026, 6 PM IST.**
TieTrace checks how connected an expert is to the company in a story, using an OpenAlex research graph in FalkorDB. Every tie comes with its DOI, and every "no tie found" comes with its scope.

## Team and attribution (two people: Abdul and Aaryan)
- **Who is working:** find out at the start of each session from `git config user.name` (Abdul = `23abdul23`); if it does not clearly say Abdul or Aaryan, ask once and remember it for the session. Both are beginners and must be able to explain everything (rules 12-13).
- **Record who did what, in every doc you touch:**
  - `checkpoints.md`: each ticked box ends with `(Abdul)` or `(Aaryan)` plus the date (no Owner column; claims are `(→ name)` on items). Never tick a box on someone else's behalf without naming who actually did it.
  - `AI_USE.md`: every new row has a `Who` column value; `Learned` lines end with `[Abdul]` or `[Aaryan]`.
  - Other docs (spec, redesign, README, `eval/results.md`): put a one-line `Last updated by <name>, <date>: <what>` at the top when you change them. Decisions in the spec/redesign get `(decided by <name>)`.
  - Commits: name the person in the message body (`Work by: Abdul`); the git author already shows it.
- **Avoid collisions:** before starting a checkpoint, check its items for a `(→ name)` claim in `checkpoints.md`. If they belong to the other person, do not edit it without asking. Claim work by writing `(→ name)` on the item, and pull/merge before editing shared docs.
- **Hand-off:** when stopping mid-checkpoint, leave a one-line note under that checkpoint in `checkpoints.md` saying what is done and what is next.

## Read first
- `tietrace-v2-redesign.md`: tiers, graph model, queries Q1-Q5, evaluation E1-E8, build plan, ethics.
- `docs/mcp-spec.md`: the MCP tool contract (inputs, outputs, errors) and the diagrams. **Code follows this file.** If the code needs to differ, update the spec in the same change.

## Architecture
- **Claude Code is the agent and MCP client.** The main session is the orchestrator; the subagents live in `.claude/agents/`.
- **The Python MCP server makes no LLM calls**, and it uses no API key and no Ollama. If a scripted LLM step is ever needed, use `claude -p`, never the API SDK.
- **FalkorDB runs locally in Docker** and holds one research graph (`research:<topic>`) plus one memory graph per newsroom (`newsroom:<id>`).
- **No custom UI and no crawler.** Journalist URLs are read with WebFetch; the data comes from the OpenAlex API.

## Rules the code must keep
- **Model-reachable reads use `GRAPH.RO_QUERY`**, with a timeout and a row cap. Writes are fixed, parameterised queries only. Never build Cypher by string formatting with user input.
- **Every tool returns the envelope** from spec section 4 (`ok`, `data`, `cypher`, `scope`, `verdicts_applied`, `warnings`), including the Cypher it executed.
- **The newsroom comes from the `TIETRACE_NEWSROOM` env var**, never from a tool argument.
- **The Verifier rejects claims without evidence:**
  - a positive claim needs a stored edge with a DOI;
  - a "no tie" claim needs a `query_name` plus scope (hops, years, coverage).
- **Banned output words:** "independent" and "conflicted". Say "ties found in the research record".
- **Experts appear as "Expert #n"** unless the case has `public_case=true` (the GEBN case).
- **Time-based fields are precomputed at load**, because FalkorDB has no temporal arithmetic.
- **`COAUTH` is stored in both directions; always match it with `->`.** FalkorDB counts undirected matches inconsistently (see `AI_USE.md`).
- **Tier rules** (decided Oct 15, redesign section 3):
  - Tier A counts Coca-Cola money on any topic, with each piece of evidence labelled on-topic or off-topic;
  - an affiliation with a bottler (`PARENT_OF.rel = "bottler"`) is Tier A2;
  - "recent" means 2021 onwards (`Meta.recent_year`);
  - Tier C traverses `COAUTH_B` only.
- **FalkorDB quirks** (details in `AI_USE.md`):
  - results are capped at 10,000 rows, so count in Cypher;
  - to traverse from a list of IDs, use `WHERE x.oa_id IN $ids`; `UNWIND` plus an inline map followed by a traversal skips the index;
  - after a variable-length pattern, pass relationship properties through `WITH`;
  - `algo.BFS` takes no property filter; use `COAUTH_B`;
  - `algo.BFS` returns no row when nothing is reached.
- **Never fabricate data.** Every node and edge comes from OpenAlex (or the hand-checked `COKE_FAMILY` list). If the API is down, build from the `data/raw/` cache and mark the missing fields as unknown. Synthetic data belongs only in test fixtures.
- **Score formula is fixed** (redesign section 4). Do not tune it after seeing E1/E2 results.
- **Human-only tools never get allowlisted:** `record_verdict`, `approve_note`, `set_policy`. Claude Code's permission prompt is the human gate.

## Working style
- **The author is a beginner** and must be able to explain every query and decision to the judges (rules 12-13). Keep the code boring and small, and prefer plain Cypher over clever abstractions.
- **Each checkpoint runs as a Builder/Reviewer loop.**
  - A Builder agent writes and runs the code.
  - A separate, strict Reviewer agent verifies it against the spec (correctness, efficiency, alignment) and writes the tests.
  - Must-fix findings go back to the Builder until the Reviewer passes the work.
  - Tests are never weakened to make them pass.
  - Then tick `checkpoints.md`.
- **Log AI help in `AI_USE.md`:** what was decided, what the AI wrote, and what was verified.
- **Report evaluation results honestly**, including failures. Never put a number in the README or demo that was not computed.
- **Cut order if behind:** second topic, deployment, blog post, Review Desk, `set_policy`, the max-flow test (E4).

## Commands
- **FalkorDB:** start with the AOF `docker run` command under *FalkorDB data persistence* below (Browser at http://localhost:3000)
- **Ports on Abdul's machine (Oct 15):** 6379 and 3000 are taken by other containers, so FalkorDB runs on host ports 6380 and 3001 (`-p 6380:6379 -p 3001:3000`). Set `FALKORDB_PORT=6380` for `loader.py`, tests and the server (default 6379). Browser: http://localhost:3001.
- **FalkorDB data persistence:** the container is named `falkordb` and uses the named volume `falkordb-data`. The volume alone is not enough: FalkorDB keeps the graph in RAM and only snapshots it now and then, so a hard stop lost data once (Oct 16). AOF fixes that (at most about 1 s lost on a crash). Start it with `docker start falkordb`.
  - **New container:** `docker run -d --name falkordb -p 6379:6379 -p 3000:3000 -v falkordb-data:/var/lib/falkordb/data -e REDIS_ARGS="--appendonly yes --appendfsync everysec" --stop-timeout 120 falkordb/falkordb:v4.22.1`
  - **Pin `v4.22.1`, never `:latest`** (decided by Aaryan, Oct 16). On Oct 8 `:latest` pointed to 6.0.2 (Rust rewrite), where `toLower()` on a title with U+FFFD errors and then crashes the server; 4.22.1 cannot read 6.x data files, so switching means a fresh volume plus a rebuild from cache.
  - **Aaryan's machine:** volume `falkordb-data-v4` (old 6.0.2 data kept in `falkordb-data` as a backup), Browser on host port 3001 (`-p 3001:3000`), Redis on 6379, Docker context `default`.
  - **Migrating an existing container (keeps the data):** first `docker exec falkordb redis-cli CONFIG SET appendonly yes` and wait until `INFO persistence` shows `aof_rewrite_in_progress:0`; then `redis-cli SAVE`, `docker stop -t 120 falkordb`, `docker rm falkordb`, and the `docker run` above. Turning AOF on in a fresh container before the AOF file exists would start empty.
  - **Check:** `docker exec falkordb redis-cli CONFIG GET appendonly` must say `yes`.
- **Build the research graph:**
  - `uv run python loader.py --rebuild` rebuilds from the `data/raw/` cache, with zero API calls once the cache exists;
  - add `--refresh` to re-download;
  - the OpenAlex key lives in `.env` (`OPENALEX_API_KEY`). Never print or log it.
- **Tests:** `uv run python tests/test_loader.py` (plain asserts, no pytest).
- **Register the server:** `claude mcp add tietrace-alpha -e TIETRACE_NEWSROOM=alpha -e FALKORDB_PORT=6380 -- uv run --directory C:\Users\Azeem\Desktop\TieTrace python server.py` (`uv run --directory` finds the venv from any folder; drop `FALKORDB_PORT` if FalkorDB is on 6379). The server exits at once if `TIETRACE_NEWSROOM` is unset.
