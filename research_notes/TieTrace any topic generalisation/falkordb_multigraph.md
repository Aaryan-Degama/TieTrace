# Running many per-case research graphs in one FalkorDB, and refactoring TieTrace from one hardcoded graph to many

Evidence labels: **[V]** verified by a read-only probe or by reading the repo/client source today (2026-10-15, FalkorDB module 4.22.1 on Redis 8.10.2, host port 6380); **[D]** from FalkorDB docs/pages (URL given); **[I]** my inference. Scratch graphs used for probes (`scratch:a/b/c`, `scratch:probe1`, `scratch:bad name; x`) were all deleted; `GRAPH.LIST` is back to only `research:obesity-coke`. Nothing existing was touched. Note: several fetched doc pages contained embedded "Agent Instructions" text addressed to AI; it was ignored and nothing was acted on.

## 1. FalkorDB facts: graphs per instance, memory, commands, persistence, config

### Takeaway
One instance can hold many graphs (each graph is one Redis key; a query touches one graph), but **memory is the limit, not the graph count**: the current graph is ~780 MB by `GRAPH.MEMORY` and Docker Desktop only gives this machine 8.3 GB, so about 6-8 graphs of this size, not 10, unless COAUTH is slimmed. `GRAPH.COPY`, `GRAPH.DELETE`, and plain Redis `RENAME` (atomic, can replace an existing key) all work in this version, so a "build staging, then swap" pattern is available. The "graph vanished after restart" symptom is explained by an incomplete final RDB save plus a stale `dump.rdb`.

