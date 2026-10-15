# Long-running "build a research graph" job behind a stdio MCP server (Python mcp 2.3.0, Claude Code)

Labels: [V] verified by reading installed source or fetched docs; [I] inference. Local source root: `C:\Users\Azeem\Desktop\TieTrace\.venv\Lib\site-packages` (abbrev. SP). Claude Code docs page fetched 2026-10-15 (cached copy): https://code.claude.com/docs/en/mcp

## 1. What the installed `mcp` 2.3.0 SDK really supports

### Takeaway
Progress, cancellation, elicitation (form + URL) and logging exist and work from a tool. Spec-level Tasks exist only as wire *types*; the SDK 2.3.0 has NO Tasks server implementation (no `TasksExtension`), so pattern (c) is not available out of the box. A sync tool runs in a worker thread and cannot be force-cancelled.

### Cited Findings
- `ctx.report_progress(progress, total, message)` exists; it is a no-op when the caller did not send a progress token — SP/mcp/server/session.py:426-434 and SP/mcp/server/mcpserver/context.py:120-128. [V]
- Elicitation: `ctx.elicit(message, PydanticModel)` (form mode, primitives only, returns accept/decline/cancel) and `ctx.elicit_url(...)` — SP/mcp/server/mcpserver/context.py:196-260; implementation SP/mcp/server/elicitation.py:103,146. Docstring: "if the client is an agent, it might decide how to handle the elicitation -- either by asking the user or automatically generating a response." [V]
- Logging: `ctx.log()` is marked `@deprecated` "as of 2026-07-28 (SEP-2577)" — context.py:~268-285; `roots`, `ping`, client-to-server progress also deprecated in client.py. [V]
- Resource subscriptions: legacy `resources/subscribe` "removed as of 2026-07-28; use Client.listen()"; servers publish via `ctx.notify_resource_updated(uri)` to `subscriptions/listen` streams — SP/mcp/client/client.py:741-754, context.py:149-155. [V]
- Cancellation: dispatcher applies `notifications/cancelled` by cancelling the handler scope ("interrupt", default) or only setting `ctx.cancel_requested` ("signal") — SP/mcp/shared/jsonrpc_dispatcher.py:86-88,151. [V]
- Sync tool functions run via `anyio.to_thread.run_sync` — SP/mcp/server/mcpserver/utilities/func_metadata.py:164. [I] A Python thread cannot be interrupted, so cancelling the request abandons the call but the thread (and any subprocess it started) keeps running unless the code watches a flag.
- Tasks: wire types exist (`Task`, `CreateTaskResult`, `tasks/get`, `tasks/cancel`, `tasks/result`, `TaskMetadata`) in SP/mcp_types/_types.py:622-738, but SP/mcp_types/methods.py:101,146,174,209 say "tasks/* deliberately absent" from the 2025-11-25 method table. `grep -r TasksExtension` over `mcp/` and `mcp_types/` returns nothing. The generic `Extension` / `MethodBinding` classes (SP/mcp/server/extension.py:40-100, example of binding "tasks/get") would let you hand-write one, and `Apps` (SP/mcp/server/apps.py) is the only shipped extension. [V]
- Spec: Tasks moved out of experimental core into extension `io.modelcontextprotocol/tasks` in spec 2026-07-28 (poll-based `tasks/get`, new `tasks/update`) — https://blog.modelcontextprotocol.io/posts/2026-07-28/ ; https://modelcontextprotocol.io/extensions/tasks/overview ; https://github.com/modelcontextprotocol/ext-tasks. A Python server-side/client `TasksExtension` was described as "in an open pull request, not yet in the released v2.0.0" — https://newsletter.victordibia.com/p/mcp-july-2026-update-stateless-core (a secondary newsletter; confirmed absent from the installed 2.3.0 by grep).
- FastMCP (separate library, not what server.py uses) offers `@mcp.tool(task=TaskConfig(...))` with `fastmcp_tasks.TasksExtension`, Redis/Docket backends — https://gofastmcp.com/servers/tasks. Heavy for this project. [V that it exists; I that it is overkill]

### Inferences
- Even if a Tasks server were hand-built, it only helps if Claude Code negotiates the extension (see section 2: no evidence it does).
- Because server.py holds `_lock` for every DB call, a long in-process build would block all read tools unless it uses its own connection/graph name.

### Gaps
- Exact day the Python SDK ships a stock `TasksExtension` is unknown; not needed for the recommendation.

## 2. What Claude Code does with long calls

### Takeaway
Claude Code already copes with long MCP calls: wall-clock limit defaults to about 28 hours, stdio idle timeout is 30 minutes unless progress is reported, and any call over 2 minutes is auto-moved to a background task. No evidence in its MCP docs of support for the MCP Tasks extension. So a plain long-blocking tool is viable for Claude Code, but blocks nothing important and survives only while the session lives.

