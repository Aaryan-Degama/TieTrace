# TieTrace

Last updated by Aaryan, Oct 17 2026: P2-1 hardening (loader hook, `.env` deny, RAM check, password, ACL, CI, idle graphs).

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
- **The only UI is `web/`** (local, binds 127.0.0.1): it adds no analysis, and the human-only tools are reachable only by an on-page confirm click, never by the model (decided by Aaryan, Oct 17). **No crawler.** Journalist URLs are read with WebFetch; the data comes from the OpenAlex API.

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
- **Human-only tools never get allowlisted:** `record_verdict`, `approve_note`, `set_policy`. Claude Code's permission prompt is the human gate; `.claude/settings.json` has `permissions.ask` rules for them so the prompt appears even in auto mode (docs: explicit ask rules still force a prompt). Don't demo in bypass-permissions mode.

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
- **FalkorDB:** start with the AOF `docker run` command under *FalkorDB data persistence* below (Browser at http://127.0.0.1:3001)
- **Ports on Abdul's machine (Oct 17):** 6379/3000 are taken by other containers, and after a Windows restart 6380-6479 became reserved (`netsh interface ipv4 show excludedportrange protocol=tcp`), so FalkorDB runs as container `falkordb-main` on host ports **6500** (DB) and 3001 (Browser), volume `falkordb-data-v4`. Set `FALKORDB_PORT=6500` for the loader, tests, the web app and the server. Old containers `falkordb` (`:latest`) and `falkordb-v4` are stopped and kept; never start them against that volume at the same time.
- **Bind to localhost only** (Aaryan, Oct 16): every `-p` is `127.0.0.1:...`, so FalkorDB (no password) is not reachable from the network.
- **FalkorDB data persistence:** the container is named `falkordb` and uses the named volume `falkordb-data`. The volume alone is not enough: FalkorDB keeps the graph in RAM and only snapshots it now and then, so a hard stop lost data once (Oct 16). AOF fixes that (at most about 1 s lost on a crash). Start it with `docker start falkordb`.
  - **New container:** `docker run -d --name falkordb -p 127.0.0.1:6379:6379 -p 127.0.0.1:3001:3000 -v falkordb-data:/var/lib/falkordb/data -e REDIS_ARGS="--appendonly yes --appendfsync everysec" --stop-timeout 120 falkordb/falkordb:v4.22.1`
  - **Pin `v4.22.1`, never `:latest`** (decided by Aaryan, Oct 16). On Oct 8 `:latest` pointed to 6.0.2 (Rust rewrite), where `toLower()` on a title with U+FFFD errors and then crashes the server; 4.22.1 cannot read 6.x data files, so switching means a fresh volume plus a rebuild from cache.
  - **Aaryan's machine:** volume `falkordb-data-v4` (old 6.0.2 data kept in `falkordb-data` as a backup), Browser on host port 3001 (`-p 127.0.0.1:3001:3000`), Redis on 6379, Docker context `default`.
  - **Migrating an existing container (keeps the data):** first `docker exec falkordb redis-cli CONFIG SET appendonly yes` and wait until `INFO persistence` shows `aof_rewrite_in_progress:0`; then `redis-cli SAVE`, `docker stop -t 120 falkordb`, `docker rm falkordb`, and the `docker run` above. Turning AOF on in a fresh container before the AOF file exists would start empty.
  - **Check:** `docker exec falkordb redis-cli CONFIG GET appendonly` must say `yes`.
- **Build the research graph:**
  - `uv run python loader.py --rebuild` rebuilds from the `data/raw/` cache, with zero API calls once the cache exists;
  - add `--refresh` to re-download;
  - the OpenAlex key lives in `.env` (`OPENALEX_API_KEY`). Never print or log it.
  - **RAM check before a real build (a rough guard, not a promise).** The new graph's size is estimated from the case's last `total_graph_sz_mb`, or 1000 MB if there is no earlier build. The loader refuses if either test fails; `--skip-ram-check` overrides.
    - **Test 1:** FalkorDB's `used_memory` plus the new graph must stay under 90% of `maxmemory`, or of `total_system_memory` when `maxmemory` is 0. The staging build keeps the old graph until the RENAME swap, and `used_memory` already counts it.
    - **Test 2 (Linux):** the new graph must fit in `/proc/meminfo` MemAvailable.
    - **What it cannot see:** with `maxmemory` at 0, `total_system_memory` is the whole machine (on Aaryan's machine, Oct 17: 15.01 GiB, while only about 3.98 GiB was available), so test 1 alone would wave through a build that cannot fit. Test 2 catches that on native Linux Docker. Neither test can see a `docker run --memory` limit, and on Docker Desktop MemAvailable is the host's, not the VM's. Also, `total_graph_sz_mb` overstates real RAM (784 + 727 MB reported, against 1.19 GiB `used_memory` for both graphs).
    - **The real guard is `maxmemory`** on FalkorDB (for example `--maxmemory 6gb` in `REDIS_ARGS`): Redis then refuses writes past it, and test 1 uses it.
- **Guard hook (model-issued Bash only):** `.claude/hooks/guard_loader.py` (`PreToolUse`, `onFailure: block`, which needs Claude Code 2.1.295 or later; older versions let a crashed hook through) blocks:
  - `loader.py --refresh`;
  - `loader.py` without a `--case <slug>` that has `cases/<slug>/case.json`;
  - any command naming `.env`, except inside a `git commit -m` message. For a message that would need `$(...)`, write it to a file and use `git commit -F <file>`.

  **It is a text check and a seatbelt, not a sandbox.** The real limit on the API budget is the OpenAlex key itself (its spending limit), not this hook. Humans in their own terminal are not affected; run `--refresh` yourself.
  - **Whole-text rules come first.** If the text mentions loader outside a pure reader part, any `--ref` anywhere is refused. If a loader run is found (or loader sits inside `$(...)`, backticks or a heredoc), the last `--case` must be a real case. Python reading code from `-c`, `-` or a heredoc is refused while loader is mentioned.
  - **Known evasions (still get through):**
    - a `$(...)` name built from pieces (`$(echo lo''ader.py)`, `printf`), and a `--case` taken from a later, unrelated command (a `$(...)` name with a quoted or escaped `--ref` flag is caught);
    - a variable (`x=loader.py; python $x`);
    - `cp loader.py y.py`, then running `y.py`;
    - a command built with `printf` or `eval` from pieces;
    - a script file that imports loader;
    - a `python -c` string run without the word loader in the command.
  - **Known false positives, and what to do instead:**
    - `uv run ruff check loader.py` and `python -m py_compile loader.py` are blocked: run them in your own terminal.
    - `sed`/`awk` on `loader.py` are blocked (both can run commands): use the Read tool or `head`/`grep`.
    - A heredoc with unbalanced quotes that mentions loader is blocked: use the Edit tool.
    - Searching for the file name: `grep '[.]env'` instead of `grep .env`.
    - Any mention of `--ref...` next to a loader command is blocked, even in text.

  Tests: `tests/test_hooks.py`.
- **`.env` deny rules:** `permissions.deny` has `Read(/.env)` and `Edit(/.env)` (in project settings `/` means the primary working directory, per the permissions docs). They cover the file tools and the Bash file commands Claude Code recognises (`cat`, `head`, `sed`, redirections), but not a script that opens the file itself (`python -c "open('.env')"`); the guard hook blocks those when the command text names `.env`.
- **Optional: a FalkorDB password.** All code connects through `loader.connect()`, which reads `FALKORDB_PASSWORD` (default: none). To turn it on: `docker exec falkordb redis-cli CONFIG SET requirepass '<pw>'` (lasts until the container restarts; to keep it, add `--requirepass <pw>` to `REDIS_ARGS` in the `docker run` line). Export `FALKORDB_PASSWORD` for the loader, tests and the web app, and add `-e FALKORDB_PASSWORD=<pw>` to `claude mcp add`. The web app passes it on in its MCP config file, which is written owner-only (0600). After that, `redis-cli` needs `-a <pw>`. Not turned on yet (Aaryan's call; it would break Abdul's setup until he sets the variable). Tested on a throwaway container, Oct 17: no password gives `Authentication required`, a wrong one is refused, the right one lists graphs.
- **Optional: read-only Redis user for the research graphs (D11).** Tested Oct 17 on a throwaway `falkordb/falkordb:v4.22.1` container only, never on the live one:
  ```
  redis-cli ACL SETUSER reader on '>pw' resetkeys '~research:*' resetchannels -@all +graph.ro_query +graph.list +ping +info +hello +client
  ```
  As `reader`: `PING`, `GRAPH.LIST` and `GRAPH.RO_QUERY research:*` work, also from the Python client (`FalkorDB(username="reader", password=...)`). `GRAPH.QUERY` gives `NOPERM ... 'graph.QUERY'`, a write in `RO_QUERY` gives `graph.RO_QUERY is to be executed only on read-only queries`, `RO_QUERY newsroom:*` gives `NOPERM No permissions to access a key`, and `GRAPH.DELETE`, `GET`, `KEYS` and `CONFIG GET` give `NOPERM`. `GRAPH.LIST` still shows the `newsroom:*` names, but not their contents. The MCP server cannot use this user yet, because it writes `newsroom:<id>`; the code has no `FALKORDB_USERNAME`. `ACL SETUSER` is lost on restart unless it goes into an ACL file.
- **Idle graphs: free RAM, rebuild for free.** Each research graph takes about 0.7-0.8 GB by `GRAPH.MEMORY`. With Docker Desktop (a VM of about 7.75 GiB), 6-8 fit. With native Docker on Linux (Aaryan's machine), the limit is the host's free RAM (about 4 GiB free on Oct 17), not `total_system_memory`.
  - List the graphs and their sizes: `docker exec falkordb redis-cli GRAPH.LIST`, then `docker exec falkordb redis-cli GRAPH.MEMORY USAGE research:<slug>` (first two lines: `total_graph_sz_mb`) and `docker exec falkordb redis-cli INFO memory | grep used_memory_human`. On Aaryan's machine add `--context default` after `docker`.
  - Free an idle case graph: `docker exec falkordb redis-cli GRAPH.DELETE research:<slug>`. The raw cache in `cases/<slug>/raw/` (obesity-coke: `data/raw/`) stays, so `uv run python loader.py --case <slug> --rebuild` brings it back with 0 API calls. A server on that case gets `NO_GRAPH` until then.
  - **Never delete `newsroom:*`.** That is the newsroom's memory (cases, verdicts, notes); it is not rebuildable from anything.
  - Do not delete the graph of the default case or of a case the web app or a demo is using.
- **CI:** `.github/workflows/ci.yml` runs the suites that need no research graph and no key: `test_hooks`, `test_e5_precheck`, `test_discover` (recorded fixtures), plus a compile check. The other suites need the gitignored research graphs (checked on an empty FalkorDB, Oct 17) and run locally only.
- **Tests:** `uv run python tests/<suite>.py` for `test_loader`, `test_research`, `test_server`, `test_eval`, `test_cases`, `test_discover`, `test_memory`, `test_obsidian` (plain asserts, no pytest). Suites use `zz-test-*` newsrooms, never `alpha`.
- **Web app:** `uv run uvicorn web.app:app --host 127.0.0.1 --port 8000` (needs a logged-in `claude`; env `TIETRACE_NEWSROOM`, default `alpha`, and `FALKORDB_PORT`). Tests: `uv run python tests/test_web.py` (`WEB_LIVE=1` adds a real `claude -p` run).
- **See the memory graph in Obsidian:** `uv run python obsidian_export.py --watch 5` writes linked notes (cases, experts, papers, funders) to `~/Documents/Obsidian Vault/TieTrace/<newsroom>/`; open Graph view (Ctrl+G). Read-only on FalkorDB; writes only in that folder, and moves stale notes to the vault's `.trash/`. The full research graph is better viewed in the FalkorDB Browser.
- **Register the server:** `claude mcp add tietrace-alpha -e TIETRACE_NEWSROOM=alpha -e FALKORDB_PORT=6380 -- uv run --directory <path-to-repo> python server.py` (`uv run --directory` finds the venv from any folder; drop `FALKORDB_PORT` if FalkorDB is on 6379). The server exits at once if `TIETRACE_NEWSROOM` is unset.