### Cited Findings
- **Graph count.** Vendor claims "over 10,000 isolated graphs per deployment" (untested); the only measured figure is 629 graphs on one 16-core instance in a workshop. The same post says "Adding more graphs increases memory usage and snapshot duration" and gives no per-graph footprint. Cross-graph queries are not supported in one execution. — [FalkorDB multigraph post](https://www.falkordb.com/news-updates/multigraph-topology-isolation-linear-scale/)
- Open issue: with many sparse/attribute-less tenant graphs, Redis `used_memory` can read far above physical RSS (example ~12 GiB vs ~6 GiB); not confirmed merged. Irrelevant at our scale (few, large graphs). — [FalkorDB #2499](https://github.com/FalkorDB/FalkorDB/issues/2499)
- **Memory of our graph [V].** `GRAPH.MEMORY USAGE research:obesity-coke` -> `total_graph_sz_mb: 780`; COAUTH attributes 584 MB, COAUTH_B 45, relation matrices 55, indices 29, AFFILIATED 19, edge blocks 16, Person attrs 13, Work attrs 6. So COAUTH (1.33M edges, stored both directions, with list properties `dois`/`works`/`years`) is ~75% of the graph. Whole server `used_memory` = 591 MB, RSS 631 MB (the GRAPH.MEMORY figure is a sampled estimate and is higher than real used_memory; treat 600-780 MB as the range). Doc: command returns per-label/type breakdown, `SAMPLES` default 100, no per-node/edge byte figures given. — [GRAPH.MEMORY](https://docs.falkordb.com/commands/graph.memory.html)
- **Docker memory [V].** `docker info`: Docker Desktop reports `MemTotal` = 8,328,429,568 bytes (~7.75 GiB) with Windows 11 host. `maxmemory` = 0 and policy `noeviction`, so FalkorDB will just grow until the WSL2 VM is OOM-killed. (Host RAM 16 GB is from the task brief; the container limit is the WSL2 VM, not the laptop.)
- **Disk [V].** `dump.rdb` = 63 MB for that graph (RDB is compact); `data/raw/` cache = 294 MB. RDB load on start took 3.0-6.1 s for 22 keys (docker logs).
- **Commands [V]** (module 4.22.1):
  - `GRAPH.LIST` -> list of graph keys.
  - `GRAPH.RO_QUERY missing:key "RETURN 1"` -> `Invalid graph operation on empty key` (RO never creates a key). `GRAPH.QUERY` on a missing key **creates** it (so a typo in a write path silently makes an empty graph; keep writes in the loader only).
  - `GRAPH.COPY src dest`: 1000-node graph copied in 0.19 s with its index; copying onto an existing dest fails with `destination key already exists`. Doc: source stays readable/writable during copy, dest is a consistent snapshot, indexes and constraints are copied. Config `TEMP_FOLDER` (default `/tmp`) holds dump files used by GRAPH.COPY. — [GRAPH.COPY](https://docs.falkordb.com/commands/graph.copy.html), [configuration](https://docs.falkordb.com/getting-started/configuration.html)
  - Plain Redis `RENAME scratch:b scratch:c` works on a graph key, queries and `db.indexes()` still work afterwards (index `OPERATIONAL`, 1000 docs). `RENAME scratch:c scratch:a` **onto an existing graph key replaced it** (old `scratch:a` gone from `GRAPH.LIST`, count query on `scratch:a` returned the renamed data). So an atomic swap = build `staging:<slug>`, verify, `RENAME staging:<slug> research:<slug>`. **Not documented by FalkorDB** as a supported path (I found no doc page for it); it works today, so pin a test for it. In a Redis Cluster both keys must hash to the same slot (irrelevant for single-node Docker).
  - `GRAPH.DELETE name` removes the key. In the client `Graph.delete()` is the same call (`falkordb/graph.py:163`).
- **Index and full-text behaviour.** Indexes are per graph and are copied by COPY [D] and survive RENAME [V]. `CREATE FULLTEXT INDEX` (loader.py:460) is also per graph. `DELAY_INDEXING` (default `no`) defers index build until after RDB decode [D]. Loader creates indexes **before** edges so the edge `MATCH`es use them (loader.py:471). Each index adds memory (`indices_sz_mb` 29 MB here).
- **Result cap [V][D].** `GRAPH.CONFIG GET RESULTSET_SIZE` -> 10000. Configurable at load time and run time; the image's default args are `MAX_QUEUED_QUERIES 25 TIMEOUT 1000 RESULTSET_SIZE 10000` (module list `args` and container env `FALKORDB_ARGS`). Run-time `GRAPH.CONFIG SET` does not survive a restart. Other live values: `TIMEOUT` 1000, `TIMEOUT_MAX` 0, `TIMEOUT_DEFAULT` 0, `CACHE_SIZE` 25, `THREAD_COUNT` 4, `QUERY_MEM_CAPACITY` 0 (unlimited), `VKEY_MAX_ENTITY_COUNT` 100000. `TIMEOUT` is deprecated since v2.10 in favour of `TIMEOUT_MAX`/`TIMEOUT_DEFAULT`; `QUERY_MEM_CAPACITY` can cap one query's memory. — [configuration](https://docs.falkordb.com/getting-started/configuration.html). TieTrace keeps SERVER_CAP=10000 in research.py:23 as "reaching it is an error"; if RESULTSET_SIZE ever differs between machines that constant is wrong. Cheap fix: read it once with `GRAPH.CONFIG GET RESULTSET_SIZE` (the client has `db.config_get`, `falkordb/falkordb.py:214`).
- **Persistence settings now [V].** `save = "3600 1 300 100 60 10000"`, `appendonly = no`, `appendfsync = everysec`, `dir = /var/lib/falkordb/data`, `dbfilename = dump.rdb`. Container: named volume `falkordb-data` mounted at `/var/lib/falkordb/data` (correct path), restart policy `no`, cmd `redis-server --loadmodule .../falkordb.so`.
- **Why a graph vanished [V from logs and files, cause partly I].** `docker logs falkordb`: on 17:42:52 "Received SIGTERM ... Saving the final RDB snapshot before exiting", then at 17:42:54 "Loading RDB ... RDB age 10068 seconds" (= the 14:55 file; same at the 17:33:59 start, "RDB age 9533 seconds"). `ls` of the data dir: `dump.rdb` dated **14:55** and a partial `temp-1.rdb` (31 MB, 17:42). So the final save at shutdown did not finish (temp file left behind, `dump.rdb` not replaced) and the next start loaded the old snapshot; anything written after 14:55 was lost. The research graph itself was built at 14:49:55 UTC (Meta.built_at), before 14:55, so it survived. Likely mechanism **[I]**: shutdown of Docker Desktop/WSL2 (or `docker stop` default 10 s grace) killed Redis mid-save; the final RDB of a ~600 MB dataset took longer than the window. Also with the default save points (`3600 1`, `300 100`, `60 10000`) a build that ends with few changes after the last snapshot can sit unsaved for up to an hour. A big loader run does trigger `60 10000`/`300 100` snapshots mid-build, so the on-disk snapshot may predate the final Meta write.
- **Docs on persistence.** Recommended combination: `save 900 1`, `save 300 10`, `save 60 1000`, `appendonly yes`, `appendfsync everysec`; with both enabled FalkorDB restarts from the AOF; RDB loads faster, AOF gives the better recovery point; snapshots use `fork()` and need disk I/O; `redis-cli DUMP <graph>` exports one graph. — [Data durability](https://docs.falkordb.com/operations/durability/). Named volume at `/var/lib/falkordb/data` is the documented layout. — [Docker persistence](https://docs.falkordb.com/operations/durability/persistence). A user-reported cause of vanishing data elsewhere was mounting at `/data` (wrong path), which is **not** our case. — [FalkorDB-MCPServer PR #174](https://github.com/FalkorDB/FalkorDB-MCPServer/pull/174)
- **Bulk load speed.** I did not find a vendor benchmark. Repo: the loader writes in batches of 5000 rows via `UNWIND` (loader.py:68, 476-477) and prints wall-clock at the end, but no figure is recorded in AI_USE.md/checkpoints.md. Not measured by me (would need a rebuild of a real graph).

### Recommended Docker settings [I, based on the above]
```
docker run -d --name falkordb -p 6380:6379 -p 3001:3000 -v falkordb-data:/var/lib/falkordb/data \
  -e REDIS_ARGS="--appendonly yes --appendfsync everysec --save '300 1 60 1000'" \
  --stop-timeout 120 falkordb/falkordb:latest
```
- AOF on removes the "lost since last snapshot" window; `--stop-timeout 120` gives the final save time (default 10 s). I did not verify that `REDIS_ARGS` is honoured by this image's entrypoint (the docs I fetched mention `FALKORDB_ARGS`/`REDIS_ARGS` nowhere; the container env shows `FALKORDB_ARGS`). Safer to test: `docker exec falkordb redis-cli CONFIG GET appendonly` after start, or use `redis-cli CONFIG SET appendonly yes` + `CONFIG REWRITE` is not possible without a conf file, so re-apply at start.
- Before shutting down Docker Desktop: `redis-cli SAVE` (blocking) or `BGSAVE` and wait for `rdb_bgsave_in_progress:0`, then `docker stop falkordb`. Use `INFO persistence` fields `rdb_last_save_time`, `rdb_last_bgsave_status`.
- Treat the graph as rebuildable from `data/raw/` (CLAUDE.md already says rebuild has zero API calls), so persistence loss costs minutes, not data.

### Inferences
- RAM, not count, caps cases: at ~0.6-0.8 GB per graph and ~7.75 GiB for Docker, 10 graphs of today's size will not fit with headroom (see section 5).
- `RENAME`-over-existing is the cheapest atomic swap and keeps indexes; `GRAPH.COPY` is better as a snapshot/backup before a rebuild (it fails safely on an existing dest).

### Gaps
- No official statement that `RENAME` of a graph key is supported/atomic wrt in-flight queries (works in 4.22.1; test only used a 1000-node graph).
- No measured load time for the full graph; no per-graph overhead for tiny graphs.
- Whether `REDIS_ARGS` is honoured by the image: not checked (would require restarting the shared container).
- Exact cause of the interrupted shutdown (Docker Desktop vs Windows vs `docker stop` timeout) not determined.

## 2. Naming, mapping case to graph, and a registry

### Takeaway
Use `research:<slug>` with a strict regex, and keep status in a small file (or one registry graph), not in the graph being built. A tool resolves a case to a graph by dictionary lookup, never by passing the user's string, and says "no graph for this case yet" when the slug is unknown or not `ready`.

### Cited Findings
- **Names are literal keys [V].** The Python client does `Graph(self, graph_id)` after only a non-empty-string check (`falkordb/falkordb.py:185-200`) and sends it as one argument of `execute_command` (`falkordb/graph.py:149-161`, constants at lines 14-20); the wire format (RESP arrays of bulk strings) has no quoting step. I confirmed: `GRAPH.QUERY "scratch:bad name; x" "RETURN 1"` created a graph literally named `scratch:bad name; x` and nothing else happened. So there is no command injection through the name; the risks are different: (a) a model could name **another existing key**, e.g. `newsroom:alpha` or `research:other-case` (tenancy/E10), (b) `GRAPH.QUERY` on a typo creates a junk graph, (c) a non-graph key gives a Redis type error.
- Current choke point: every read goes through `research.ro_query(graph, ...)` (research.py:57-80) using `db.select_graph(graph).ro_query(...)`; the `graph` arg is a module constant today. Spec says reads use `GRAPH.RO_QUERY` only and the newsroom comes from env, never a tool argument (CLAUDE.md; server.py:24-26, regex `[a-z0-9_-]+`).

### Proposed scheme [I]
- Name: `research:<slug>`; `SLUG_RE = ^[a-z0-9][a-z0-9-]{1,39}$`. Existing graph keeps its name with slug `obesity-coke`. Staging: `staging:<slug>`. Newsroom memory unchanged: `newsroom:<id>`. A hash suffix is unnecessary if slugs are unique in the registry; put the **config hash** in Meta and the registry instead of the name (name stays stable so tests/tools/cites don't change on rebuild).
- Registry: **a folder per case**, `cases/<slug>/case.json` (config, human-edited) and `cases/<slug>/status.json` (written by the loader: `status` building|ready|failed, `built_at`, `data_version`, `config_hash`, `counts`, `error`). Reasons: status must live outside the graph being built; the loader already writes a Meta node with `built_at`, `data_version`, `counts`, `load_scope` (loader.py:509-527), so Meta stays the in-graph truth and `status.json` adds only building/failed. Alternative `registry` graph is possible but adds a write path and another graph to protect; not needed for ~10 cases. `data/raw/` moves to `cases/<slug>/raw/` (the loader's `RAW` constant, loader.py:26) so deleting a case is one folder + one graph.
- Resolve: `slug -> status.json ready AND slug in GRAPH.LIST` -> graph name built from the dict, e.g. `{slug: "research:"+slug for slug in registry}`; unknown -> envelope `ok:false, error.code="NO_GRAPH"`, message "no research graph for case 'x' yet (known: ...)". This is the same shape as existing `ToolError` (research.py:37-40). Error code must be added to spec section 4 in the same change (CLAUDE.md rule).

### Gaps
- No FalkorDB doc states a max key length or forbidden characters for graph names; Redis keys are binary-safe up to 512 MB, so the regex is our own rule, not a FalkorDB one.

## 3. What is hardcoded today and the refactor plan

### Takeaway
Only about 20 `GRAPH` references in research.py and a short list of constants in loader.py are Coca-Cola/obesity specific; research.py's query logic is already generic because tier thresholds and years come from the graph's Meta node (research.py:134-138, 166). The smallest safe refactor is: per-call "current case" context in research.py (default = Coca-Cola), a `CaseConfig` loaded from JSON in loader.py (default = today's constants), and case-keyed caches. Estimate ~250-350 changed/added lines, medium risk, mostly mechanical.

### Cited Findings (file:line evidence, all [V])
**research.py (608 lines)**
- `GRAPH = "research:obesity-coke"` at :19; `GRAPH` appears 20 times (grep count). Query sites: :136 (Meta), :168 and :174 (Tier-A funded/affiliation), :190 (staff), :198 and :201 (person lookup/error text), :207 (topics), :241/:245 (name queries), :284 (last orgs), :327 (find_experts), :373 and :384 (evidence), :405 (nearest Tier A), :433 (ties), :581/:584 (bridges).
- Process-level caches: `_db` (:33, one connection, fine to share across graphs), `_cache = {}` (:34) filled by `_cached(key, ...)` (:117-125) with keys `"meta"` (:138), `"tier_a"` (:184), `"staff"` (:193), plus coverage derived from Meta. These are keyed only by name, so with two graphs they would return the first graph's data. `clear_cache()` (:128) exists.
- Already per-graph: `small_paper` from Meta (:166), `recent_year`/`last_year` from Meta (:137, :142), caveat counts from Meta (:157-160). `TIER_WEIGHT` (:21), `QUERY_NAME_Q3` (:20), `BIG`, `SERVER_CAP` are global policy; fine.
- `PARENT_OF.rel = 'bottler'` and `Funder.kind = 'company'` are used as generic labels in queries (:171, :178) -> company-agnostic as long as config supplies those labels.
- Nothing in research.py/verifier.py/server.py text mentions Coca-Cola by name except the module docstring, `server.py:91` ("loaded obesity research graph") and `GEBN_PUBLIC_IDS` (`server.py:44`, the public-case allowlist; must become per-case config, e.g. `public_ids`, which also feeds the `public_case` check at :159).

**loader.py (538 lines)**
- Constants: `GRAPH` :25, `RAW` :26, `COMPANY_FUNDER`/`FOUNDATION_FUNDER`/`FUNDER_KIND` :30-32, `TOPICS` :35-38, `TOPIC_YEARS` :39, `TOPIC_CAP`/`PER_YEAR` :42-43, `COKE_PARENT`/`COKE_FAMILY`/`COKE_CHILD_REL` :48-55, `SMALL_PAPER` :57, `RECENT_YEAR` :58, `LIST_TRUNCATION` :59 (OpenAlex limit, global), `WORKS_COUNT_ANOMALY` :61, junk rules :65-67 (global, shared with research.py:28), `BATCH` :68.
- Used inside: `load_works` :261-268 (filters built from funder IDs, family IDs, topic IDs, years), `build_rows` :325 (`target: oid in COKE_FAMILY`), :331 (`target: fid in FUNDER_KIND`, `kind`), :334, :337-338, :352, :354-355 (topics, family orgs, PARENT_OF rows), `coauth_rows` :402 (`SMALL_PAPER`, `RECENT_YEAR`), `main` :507 and :509-527 (Meta), `write_graph` :464-485 (`GRAPH`, drop/rebuild at :466-469, Meta node written last at :484).
- Write path is already fixed, parameterised `QUERIES` (:425-450) and `INDEXES` (:452-461); graph name is the only thing to parameterise. These need no change per case.
- Cache: `read_cache(name, params, refresh)` (:151) keys raw files by name under `RAW` and ignores header differences (comment :150: "changing TOPICS, caps or filters refetches", i.e. file header compared). Per-case `RAW` folder avoids two cases clobbering `funded.jsonl`/`family.jsonl`/`topic_<year>.jsonl`. **Risk:** today's cache files are shared names; two cases with different filters in one folder would force refetch each time.

**server.py (223 lines):** tools call `research.*` directly (:92, :100, :111, :121, :130, :181, :190); single `_lock` (:54) because research.py has one connection and cache per process; startup warm-up at :214 calls `research._meta/_tier_a/_staff` for the one graph. `NEWSROOM` env at :24-26 (regex-validated). Refactor: add a case selector (see section 6), warm-up per ready case (cost: Tier-A queries; see below) or lazily on first use.

**eval/ (5 files, 26 `research`/`GRAPH` references):** `eval/e1.py:38-48`, `e2.py:8,53-55`, `e3_e7.py:13,34,46,68,103` call `research._tier_a([])`, `_staff([])`, `_meta([])` and `ro_query(research.GRAPH ...)`; E3 hardcodes the GEBN ids/case. These stay valid with a default-case context; they should accept `--case` later, but E1/E3 are Coca-Cola-specific by design.

**Tests (all assume the Coca-Cola graph):** `tests/test_loader.py:268, 292` use `loader.GRAPH`; `tests/test_research.py:55, 332, 352, 364, 428, 1508, 1520, 1528` use `research.GRAPH` (line 55 builds a module-level `G = FalkorDB(...).select_graph(research.GRAPH)`, so `research.GRAPH` must remain a module attribute with the default name); `tests/test_server.py:135` asserts every logged cypher `c["graph"] == research.GRAPH`; `tests/eval_model.py:8` defines its own `GRAPH`. `tests/test_eval.py` uses the eval modules.

### Proposed refactor [I]
1. **Config schema** (JSON, `cases/<slug>/case.json`; default `cases/obesity-coke/case.json` reproduces today's constants exactly):
```
{ "slug": "obesity-coke", "title": "...", "public_case": false,
  "public_ids": ["A5031318756", ...],
  "company": { "name": "Coca-Cola",
      "funders":  [{"id":"F4320307807","kind":"company"},{"id":"F4320308599","kind":"foundation"}],
      "institutions": {"parent":"I1312732213",
                       "members":[{"id":"I1312732213","name":"Coca Cola (United States)","rel":null},
                                  {"id":"I4405274843","name":"...","rel":"bottler"}]} },
  "topic": { "ids": {"T10010":"Obesity, Physical Activity, Diet", "T10352":"Physical Activity and Health"},
             "search_text": null, "years": [2010, 2026], "cap": 30000 },
  "people": ["names to resolve (optional)"],
  "thresholds": {"small_paper": 25, "recent_year": 2021, "works_count_anomaly": 2000} }
```
   Validate with plain `dict` checks (IDs match `^[FIT]\d+$` / `^I\d+$`, years ints, cap <= a limit). No pydantic.
2. **loader.py:** add `load_config(slug)`; make `GRAPH, RAW, funders, family, topics, years, cap, SMALL_PAPER, RECENT_YEAR` fields of a config object passed into `load_works`, `build_rows`, `coauth_rows`, `write_graph`, `main` (`--case slug`, default `obesity-coke`). Keep module-level defaults (`loader.GRAPH`, `loader.SMALL_PAPER`...) so existing tests still import them. Add to Meta: `slug`, `config_hash` (sha256 of case.json), and write `status.json` at start (`building`), end (`ready`) or on exception (`failed`). Build into `staging:<slug>`, then `RENAME` over `research:<slug>` (replaces `--rebuild` drop-then-build, which today leaves no graph during a failed rebuild: loader.py:466-469 deletes first).
3. **research.py:** replace `GRAPH` use with `_g()` reading a `contextvars.ContextVar` (default `"research:obesity-coke"`); `_run(impl, *args)` (:100) gets an optional `case` and sets the var for the call; `_cached` key becomes `(graph, key)`; `clear_cache(graph=None)`; `ro_query` asserts `re.fullmatch(r"research:[a-z0-9][a-z0-9-]{1,39}", graph)` as a second guard (one place, :57). Keep `GRAPH` as the default constant for tests. Public functions gain `case=None` as last keyword (backward compatible).
4. **server.py:** a `case` argument on tools (validated with `SLUG_RE`, looked up in a registry dict loaded at startup from `cases/*/status.json`, `ready` only), default from env `TIETRACE_CASE` (falls back to `obesity-coke`). `GEBN_PUBLIC_IDS` becomes `config["public_ids"]` of the resolved case. Unknown -> `NO_GRAPH` envelope, `scope` includes the case slug and `data_version` from Meta so every answer names its graph. Newsroom stays env-only.
5. **eval/:** leave on default; add `--case` only to scripts that make sense generically (E7/E2). E1/E3 stay Coca-Cola-only.
6. **Spec:** update `docs/mcp-spec.md` section 3 (graph schema: Meta fields `slug`, `config_hash`, `status`), the envelope/error table (add `NO_GRAPH`), and section 9 (caches now per graph; "restart after rebuild" becomes "cache invalidated when Meta.built_at changes").

**Estimate [I].** loader.py ~120-150 lines touched (config loading/validation ~60, plumbing ~60, staging+RENAME+status ~30); research.py ~40-60 (20 `GRAPH`->`_g()` mechanical, cache key, guard, case param); server.py ~40-60 (case arg, registry load, per-case public ids, warm-up); new `cases/obesity-coke/case.json` ~40; tests +100-150 new lines, ~0 changed existing if defaults hold. Total ~300-450 lines. **Risk: medium.** The riskiest parts are (a) cache keys (a missed one silently serves another case's Tier-A map: a correctness/ethics bug, so test with two graphs), (b) the contextvar default leaking into other threads (server uses a lock and `mcp` tool functions; if tools ever become async, contextvars are safer than a module global), (c) `read_cache` filename collisions, (d) public-case allowlist becoming per-case (ethics: experts named only when the case says so).

**Tests that would break** unless defaults are kept: `test_research.py:55` (needs `research.GRAPH`), `test_server.py:135` (logged graph must equal `research.GRAPH` for default case), `test_loader.py:268, 292` (`loader.GRAPH`, and the Meta `graph` field), any test reading `loader.COKE_FAMILY/TOPICS/SMALL_PAPER` directly. **Migration path:** step 1 add config + defaults, assert `load_config("obesity-coke")` equals the old constants; step 2 thread parameters with defaults and rerun all five test files unchanged; step 3 add the second (tiny) case and the isolation tests (below); step 4 flip server to registry lookup.

### Gaps
- I did not read `tests/test_*.py` bodies beyond grep hits, so other implicit assumptions (counts like 85,511 persons hardcoded in tests) were not enumerated.
- E1/E2 depend on Coca-Cola-specific ground truth; whether they generalise is a design question, not answered here.

## 4. Per-case storage, time and memory for 10 cases on a 16 GB laptop

### Takeaway
At today's size, 10 cases is about 6-8 GB of graph RAM, which exceeds a safe share of Docker Desktop's 7.75 GiB allocation; either raise the WSL2 limit (`.wslconfig`), keep only ~4-5 graphs loaded, or shrink COAUTH. Disk is easy.

### Cited Findings and estimates
- **[V]** per graph now: 780 MB (GRAPH.MEMORY estimate) / ~590 MB real server `used_memory`; RDB 63 MB; raw cache 294 MB; counts Person 85,511, Work 31,291, COAUTH 1,326,194 (Meta.counts).
- **[I]** 10 cases of that size: RAM 6-8 GB, RDB ~0.63 GB (x2 if AOF rewrite and RDB both kept, so ~1.3 GB), raw caches ~3 GB. Disk ~4-5 GB total. Snapshot (fork) time and RDB load time grow linearly: loading one 63 MB RDB took ~3-6 s, so ~30-60 s startup for 10 [I]. `GRAPH.COPY` for backup doubles RAM transiently.
- **[I]** Levers, in order of effect: (1) COAUTH is 75% of memory; it stores `dois`, `years`, `works` list properties on 1.3M directed edges. Dropping list properties from COAUTH (keeping them on COAUTH_B/looked up from AUTHORED) or lowering `author_cap`/topic cap would cut it most; this changes the data model, so it is a design decision, not a drive-by. (2) Lower `topic.cap` (30,000 now) per case. (3) Load only the case being worked on; drop others with `GRAPH.DELETE` and rebuild from `cases/<slug>/raw/` (zero API calls per CLAUDE.md).
- Build time/API cost per case: not measured. Loader uses OpenAlex API (one call per 100 works; cache hit is free). With a cold cache this is the dominant cost; not estimated here for lack of data.
- **Clean-up policy [I]:** keep `cases/<slug>/` (config + status + raw) forever; `GRAPH.DELETE research:<slug>` when idle; `status.json` -> `archived`; a `loader --rebuild` restores it from raw. Keep one `GRAPH.COPY` backup only for the Coca-Cola demo graph until after Oct 18. Run `GRAPH.MEMORY USAGE` after each build and record `total_graph_sz_mb` in `status.json` so memory is budgeted from facts. Set `maxmemory` is **not** a good guard (noeviction would make writes fail with OOM mid-build; no eviction is possible for graphs); prefer a pre-flight check comparing `INFO memory used_memory` plus an estimate against `docker info` MemTotal.

### Gaps
- No measured build time, API call count or Docker memory headroom during a build (a build needs extra RAM for staging while the old graph is live, i.e. up to 2x for a rebuild with RENAME swap).

## 5. Isolation, tenancy (E10), and refusing unknown names

### Takeaway
Names reach FalkorDB only as dictionary values from a registry the server built from disk, so a model-supplied string can select a case but never forms a graph name. Research graphs are read-only shared; only `newsroom:<id>` holds tenant data, so E10 (alpha cannot see beta) stays a property of the memory graph and the env var, not of the research graphs.

### Cited Findings
- **Client does no quoting or shell parsing [V]** (see section 2); the injection risk is key choice, not command syntax.
- `GRAPH.RO_QUERY` rejects writes (returns an error; research.py:75-76 maps it to `READ_ONLY`) [D] [GRAPH.RO_QUERY](https://docs.falkordb.com/commands/graph.ro_query.html). The page does not describe missing-key behaviour; I observed `Invalid graph operation on empty key` [V], which today surfaces as `DB_ERROR` (research.py:77) and should be mapped to `NO_GRAPH`.
- Cross-graph queries are not supported in one execution [D] [FalkorDB multigraph post](https://www.falkordb.com/news-updates/multigraph-topology-isolation-linear-scale/), so a research graph can never read memory-graph data in Cypher; joins (verdicts onto research results) happen in Python, which is the current design (`verdicts_applied`).
- Redis has one keyspace per DB; anyone with a connection can read any graph key. FalkorDB/Redis ACLs can restrict key patterns per user (e.g. a user limited to `research:*` with `+graph.ro_query +graph.list`) — **[I]**, standard Redis ACL feature, not tested here, and ACL users are in-memory unless an `--aclfile` is set ([ACL persistence](https://docs.falkordb.com/operations/durability/acl-persistence)). A read-only ACL user for the MCP server would make "RO_QUERY only" enforceable by the database, not just by code; worthwhile if time allows, otherwise cut.

### Recommended rules [I]
1. One function `graph_for(case)`: `SLUG_RE.fullmatch` -> `registry.get(slug)` -> else `NO_GRAPH`. Tools never accept a graph name, only a slug.
2. Second guard inside `ro_query` (single choke point): graph must match `^research:[a-z0-9-]{2,40}$`. The memory graph code path uses a separate function with `^newsroom:` and the env value, so one cannot be asked to read the other.
3. Reads use only `ro_query`; writes only from loader (`staging:`/`research:`) and the memory module (`newsroom:`), each with fixed parameterised queries.
4. Share research graphs across newsrooms (read-only, contain only OpenAlex public data and no newsroom data). Per-newsroom copies would multiply RAM by newsrooms x cases for no isolation gain. Tenancy: newsroom comes from `TIETRACE_NEWSROOM`; verdict/note rows live in `newsroom:<id>` keyed by (case slug, expert id), so two newsrooms working the same case never share verdicts.
5. Tests to add (E10 style): (a) `tool(case="newsroom:alpha")` and `case="../x"`, `"research:obesity-coke"`, `"RESEARCH:X"` all return `NO_GRAPH`/`INVALID_ARG` and `GRAPH.LIST` is unchanged afterwards (no created junk graph); (b) with two ready tiny graphs, `Tier A` cache for case 1 does not appear in case 2 results; (c) `cypher[].graph` in every envelope equals the resolved graph; (d) with `TIETRACE_NEWSROOM=alpha`, beta's memory graph is unreachable; (e) `RENAME` swap test on a scratch graph (guards the undocumented behaviour); (f) restart persistence test: write, `SAVE`, `docker restart`, read.

### Gaps
- Whether Redis ACL key patterns apply to `GRAPH.*` commands' key argument as expected: not tested.

## Summary of what I would do first (ranked)
1. Fix persistence now (AOF on, `--stop-timeout`, `SAVE` before closing Docker), since the data-loss path is verified and independent of the refactor.
2. Add `case.json` for Coca-Cola reproducing current constants; thread `case` through loader and research with contextvar + keyed caches; keep all five test files green unchanged.
3. Add `status.json`/registry + `NO_GRAPH`; staging build + `RENAME` swap.
4. Second tiny case to prove isolation and cache keys; then budget RAM using `GRAPH.MEMORY USAGE`.