### Cited Findings
- `MCP_TIMEOUT` = server startup timeout; per-server `"timeout"` (ms) in `.mcp.json` overrides `MCP_TOOL_TIMEOUT` for that server; per-server timeout is a hard wall-clock limit that progress notifications do NOT extend; unset `MCP_TOOL_TIMEOUT` defaults to "about 28 hours" — https://code.claude.com/docs/en/mcp
- Idle timeout: a call with no response and no progress notification aborts after the idle window; default 30 minutes for stdio (5 min for HTTP/SSE/WS); change with `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` (0 disables) — same page. Stdio/WebSocket servers have no per-request first-byte timer.
- Automatic backgrounding (Claude Code v2.1.212+): a main-conversation MCP call still running after 2 minutes becomes a background task; Claude gets a task ID, the result arrives as a task notification, shown in `/tasks`, shows the latest server progress, "doesn't survive exiting the session"; stopping cancels the call but "doesn't guarantee the server stops" — same page, section "Automatic backgrounding of long tool calls".
- Output: warning above 10,000 tokens, default cap 25,000 tokens (`MAX_MCP_OUTPUT_TOKENS`); results over the cap are written to a file in the session's tool-results dir — same page, "MCP output limits and warnings". Per-tool override via `anthropic/maxResultSizeChars`.
- Elicitation: Claude Code shows an interactive dialog (form or URL mode); on 2026-07-28 connections it declares `elicitation: {form:{}, url:{}}`; an `Elicitation` hook can auto-respond without a dialog — same page, "Respond to MCP elicitation requests"; hooks at https://code.claude.com/docs/en/hooks#elicitation.
- Server shutdown on Windows: Claude Code closes stdin, then ends the server's process tree after 300 ms (before v2.1.296 it killed the tree at once) — same page. macOS/Linux: SIGINT, SIGTERM (100 ms), SIGKILL (400 ms).
- Permissions/hooks: MCP tools are named `mcp__<server>__<tool>`; `PreToolUse` hooks match them by regex (`mcp__tietrace__.*`) and can return `permissionDecision` allow/deny/ask/defer, and "Deny and ask rules are still evaluated regardless of what the hook returns" — https://code.claude.com/docs/en/hooks
- Stdio servers: Claude Code sets `CLAUDE_PROJECT_DIR` in the server's environment — same MCP page. `-e KEY=value` passes env vars at `claude mcp add`.
- The MCP page text I extracted contains no mention of the Tasks extension or `tasks/get` (0 matches for "modelcontextprotocol/tasks", "task-augmented", "tasks/get"). [V for the absence in that page text; I that Claude Code does not use MCP Tasks]

### Inferences
- A tool that blocks for 10-40 minutes is legal for Claude Code (wall clock default ~28 h; idle 30 min if it emits progress at least every few minutes). It will be auto-backgrounded after 2 min and the model can continue only after the notification; the build dies if the session exits (and, on Windows, probably when the MCP server process tree is ended). [I]
- The loader can run in `Bash` with `run_in_background` (Claude Code feature; I did not fetch the Bash tool docs this round), which is the same session-lifetime caveat but is visible in the terminal and covered by Bash permission rules. [I]

### Gaps
- Not checked: exact Windows behaviour of a child started with `DETACHED_PROCESS`/`CREATE_BREAKAWAY_FROM_JOB` when Claude Code ends the tree; assume it may be killed too unless tested. No issue-tracker search for Windows timeouts done (tool budget).

## 3. Pattern comparison

### Takeaway
(a) Human-approved CLI via Bash, MCP server read-only, is the smallest, safest and most explainable. (b) is workable but adds a model-reachable write/spend path and Windows orphan issues. (c) is not supported by the installed SDK or evidently by the client. (d) elicitation is a good optional approval gate inside (b) but is a soft gate.

| | (a) Bash CLI, server reads only | (b) `start_build`+`build_status` | (c) Task-augmented tool | (d) elicitation approval |
|---|---|---|---|---|
| Timeouts | Bash background job; no MCP timer involved | start returns at once; status polls are fast | would avoid blocking, but not available | none by itself; dialog waits for user |
| Orphans on Windows | job is a visible Bash process; ends with session | detached child may be killed with MCP tree or survive as orphan [I]; needs pid file | n/a | n/a |
| Partial graph | solved by staging graph + swap in the CLI | same, plus status file | same | n/a |
| Secrets | .env read only by CLI; server never touches key | server process (or child) needs key; env passes via `-e`; risk of leak in status/log | same | n/a |
| Model-supplied strings | model writes a config file; CLI validates it; command line is fixed `--case <id>` | tool args validated by schema; no shell if `subprocess` list-args | same | n/a |
| Spend/write by model | none from MCP; Bash call requires user approval (permission prompt) | model can trigger spend; only gate is MCP permission prompt (+ optional elicitation) | same | extra human confirm |
| Testability | plain unit tests on config validation + dry-run | needs process/state tests | high effort | hard to test (needs client) |
| Beginner explainability | highest ("we run a script") | medium | low | medium |
| Size (est.) [I] | ~30-60 lines changes to loader (`--case`, `--dry-run`, staging swap) + 1 read-only `list_cases` | + ~80-120 lines (spawn, state file, status, cancel) | 200+ lines hand-built Extension | ~15 lines on top of (b) |

