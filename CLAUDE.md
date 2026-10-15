# TieTrace

FalkorDB Graph Hacks entry (Tracks 1 and 2). **Submit by Oct 18 2026, 6 PM IST.**
TieTrace checks how connected an expert is to the company in a story, using an OpenAlex research graph in FalkorDB. Every tie comes with its DOI, and every "no tie found" comes with its scope.

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
- **`COAUTH` is stored in both directions.**
- **Score formula is fixed** (redesign section 4). Do not tune it after seeing E1/E2 results.
- **Human-only tools never get allowlisted:** `record_verdict`, `approve_note`, `set_policy`. Claude Code's permission prompt is the human gate.

## Working style
- **The author is a beginner** and must be able to explain every query and decision to the judges (rules 12-13). Keep the code boring and small, and prefer plain Cypher over clever abstractions.
- **Log AI help in `AI_USE.md`:** what was decided, what the AI wrote, and what was verified.
- **Report evaluation results honestly**, including failures. Never put a number in the README or demo that was not computed.
- **Cut order if behind:** second topic, deployment, blog post, Review Desk, `set_policy`, the max-flow test (E4).

## Commands
- **FalkorDB:** `docker run -p 6379:6379 -p 3000:3000 -it --rm falkordb/falkordb:latest` (Browser at http://localhost:3000)
- **Register the server:** `claude mcp add tietrace-alpha -e TIETRACE_NEWSROOM=alpha -- python server.py`
