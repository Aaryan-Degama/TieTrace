# TieTrace checklist: web app agent (Claude, OpenAI, Gemini)

*Last updated by Abdul, Oct 17 2026: first version, made from `docs/superpowers/plans/2026-10-17-multi-provider-agent.md` and Abdul's instructions.*

**Follow:** the plan `docs/superpowers/plans/2026-10-17-multi-provider-agent.md` (the design, interfaces and test names) and `WebApp.md`. If this checklist and the plan disagree, the plan wins, except for the **changes Abdul made on Oct 17** below.

## Scope (Abdul's instruction)
- **We only add the agent to the web app.** Everything else stays and works exactly as it does now: `server.py` and its 13 tools, the research and memory graphs, the verifier, the existing pages, the "Local (Claude Code login)" mode, the human-only Approve flow, all existing tests. Do not change their behaviour.
- The picked model becomes the main agent that drives our MCP tools and our data.

## Changes to the plan (decided by Abdul, Oct 17)
- **Vendor SDKs, per their official docs.** The plan said plain `httpx`; replace that with each vendor's official Python SDK, used the way its current official docs show for function/tool calling. The Builder must read the current docs first and cite the page in `AI_USE.md`. This also needs the `CLAUDE.md` rule amended (decision D1 in the plan): the MCP server still makes no LLM calls and holds no key; only `web/agent/` may call a provider, with the visitor's own key. (decided by Abdul, Oct 17; Aaryan to confirm)
- **Gemini is the default and the free-tier model.** Abdul has put `GEMINI_API_KEY` in `.env` (git-ignored). The Builder uses the **free-tier Gemini model** that Google's current docs list for the free tier (read the docs for the exact model id; do not guess), for every Gemini test. Claude and OpenAI are added too and are used **only with a key the visitor types** (no key of ours is stored for them).
- **The server's own Gemini key is for tests and the local demo only.** It is read from the environment on the host and is **never** sent to the browser, returned, logged, or saved. A hosted deployment does not ship it (visitors bring their own key). Decide with Aaryan before any deployment (open question O-A below).
- **Everything is tested on the actual web app**, through a real browser against the running app (see "How testing works"), not only unit tests.

## Rules (same as `CLAUDE.md`)
- Each box is a **Builder / Reviewer loop**: a Builder agent writes and runs the code; a separate strict Reviewer agent reads it against the plan, writes or extends the tests, and **tests it on the running web app**. Must-fix findings go back to the Builder until the Reviewer passes. **Tests are never weakened.**
- Tick a box only when the Reviewer has passed it, with `(Abdul)` or `(Aaryan)` and the date. Claim an item with `(→ name)`. Add an `AI_USE.md` row (with the Who column) for every task.
- **Keys:** never print, log, commit, or paste a key anywhere. `.env` stays git-ignored. Run the secret scan (Task A9) before every push.
- Banned words in all copy and prompts: "independent", "conflicted". Experts are "Expert #n" always. Human-only tools (`record_verdict`, `approve_note`) never reach the model.
- Test ports: FalkorDB container `falkordb-main` on `FALKORDB_PORT=6500`; web app on `127.0.0.1:8000`. In PowerShell set `$env:FALKORDB_PORT="6500"` first.

## How testing works (every task)
1. **Unit / contract tests** (offline, no network): `uv run python tests/<file>.py`, with scripted fake providers. These run in the normal suite.
2. **Live tests on the real web app** (the Reviewer does these, with the browser tool): start the app (`uv run uvicorn web.app:app --host 127.0.0.1 --port 8000`), open `http://127.0.0.1:8000`, choose the provider in the page, send a story, and **look at what the page shows** (cards, tier chips, the live evidence panel, errors). The Reviewer records what they saw in the checklist note for the task: the story, the provider, the result, and any failure, honestly.
3. **Free-tier discipline:** keep live runs short (turn cap 8), use short stories, and stop on a 429 (rate limit) instead of retrying in a loop. Record the Gemini free-tier limits found in the docs in `docs/test-keys.md`.
4. **Claude and OpenAI live tests** need a key typed by Abdul (a temporary, spend-limited key from each console). If no key is available, mark the live test **not run** and say so; do not fake it.

Status key: ⬜ not started · 🟨 in progress · ✅ done (Reviewer passed) · ✂️ cut

