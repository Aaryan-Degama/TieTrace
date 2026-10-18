# One Database, Many Graphs

### Running read-only research graphs and private newsroom memory side by side in FalkorDB

*By Aaryan Degama, Abdul, Kavyan and Jalendu, TieTrace team, for FalkorDB Graph Hacks (Tracks 1 and 2). Oct 18, 2026. Every number here comes from our test suites, evaluation scripts or FalkorDB's own counters; the source is named where it matters.*

---

TieTrace answers one question for journalists: *what ties does the research record show between this expert and this company?* Our [first post](https://github.com/Aaryan-Degama/TieTrace/blob/main/BLOG1.md) covered what the tool finds and how we evaluated it. This one is about the database underneath.

The short version: one FalkorDB server holds several large research graphs, one per investigation, and a small private memory graph for every newsroom. The research graphs are rebuilt, swapped and deleted freely. The memory graphs are never rebuilt and never deleted. An AI agent reads both, writes to one, and cannot touch the wrong one. Getting that right taught us more about FalkorDB than any single query did.

## The shape of the system

```text
Journalist
    |
    v
Agent + MCP client  (Claude Code or the web app)
    |
    |  13 tools, stdio
    v
server.py  (no LLM calls)
    |                                   |
    |  GRAPH.RO_QUERY (read-only)       |  fixed, parameterised writes
    v                                   v
research:<case>                     newsroom:<id>
one per investigation               one per newsroom (memory)
    ^
    |  builds staging:<case>, then RENAME
loader.py
```

The server is a Python MCP server with 13 tools and no model calls. The agent (Claude Code, or our web app with the visitor's own Claude, Gemini or OpenAI key) decides which tool to call; Cypher does all the work on the data. Every tool returns the same envelope: `ok`, `data`, the exact `cypher` it ran with its parameters, the `scope` of the search, any `verdicts_applied`, and `warnings`. Any answer can be replayed by pasting its Cypher into the FalkorDB Browser.

## Why many small graphs instead of one big one

We could have put every investigation and every newsroom into one graph and separated them with labels or properties. We chose named graphs for four reasons, and each turned out to matter.

1. **Different lifecycles.** A research graph is a derived artefact: it can be thrown away and rebuilt from the download cache. A newsroom graph is a record of human decisions (verdicts, approved notes) and cannot be rebuilt from anything. Keeping them apart means a rebuild can never damage memory.
2. **Different trust levels.** The agent may only read research data, but it must write memory. With separate graphs that rule is a property of the *call* (`GRAPH.RO_QUERY` versus a fixed write), not a `WHERE` clause someone could forget.
3. **Separate rebuilds and deletes.** Adding a Nestlé investigation did not touch the Coca-Cola graph. An idle case can be dropped with one `GRAPH.DELETE` to free RAM.
4. **Isolation by name.** A newsroom's memory lives at `newsroom:<id>`, and the server picks `<id>` from its environment. No query can wander into another newsroom by accident, because no query ever names it.

## Inside a research graph

Each research graph is built from [OpenAlex](https://openalex.org) (CC0) for one company and one topic: every work funded by the company or its foundation (on any topic, so the direct evidence is complete), plus the most-cited works on the topic for each year.

| Node | What it is |
|---|---|
| `Person` | An OpenAlex author, with `name_norm` for name lookup |
| `Work` | A paper: DOI, year, number of authors, title |
| `Funder` | A funder; `target: true` if it belongs to the company family |
| `Org` | An institution; `target: true` if it belongs to the company family |
| `Topic` | The investigation's OpenAlex topic(s) |

| Edge | What it carries |
|---|---|
| `AUTHORED` | Person → Work |
| `AFFILIATED` | Person → Org, with the work it was listed on |
| `FUNDED_BY` | Work → Funder |
| `PARENT_OF` | Org → Org, the corporate family, with `rel` such as `"bottler"` |
| `ABOUT` | Work → Topic |
| `COAUTH` | Person → Person, derived at load, stored in both directions |
| `COAUTH_B` | The subset of `COAUTH` strong enough for our Tier B rule |

**We precompute almost everything at load time.** FalkorDB has no temporal arithmetic, so "papers since 2021" or "small papers in the last five years" are counted by the loader and stored as edge properties. Co-authorship weight uses fractional counting: each shared paper adds `1/(n_authors - 1)`, so a 40-author consortium paper adds almost nothing while a two-author paper adds 1. Company-family membership is also resolved at load: the ownership chain is walked once with `[:PARENT_OF*0..3]`, a human approves the list, and matching orgs and funders get `target: true`. At query time, "is this affiliation inside the Coca-Cola family?" is a single property check, `(o:Org {target: true})`.

**`COAUTH_B` exists because of one number.** Unfiltered co-author paths reach 56.9% of all people within two hops, which would make "close to a funded researcher" meaningless. So the multi-hop search walks only `COAUTH_B`, a pre-filtered edge type, rather than filtering `COAUTH` at query time (which `algo.BFS` cannot do anyway).

**Indexes.** Every node type is indexed on its OpenAlex id, plus `Person.name_norm`, `Work.doi`, and a full-text index on `Work.title`.

Three research graphs are loaded today:

| | Coca-Cola × obesity | Philip Morris × lung cancer | Nestlé × infant formula |
|---|---|---|---|
| Works | 31,291 | 21,756 | 28,044 |
| People | 85,511 | 68,723 | 73,961 |
| Co-author edges (both directions) | 1,326,194 | 1,458,502 | 3,915,672 |
| `COAUTH_B` edges | 50,436 | 66,172 | 48,538 |
| Size reported by `GRAPH.MEMORY USAGE` | 784 MB | 726 MB | 1,785 MB |

*Counts from each graph's `Meta` node, written at build time. The Nestlé graph was built on Oct 18. Its co-author edge count is almost three times the others' with fewer works, which is why it is also more than twice the size.*

Each graph also carries one `Meta` node: when it was built, the OpenAlex data version, a hash of the case configuration, the counts above, and the share of topic works that list any funder. Every answer quotes that share, so a reader can see how much "no tie found" is worth for that graph.

## Inside a newsroom graph

The memory graph is small and append-only in spirit.

```text
Session     --HAS_EVENT-->                Event   (one per tool call)
Case        --HAS_CHECK-->                Check   (tier, score, DOIs, scope)
newer Check --SUPERSEDES {diff_json}-->   older Check
Verdict     --AFFECTS {before -> after}-> Check   (human only)
Case        --HAS_NOTE-->                 Note    (approved by a human)
```

- A **`Session`** per server process and an **`Event`** per tool call (tool name, ok or error code, duration). Writing an event takes about 0.3 ms.
- A **`Case`** per story and a **`Check`** per expert checked, with the tier, the evidence DOIs and the no-tie scope frozen at that moment.
- When a check is re-run, the new `Check` points at the old one with **`SUPERSEDES`**, carrying a JSON diff of what changed. Nothing is overwritten, so "what did we know on Tuesday?" always has an answer.
- **`Verdict`** nodes (rule out a paper, merge two author profiles) and approved **`Note`** nodes are written only after a human clicks or approves; the model cannot call those tools without a permission prompt.

"The current check" is simply one nobody supersedes:

```cypher
MATCH (c:Case)-[:HAS_CHECK]->(k:Check)
WHERE k.expert_id IN $ids AND NOT ()-[:SUPERSEDES]->(k)
RETURN ...
```

That is the query behind cross-session memory: open a new story with an expert checked last week, and `open_case` shows the earlier check, who made it and which tier it found.

## Two graphs, one answer

The most useful trick in the design is how memory changes research answers *without writing to the research graph*.

When a reporter rules a paper out (for example, a record that is not really a study), that `Verdict` is stored in the newsroom graph. On the next query, the server reads the excluded work ids from memory and passes them as a parameter into the research query:

```cypher
MATCH (p:Person) WHERE p.oa_id IN $ids
MATCH (p)-[a:AFFILIATED]->(o:Org {target:true})
WITH a.work AS wid, o
MATCH (w:Work) WHERE w.oa_id = wid AND NOT w.oa_id IN $x_works
...
```

The research graph stays byte-for-byte what the loader built, one newsroom's judgement never leaks into another's results, and no graph copy is needed. After a verdict, every current check in that newsroom is re-run, and the ones that changed are listed with their tier and score before and after.

One honest gap: `algo.BFS` takes no edge filter, so the multi-hop search can still walk a co-author link that rests on an excluded paper. When that might have happened, the answer says so in its warnings.

## Isolation in three layers

Putting every newsroom in one database is only safe if one newsroom can never see another's memory. We enforce that three times.

1. **The model never names a graph.** The newsroom id comes from the `TIETRACE_NEWSROOM` environment variable when the server starts, never from a tool argument. Our isolation test called all 13 tools with another newsroom's ids: every call returned zero rows or `NOT_FOUND`.
2. **The engine refuses writes.** Every query the model can trigger goes through `GRAPH.RO_QUERY` with a timeout and a row cap. A `CREATE` on that path fails inside FalkorDB, so safety does not depend on our prompt. Writes exist only as fixed, parameterised Cypher in the server; nothing is built from user text.
3. **A Redis ACL user can be limited to `research:*`.** We tested this on a throwaway container: a `reader` user with `~research:*` and only read-only graph commands can query research graphs, while `GRAPH.QUERY`, `GRAPH.DELETE` and any query on `newsroom:*` return `NOPERM`. One surprise: `GRAPH.LIST` still shows the `newsroom:*` *names* to that user, just not their contents. The live server does not use this user yet, because it writes newsroom memory.

## Swapping a graph while people read it

A research graph is rebuilt whenever its case changes. We never want a reporter to see a half-built graph, so the loader builds into `staging:<case>`, runs a self-check (people, works, topics and authorship edges are present, and there is exactly one complete `Meta` node), and only then runs Redis `RENAME` over the live name. A failed build leaves the old graph untouched. From the download cache, the Coca-Cola graph rebuilds in about 60 seconds with zero API calls.

What happens to queries already running during the swap was not documented, so we tested it (`tests/test_rename_swap.py`). On FalkorDB 4.22.1, `RENAME` returned in about 0.5 ms while reads were still running; those reads finished on the old graph, and the next read saw the new one. No reader saw a mix.

Two smaller pieces make rebuilds safe in practice:

- **One build per case.** Two builds of the same case would share `staging:<case>`. The loader takes a Redis key `build-lock:<case>` with `SET NX EX` (expiring after four hours if a build crashes) and releases it with a short Lua script that deletes the key only if it still holds its own token, so a slow build can never delete a newer build's lock.
- **The server notices.** Just before the swap the loader records the new build in the case's status file, and a running server drops its cached facts for that case on its next call.

The same machinery gave us a time machine for the demo: `--until-year` builds a second graph from the same cache with only the works published up to a given year. Checking an expert on the "old" graph and then re-running the check on the full one lets `recheck` show what changed and why.

## Budgeting memory for many graphs

FalkorDB keeps every graph in RAM, so a third or fourth case is a memory question before it is anything else. Three things surprised us.

- **`GRAPH.MEMORY USAGE` overstates.** The first two graphs reported 784 + 727 MB, but Redis `used_memory` for both together was 1.19 GiB. We use the reported size as a safe upper estimate.
- **`total_system_memory` is not your budget.** With `maxmemory` unset, Redis reports the whole machine: 15.01 GiB on one of our laptops, which had about 3.98 GiB actually free. So before a build the loader checks two things: used memory plus the new graph's estimated size against 90% of the limit, and the new graph against Linux `MemAvailable`. The real guard is setting `maxmemory`, after which Redis refuses writes past it.
- **`GRAPH.COPY` copies in RAM.** It works on 4.22.1 and keeps indexes, but copying a research graph needs its full size free again. So we would only ever copy a small newsroom graph that way.

The staging build makes this sharper: during a rebuild the old graph and the new one exist at the same time, so the check has to leave room for both.

## Persistence and backups, by graph type

A Docker shutdown once interrupted FalkorDB's final snapshot, and it came back with an older one. We turned on the append-only file (`appendonly yes`, `appendfsync everysec`), which bounds a crash to about one second of writes. On one restart, the container log showed "DB loaded from append only file: 4.802 seconds". Before closing Docker we now run `redis-cli SAVE`, then stop with a 120-second timeout.

Backups follow the two lifecycles:

| | Research graphs | Newsroom graphs |
|---|---|---|
| Can be rebuilt? | Yes, from the raw download cache, 0 API calls | No |
| Real backup | The cache files (zipped, about 50 MB for two cases) | A copy of `dump.rdb` and the AOF directory after `SAVE` |
| Safe to delete when idle? | Yes | Never |

## The queries doing the real work

**"No tie found" with `algo.BFS`.** The negative answer is the hard one. We search outward from the expert over `COAUTH_B` edges, one hop at a time up to three, and stop at the first distance where anyone with direct ties appears:

```cypher
MATCH (e:Person) WHERE e.oa_id IN $ids
CALL algo.BFS(e, $hops, 'COAUTH_B') YIELD nodes
UNWIND nodes AS n
WITH DISTINCT n WHERE n.oa_id IN $tier_a
WITH n ORDER BY n.oa_id
RETURN count(n), head(collect(n.oa_id)), head(collect(n.name))
```

`algo.BFS` returns *no row* when nothing is reached, so the code treats a missing row as a count of zero. If nothing is found, the answer carries its scope: hops, years, and the share of papers that list any funder, for the whole graph and for that expert. Median over eight experts: 9.3 ms.

**Strongest paths with `bridges`.** For context, we find the strongest co-author routes to directly tied people, with the DOIs on every link. This is our slowest query: 1.5 s on a first call and 1.2 s cached on a warm server, but 4.2 s once on a freshly started container.

**Direct evidence.** Tier A is plain one- and two-hop matching: works the person authored that are `FUNDED_BY` a `target` funder, and `AFFILIATED` edges to a `target` org, each returned with its DOI and whether the work is on-topic. A first `tie_report` takes about 0.37 s and a repeated one under 0.01 s.

## Many writers, one memory

A newsroom graph is shared by everyone in that newsroom, so concurrent writes had to be boring. Because every write is a fixed, parameterised query, they were. Four server processes making 50 calls each saved 200 of 200 events. In a second test, two reporter sessions checked experts and recorded verdicts at the same moment (we verified that the calls actually overlapped): nothing was lost, and every current check matched a fresh re-run.

## Four quirks worth knowing

1. **Undirected matches can count inconsistently.** `()-[r]-()` gave us 3 edges where `(a:P)-[r]-(b:P)` gave 6. We store co-authorship in both directions and always match with `->`.
2. **Results cap at 10,000 rows silently.** Count inside Cypher, never by counting returned rows.
3. **`UNWIND` then traverse can skip the index.** `WHERE x.oa_id IN $ids` keeps it, which is why every query above starts that way.
4. **On 4.22.1, `OPTIONAL MATCH ... WITH d WHERE d IS NULL` does not filter.** For "write only if absent", use `WITH count(d) AS n WHERE n = 0`.

## What we would do next

- **Give the server two connections:** the ACL `reader` user for every research query, and a separate user that can write only `newsroom:<id>`. Then the read-only rule would be enforced by Redis permissions as well as by `RO_QUERY`.
- **Set `maxmemory` everywhere,** so the RAM check becomes a hard limit instead of a polite estimate.
- **Filter `algo.BFS` by verdicts,** which today needs a per-newsroom copy of `COAUTH_B` or a different traversal.

## Why one graph database

Every question TieTrace asks is a relationship question: who funded this paper, who wrote it with whom, which company owns which bottler, which expert sits two co-authors from a funded one. In a graph each is a pattern you can read aloud.

The bigger win was keeping everything in one place without mixing it up. Rebuildable research graphs and irreplaceable newsroom memories share one server, separated by name, swapped atomically, and protected by the engine itself. That let us give an AI agent real access to real data without handing it the keys.

---

*Code (MIT): [github.com/Aaryan-Degama/TieTrace](https://github.com/Aaryan-Degama/TieTrace). Install with `./install.sh`; the README lists every step.*
