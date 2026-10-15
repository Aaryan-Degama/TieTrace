# AI-use log

Hackathon rules 12-13: I must understand and explain all code. This log records what I decided, what the AI (Claude Code) wrote, and what I verified.

| Date (IST) | Decided by me | Written by AI | Verified how |
|---|---|---|---|
| Oct 14 | TieTrace v2 design, tiers, evaluation plan | Review notes on the redesign | Read and edited by hand |
| Oct 15 | Claude Code as the MCP client (no API costs); tie-checking moves into the server, since it needs no LLM; no crawler | `docs/mcp-spec.md`, `CLAUDE.md`, `checkpoints.md` | Read through; tools mapped to Q1-Q5 and the Track 2 brief |
| Oct 15 | Run FalkorDB in Docker with a named volume (`falkordb-data`), so memory graphs survive restarts | `pyproject.toml` via `uv` (falkordb, mcp, httpx) | `RETURN 1` query from Python returned `[[1]]` |

## Learned (checkpoint 1 notes)
<!-- One line per toy-graph test: what I expected, and what actually happened. -->