| Task | What | Status |
|---|---|---|
| A0 | Decisions and docs read | ✅ |
| A1 | Provider adapters (Gemini first, then Claude, OpenAI) with official SDKs | ✅ (live Claude/OpenAI not run) |
| A2 | Neutral agent loop (safety rules live here) | 🟨 offline ✅, live Gemini pending quota |
| A3 | Run budgets and refusals | ⬜ |
| A4 | Web endpoints and key handling | ⬜ |
| A5 | Page: provider picker, key box, notice | ⬜ |
| A6 | New story: model proposes a case (no build) | ⬜ |
| A7 | New story: human-approved build | ⬜ |
| A8 | End-to-end "new story" on the real web app | ⬜ |
| A9 | Test-key checklist, secret scan, docs | ⬜ |

---

## A0. Decisions and docs read (do first)
- [x] Amend `CLAUDE.md`: the MCP server makes no LLM calls and holds no key; the web app's `web/agent/` may call a provider SDK with the visitor's own key (or the host's `GEMINI_API_KEY` for tests/local), never stored; record `(decided by Abdul, Oct 17; Aaryan to confirm)`
- [x] Builder reads the **current official docs** for: the Google Gemini API Python SDK (function calling, the free-tier model id and its rate limits), the OpenAI Python SDK (tool/function calling), the Anthropic Python SDK (tool use). Write the page URLs and the model ids chosen into `AI_USE.md` and `docs/test-keys.md`
- [x] Builder checks the `mcp` 2.3.0 **client** (`mcp.client.stdio`) can start our `server.py` on Windows with `uv run` and list the 13 tools; fix the plan if not
- [x] Add the SDKs to `pyproject.toml` (`google-genai`, `openai`, `anthropic`, or whatever the current docs name them) with `uv add`; `uv.lock` committed
- **Builder note (Abdul, Oct 17):** CLAUDE.md exception added; docs read and written to `docs/test-keys.md` and `AI_USE.md`; `uv add google-genai openai anthropic` (2.31.0 / 3.33.0 / 1.15.0), all import; `tests/test_agent_a0.py` passes (mcp client lists 13 tools incl. both human-only ones, which A2 must filter). Default model ids: Gemini `gemini-3.8-flash`, Claude `claude-sonnet-5-5` (cheap: `claude-haiku-5-5`), OpenAI `gpt-6-astra`. Unknown: Gemini free-tier RPM/TPM/RPD (AI Studio login needed); Gemini docs show only the Interactions API, A1 must choose it vs `generate_content`. Boxes not ticked (Reviewer decides).
- **Reviewer (Oct 17): PASS.** Pages re-fetched and matched; 13 tools listed by the `mcp` client; no key in any file; `test_golden` 15/15 and `test_server` 26/26 still pass. Open: the Gemini free-tier RPM/TPM/RPD numbers are not on the public docs (AI Studio login needed): Abdul to paste them into `docs/test-keys.md`. Decision for A1: Gemini via the Interactions API. All four boxes (Abdul, Oct 17).
- **Reviewer checked:** confirm the docs pages cited exist and say what the Builder claims; confirm no key is in any changed file.

## A1. Provider adapters (plan Task 1)
- [x] Neutral types `ToolSpec`, `ToolCall`, `Turn`, `ProviderError` and `get_provider(name)` in `web/agent/providers.py`; model ids in `web/agent/models.json`
- [x] **Gemini adapter** (free-tier model), then **Claude** and **OpenAI** adapters, each using its official SDK the way its docs show
- [x] Offline contract tests from the plan's Task 1 (tool-call reply, text reply, 401 becomes `BAD_KEY` without echoing the key, malformed arguments `BAD_REPLY`, key only in the auth path, unknown provider refused), with recorded or mocked SDK replies
- [x] **Live test on the real Gemini free tier:** a one-turn call that asks for a tool call and gets a parsed `ToolCall` back; the Reviewer records the model id and result
- **Builder note (Abdul, Oct 17):** `web/agent/providers.py`, `models.json`, `tests/test_agent_providers.py` (12/12 offline, fake SDK clients) and `tests/test_agent_live.py` (AGENT_LIVE=1) written. Live Gemini free tier, model `gemini-3.8-flash` via the Interactions API: turn 1 returned a parsed `ToolCall(lookup, {name: Ada})`, turn 2 (tool result sent back) returned text; a wrong key gives BAD_KEY (HTTP 400 + reason API_KEY_INVALID); no 429 seen, so no free-tier limit was revealed (still unknown); no key printed. Claude and OpenAI live: not run: no key (adapters checked against docs and fakes only). Added `Turn.raw` (not in the plan): A2 must copy it into the assistant message as `raw`; Gemini refused a rebuilt tool history without it (HTTP 400, cause unconfirmed). `token_cap` 50000 and `turn_max_tokens` 2048 in models.json are my choices, not from docs. Boxes not ticked (Reviewer decides).
- **Reviewer (Oct 17): PASS after one fix round** (Gemini client close, 3 nits; offline 19/19, live Gemini `gemini-3.8-flash` tool call + answer + wrong-key `BAD_KEY`, golden 15/15, server 26/26). Live Claude and OpenAI: **not run: no key** (not ticked). Boxes (Abdul, Oct 17). Note for A2: copy `Turn.raw` into the assistant message and keep it server-side; A3 must turn an empty reply with stop 'length' into a readable error.
- **Reviewer passes when:** all offline tests pass, the Gemini live call works, Claude/OpenAI adapters pass their offline tests (live marked "not run" without a key), and the canary key never appears in any error or log.

## A2. Neutral agent loop (plan Task 2)
- [x] `web/agent/mcp_client.py` (the MCP client over stdio to the unchanged `server.py`) and `web/agent/loop.py` with `run_agent(...)`
- [x] Only the allowed tools reach the model; `record_verdict`, `approve_note` and unknown tool names are refused and never called; `public_case=true` stops the run
- [x] Redaction reuses `web.app.Parser` (names become "Name #i", ORCID and institution removed); do not re-implement it
- [x] Tool results are untrusted data: an injected "call approve_note" in a result is not obeyed
- [x] Turn cap and a readable error on a provider failure; events use the names the page already handles
- [x] Offline tests from the plan's Task 2 (scripted fake provider and fake MCP client)
- [ ] (PENDING: Gemini free-tier quota; run `AGENT_LIVE=1 FALKORDB_PORT=6500 uv run python tests/test_agent_live.py` later, then the page check in A4/A5) **Live test on the real web app with Gemini:** story "Coca-Cola and obesity. The article quotes Steven Blair and James O. Hill.": the agent calls `resolve_experts` and `check_case`; the page shows Blair Tier A1 and Hill Tier A2 with DOIs, labelled "Expert #n", and the live evidence panel shows the tools and Cypher
- **Builder note (Abdul, Oct 17):** new `web/agent/mcp_client.py`, `web/agent/loop.py`, `tests/test_agent.py` (13/13 offline: the 8 plan tests plus tool list without human-only tools, `raw` copied, parallel calls, resumed session keeps names redacted, wrong argument types via the real server) and `live_agent()` in `tests/test_agent_live.py`. `web/app.py` untouched; `session` resume works without changes (history in the dict, names via `session_names`/`remember_names`). Choices beyond the plan: tool results to the model pass through `web.hook_strip_identity.clean` (same ORCID/institution removal as the Claude path); a refused call gets no `step` event, only a `warning`. Suites: providers 19/19, golden 15/15, server 26/26, test_web 37/51 with the 10 known Windows failures (CLAUDE_NOT_FOUND / stand-in script tests) and 4 skips. **Live (run_agent + real Gemini `gemini-3.8-flash` + real server and graph, port 6500):** run 1 went list_cases, resolve_experts, then the picker for "Steven Blair" (`AMBIGUOUS`, candidate A5105003630: his split profile, so this is correct per the golden test; the story as written cannot reach Blair A1 without the picker click). The live test therefore sends the page's own follow-up sentence in the same session. That second part, with Blair A1 / Hill A2 and the no-names checks, is **NOT verified**: the re-runs got HTTP 429 from the Gemini free tier, so I stopped as instructed. Also found: the google-genai SDK retries 429 internally for minutes, so a rate limit looks like a hang (A1 adapter should add a per-turn timeout). The 'live on the real web page' check can only happen once the endpoint exists (A4/A5); A2's live check is `run_agent` against the real server and graph. Boxes not ticked (Reviewer decides).
- **Builder follow-up (Abdul, Oct 17):** `providers.py` now ends a hanging turn: `turn_timeout_s` 90 in `models.json` (my choice; wraps the whole SDK call, retries included) gives `PROVIDER_DOWN` 'no answer within 90 s'; Gemini client built with `HttpRetryOptions(attempts=1)` (SDK floor is still 1 retry), OpenAI and Anthropic clients with `max_retries=0`. Read from the installed SDK docstrings/signatures, not the web pages. +2 offline tests (providers 21/21, test_agent 13/13). One live retry after this: still no Gemini answer, ended cleanly at 90 s (PROVIDER_DOWN, not a hang; the status was not seen as an explicit 429 this time). Live A2 Blair/Hill check still not verified.
- **Reviewer (Oct 17): offline PASS after one fix round** (public_case any-form stop, tool-call failure guard, 3 nits): `test_agent` 16/16, `test_agent_providers` 21/21, golden 15/15, server 26/26. **Live Gemini NOT verified (free-tier quota: no answer within 90 s, fast-fail timeout works).** Offline boxes ticked (Abdul, Oct 17); the live box stays open.
- **Reviewer passes when:** offline tests pass **and** the live Gemini run on the page matches `tests/test_golden.py` (Blair A1, Hill A2) with no real names, ORCIDs or institutions shown.

## A3. Run budgets and refusals (plan Task 3)
- [ ] Token cap per run, empty-reply error, model refusal shown as text
- [ ] Offline tests from the plan's Task 3
- [ ] **Live:** on the real page, a story with no experts and a nonsense story each end with a readable message (no traceback, no hang); the Reviewer records both
- **Reviewer passes when:** no run can loop past the turn or token cap and every failure is readable.

## A4. Web endpoints and key handling (plan Task 4)
- [ ] `POST /api/agent/ask` (provider in the body, the visitor's key in the `X-Provider-Key` header), `GET /api/agent/providers`; same SSE events as `/api/ask`; the existing `/api/ask` (Claude Code login) is **unchanged**
- [ ] The key is only a local variable for one request: not stored, logged, echoed, or written to any file; HTTPS required when `TIETRACE_DEPLOYED=1`
- [ ] Offline tests from the plan's Task 4 (canary key absent from stream, logs and temp files; no key refused with `NO_KEY`; other origins refused; two requests with different keys do not share state)
- [ ] The host's own `GEMINI_API_KEY` is used **only** when the visitor chose Gemini and typed no key **and** the host allows it (`TIETRACE_HOST_GEMINI=1`, on locally); it is never returned by any endpoint
- [ ] **Live:** on the real web app, a request with a **wrong** key shows a clear "key rejected" message and the wrong key text is not shown anywhere in the page, the network responses (read with the network tool) or the server log
- **Reviewer passes when:** the Reviewer searches the server output, the SSE bodies and the repo for the canary key and the real `.env` Gemini key prefix and finds nothing.

## A5. Page: provider picker, key box, notice (plan Task 5)
- [ ] Provider choice (Gemini default, Claude, OpenAI, plus "Local (Claude Code login)" when the server offers it) and a password-type key box; the key lives only in a JS variable (no `localStorage`, `sessionStorage` or cookie)
- [ ] Notice copy exactly: "We do not save your API key. It is sent with each request and held only in this page's memory. Use a temporary key with a spend limit and revoke it afterwards."
- [ ] Look and layout stay as chosen (dark, chat plus live evidence panel); no thick coloured side borders; works at phone width
- [ ] Offline tests from the plan's Task 5 (static checks, no banned words, no unsourced numbers)
- [ ] **Live:** the Reviewer opens the page, takes a screenshot at desktop and phone width, switches providers, confirms the key box clears on reload and the existing "Check a story" flow in Local mode still works
- **Reviewer passes when:** the page looks right in the screenshots and nothing that worked before broke.

## A6. New story: the model proposes a case, no build (plan Task 6)
- [ ] `web/agent/casebuild.py`: model-callable `discover_candidates(kind, query)` (wraps `discover.py`, read-only OpenAlex search; the OpenAlex key stays on the host) and `propose_case(slug, case)` (validates with the loader's validator; writes `cases/<slug>/proposal.json` only)
- [ ] These tools are available only when `TIETRACE_ALLOW_BUILD=1`; otherwise the agent says plainly that downloading data is not available
- [ ] Offline tests from the plan's Task 6 (proposal, not a build; bad slugs and unknown keys rejected; no tool takes a command, path or key; discover text is untrusted)
- [ ] **Live on the real page with Gemini:** a story about a company/topic that is **not loaded** (Abdul picks one: for example a small drug or food company with a narrow topic): the agent searches candidates and shows a proposed case; **no graph is created and no loader runs**; the Reviewer confirms with `GRAPH.LIST` and the process list
- **Reviewer passes when:** a proposal appears on the page and nothing was built.

## A7. New story: the human-approved build (plan Task 7)
- [ ] `POST /api/agent/build/estimate` (runs `loader.py --case <slug> --dry-run`, returns calls, cost, minutes, RAM and a one-time token for exactly that proposal and its hash) and `POST /api/agent/build` (needs the token, then `--rebuild`, progress streamed to the page); an "Approve and build" panel with the estimate on the page
- [ ] Fixed loader command (only the validated slug varies); one build at a time; RAM pre-flight; failed build leaves the old graph; refused when `TIETRACE_ALLOW_BUILD=0`
- [ ] Offline tests from the plan's Task 7 (token rules, edited proposal invalidates the token, estimate before anything runs, failed build keeps the old graph, refused when not allowed)
- [ ] **Live on the real page:** the Reviewer clicks Approve on a small proposal (a tiny topic with a low `max_api_calls`), watches the progress, and confirms the new `research:<slug>` appears; record time, API calls and cost
- **Reviewer passes when:** no build starts without the human click and the estimate shown first, and the new graph exists after.

## A8. End-to-end "new story" on the real web app (plan Task 8)
- [ ] Offline test `test_new_story_end_to_end_with_a_scripted_model` with the tiny fixture cache (0 API calls), cleaned up after
- [ ] **Live, Gemini free tier, the whole flow on the page:** paste a story about an unloaded company, the agent discovers and proposes, **a human approves and builds**, then the same agent resolves the experts and runs `check_case` on the **new** graph; tiers come back with DOIs or a no-tie result with hops, years and funder coverage; labels are "Expert #n"
- [ ] The same flow once with **Claude** and once with **OpenAI** **if Abdul supplies temporary keys**; otherwise record "not run: no key"
- [ ] Results (story, provider, model id, time, API calls, cost, what failed) go to `eval/results.md` honestly, including failures
- **Reviewer passes when:** the Reviewer has watched the full flow on the page and the numbers in `eval/results.md` match what they saw.

## A9. Test keys, secret scan, docs (plan Task 9)
- [ ] `docs/test-keys.md`: how to create a temporary spend-limited key for Claude and OpenAI, how to use it only through an environment variable, and the **revoke checklist** (a box per provider, ticked with the date after the live tests). Gemini's key is in `.env`; record that it is a free-tier key and that Abdul revokes or rotates it after the demo if he wishes
- [ ] `test_no_secret_shaped_strings_in_the_repo`: scans tracked files for `sk-ant-`, `sk-`, `AIza` followed by 20+ key characters (the canary is excluded by name); also confirms `.env` is git-ignored
- [ ] Update `WebApp.md` section 6 ("judges cannot use their own Claude Code login on a server; bring-your-own API key instead"), `README.md` (a short "Use your own model" section with the key notice), `checklist_phase2.md` (link to this file), `AI_USE.md` (a row per task, official-docs pages cited)
- [ ] Run the full suites on the new container (`test_web`, `test_agent*`, `test_golden`, `test_server`, `test_research`, `test_memory`) and record results; the 10 known Windows failures in `tests/test_web.py` (shell-script stand-ins) are listed separately, not hidden
- **Reviewer passes when:** the secret scan is clean, the docs match the code, and the existing Local mode still behaves as before.

## Open questions (Abdul and Aaryan)
- **O-A.** Should a hosted deployment use the host's Gemini key for visitors who type none (cost and abuse risk, rate limits), or require every visitor to bring a key? Plan default: **visitors bring a key**; the host key is for local use and tests.
- **O-B.** Is `TIETRACE_ALLOW_BUILD` on for the judges' demo? Plan default: on locally, off on any hosted server.
- **O-C.** Which unloaded story do we use for the live new-story tests? Abdul picks (small topic, low cost).

**Gate:** the full new-story flow works on the real web app with Gemini (free tier), Claude and OpenAI work with typed keys or are marked "not run: no key", no key appears anywhere, and everything that worked before still works.