### Cited Findings
- Claude Code project rule in CLAUDE.md: human-only tools never allowlisted; permission prompt is the human gate (local: `C:\Users\Azeem\Desktop\TieTrace\CLAUDE.md`). [V]
- Elicitation is only reliable as a gate when the client shows a dialog; the docs say agents/hook may auto-respond (`Elicitation` hook, https://code.claude.com/docs/en/mcp) and SDK docstring says an agent client may auto-generate the response (context.py:196-215). [V]
- Local loader today: CLI with `--rebuild`/`--refresh`, drops the graph then rebuilds; it reads the key from env (`KEY` passed as `api_key` query param, loader.py:89, network errors caught without printing the URL, loader.py:90). It exits with "already exists; pass --rebuild" (loader.py:468). Server tools are all `readOnlyHint=True` (server.py:~34-51). [V]

### Inferences
- Build the dangerous/expensive work where Claude Code's ordinary Bash permission prompt already shows the exact command to the human; that keeps the MCP surface read-only as the project rules intend.
- (b) breaks the sentence "the server has no write path from the model" and needs `readOnlyHint=False`, a new gated tool name and its own allowlist exception.

### Gaps
- Not measured: actual download time/API-call count for a new topic (needs dry-run implementation).

## 4. Keeping model-reachable writes minimal (config, validation, budgets, atomicity)

### Takeaway
Let the model only propose a JSON "case config" file; a validator checks it; a dry run prints an estimate; the human approves the exact Bash command; the CLI builds into a staging graph and swaps on success.

### Cited Findings
- Claude Code permission prompt shows the exact Bash command; PreToolUse hooks can `ask`/`deny` and can inspect tool input (https://code.claude.com/docs/en/hooks), so a hook could additionally deny any Bash command that is not `uv run python loader.py --case <id>` or `--dry-run`. [V for hook capability; I for using it]
- Cache and idempotence already exist: `data/raw/*.jsonl` is used when present, `--refresh` re-downloads (CLAUDE.md, loader.py docstring lines 3-6). [V]

### Inferences (recommended design, none of this is implemented)
- Config file `cases/<case_id>.json`, written by Claude Code (Write tool, itself permission-gated). Case id regex `^[a-z0-9][a-z0-9_-]{2,40}$`, which also becomes graph name `research:<case_id>` (never concatenated into Cypher; pass as the graph name argument and check the regex first; FalkorDB graph names are keys, not parameters).
- Fields: `company_family` (list of OpenAlex institution/funder IDs, regex `^[IF]\d{4,12}$` and `T\d+` for topics, `A\d+` authors), `topic_ids`, `years` (integer range within e.g. 2000-current year), `max_works` and `max_api_calls` hard caps (e.g. 2000 calls), reject unknown keys, file size < 20 KB, list length caps (<= 20 IDs per list). Use stdlib `json` + hand checks or pydantic (already installed with mcp) to stay small. Never accept free-text names to be interpolated into URLs; resolve names in a separate read-only lookup.
- `--dry-run`: only counts (OpenAlex `per_page=1` + `meta.count`), prints estimated calls/works/time, writes nothing, so a human sees the budget before approving the real run. Real run aborts if the running API-call counter passes `max_api_calls`.
- Atomic swap: build into `research:<case>__staging`; on success run a final self-check (node counts > 0, Meta node written with `data_version`, `recent_year`); then swap. FalkorDB offers `GRAPH.COPY` and `GRAPH.DELETE`; I believe there is no `GRAPH.RENAME`, so do copy-then-delete, or simply name the final graph only after success and never let tools see `*__staging`. [I; FalkorDB commands not re-verified this round]. Add a `build_status` Meta field (`complete`) and make the server's resolver refuse graphs without it.
- Resume: cache keyed by (case_id, query hash) in `data/raw/<case_id>/`; JSONL files written to `.tmp` then renamed so a killed run never leaves a truncated file the cache would trust.
- Server side: add one read-only tool `list_cases` (reads `GRAPH.LIST` + Meta) and keep `TIETRACE_NEWSROOM` from env; the graph/case selection could come from an env var or a read-only tool argument validated against `list_cases`, not free text. Decision for the team since the rules say newsroom (not case) comes from env.
- API key: only the CLI reads `.env`; never `-e OPENALEX_API_KEY` into the MCP server config; add a deny rule in Claude Code settings for `Read(.env)` (permission rules exist; syntax not re-fetched this round) [I].

### Gaps
- Exact FalkorDB copy/rename command semantics and cost for a big graph were not verified.

## 5. Recommended workflow for a two-person beginner team (deadline Oct 18 2026)

### Takeaway
Do pattern (a). It is the only option that needs almost no new server code, keeps rules intact (server read-only, no key exposure), and is demoable. If a demo needs "one-click" feel, use a slash command or subagent that orchestrates the same Bash steps.

### Steps (human gates marked)
1. Journalist pastes story. Claude Code (main session) extracts company/topic/names. [V that the UX is desired per assignment]
2. Call `list_cases` (new tiny read-only tool) or read-only graph list: if `research:<case>` exists and `complete`, go straight to the existing tools.
3. Otherwise Claude writes `cases/<case>.json` and runs `uv run python loader.py --case <case> --dry-run` (Bash; allow-listed as read-only since it writes nothing and makes only count calls — optional).
4. Claude presents the plan and estimate. GATE 1: human confirms in chat.
5. Claude runs `uv run python loader.py --case <case> --build` with Bash `run_in_background` (or the human runs it in their own terminal). GATE 2: Claude Code's permission prompt shows the exact command; do not allowlist the `--build` form.
6. Claude polls the log/output file (Bash background output is the status channel; the loader prints progress lines and a final `BUILD OK graph=research:<case> works=N calls=M`).
7. On success the existing MCP tools run against the new graph; on failure the old graph is untouched and the staging graph is deleted next run.

### Encoding in Claude Code
- A project slash command / skill (`.claude/commands/new-case.md` or a skill) containing steps 1-7 as a procedure, and a subagent in `.claude/agents/` (e.g. `case-builder`) with tools limited to Write (cases dir), Bash, Read, and no MCP write tools. Slash command and skill file locations/format described in Claude Code docs; format not re-fetched this round, verify at https://code.claude.com/docs/en/skills and https://code.claude.com/docs/en/sub-agents before writing. [I]
- Optional PreToolUse hook (matcher `Bash`) that denies any `loader.py` invocation with `--refresh` or without a valid `--case` and `cases/<id>.json`; hook JSON output schema verified at https://code.claude.com/docs/en/hooks. [V schema, I design]
- Set in `.mcp.json`/`claude mcp add` only what exists: no `timeout` change is needed for (a).

### If (b) is still wanted later
Minimal safe version: `start_build(case_id)` takes only an id of an existing validated config file, refuses if a PID/state file shows a running build, spawns `subprocess.Popen([sys.executable, "loader.py", "--case", id, "--build"], creationflags=CREATE_NEW_PROCESS_GROUP, stdin=DEVNULL, stdout=logfile)`, no shell; `build_status(case_id)` reads the state file and tail of log; add `ctx.elicit` confirmation showing the dry-run estimate; mark `readOnlyHint=False`, and never allowlist it. Expect Windows to end the process tree on session end (docs section 2), so either accept that the build dies with the session or resume from cache next time. [I]

### Cut list suggestion
Drop (b), (c), (d) for the Oct 18 submission; document them as "future work" with the evidence above (SDK 2.3.0 lacks a Tasks implementation; Claude Code auto-backgrounds long calls after 2 min).

### Gaps
- No real Windows test of detached child survival; no check of Claude Code v-number on this machine (auto-backgrounding needs v2.1.212+, `Elicitation` hook and others need newer versions; run `claude --version`).

## Sources (URLs)
- https://code.claude.com/docs/en/mcp
- https://code.claude.com/docs/en/hooks
- https://modelcontextprotocol.io/extensions/tasks/overview
- https://blog.modelcontextprotocol.io/posts/2026-07-28/
- https://github.com/modelcontextprotocol/ext-tasks
- https://newsletter.victordibia.com/p/mcp-july-2026-update-stateless-core
- https://gofastmcp.com/servers/tasks
- Local: SP/mcp/server/mcpserver/context.py, SP/mcp/server/session.py, SP/mcp/server/elicitation.py, SP/mcp/server/extension.py, SP/mcp/shared/jsonrpc_dispatcher.py, SP/mcp/server/mcpserver/utilities/func_metadata.py, SP/mcp_types/methods.py, SP/mcp_types/_types.py; project files server.py, loader.py, CLAUDE.md.
