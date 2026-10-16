# TieTrace

*Last updated by Abdul, Oct 15: first simple README. Work in progress for FalkorDB Graph Hacks (Tracks 1 and 2).*

TieTrace checks how connected an expert is to the company in a news story. It uses a research graph built from [OpenAlex](https://openalex.org) (CC0 data) and stored in [FalkorDB](https://www.falkordb.com).

- Every tie it reports comes with its paper and DOI.
- Every "no tie found" comes with its scope: how many hops, which years, and how much of the data has funder and affiliation records.
- It never says "independent" or "conflicted". It says "ties found in the research record". A missing tie is not proof of independence, because OpenAlex is incomplete.

The first case is Coca-Cola and obesity research.

## The tiers

| Tier | Meaning |
|---|---|
| A1 | Authored a small paper (25 authors or fewer) funded by, or affiliated with, the company |
| A2 | Foundation grant, bottler affiliation, or the only evidence is a large multi-author paper |
| B | Co-authored 2 or more small papers with a Tier A person since 2021 |
| C | Within 2-3 co-author hops of a Tier A person (context only) |

Only Tier A is a "finding". Details are in [tietrace-v2-redesign.md](tietrace-v2-redesign.md).

## How it works

- **Claude Code** is the agent and MCP client. It reads the journalist's input and picks tools.
- A **Python MCP server** runs fixed, read-only queries against FalkorDB. It makes no LLM calls and needs no API key.
- A **Verifier** (plain code) rejects any claim without a stored DOI, or any "no tie" claim without its scope.
- A **human** approves verdicts and notes. Experts are shown as "Expert #n" unless the case is the public one.

The full tool contract and diagrams are in [docs/mcp-spec.md](docs/mcp-spec.md).

## Setup

You need Docker, Python 3.13 or newer, and [uv](https://docs.astral.sh/uv/).

1. Start FalkorDB (use `6379` and `3000` if they are free; on Abdul's machine they are taken):

   ```bash
   docker run -d --name falkordb -p 6380:6379 -p 3001:3000 -v falkordb-data:/var/lib/falkordb/data falkordb/falkordb:latest
   ```

   The FalkorDB Browser opens at http://localhost:3001. Next time, use `docker start falkordb`.

2. Install the Python packages:

   ```bash
   uv sync
   ```

3. Put your OpenAlex key in `.env` as `OPENALEX_API_KEY=...`. Never commit this file.

4. Build the research graph. With the `data/raw/` cache present this makes zero API calls (about 100 seconds). Add `--refresh` to re-download from OpenAlex.

   ```bash
   FALKORDB_PORT=6380 uv run python loader.py --rebuild
   ```

5. Run the tests:

   ```bash
   FALKORDB_PORT=6380 uv run python tests/test_loader.py
   ```

## Files

| File | What it is |
|---|---|
| `loader.py` | Builds the research graph from OpenAlex |
| `research.py` | Queries Q1-Q4 and name lookup (checkpoint 3, under review) |
| `verifier.py` | Claim checker (checkpoint 3, under review) |
| `tests/` | Plain-assert tests |
| `docs/mcp-spec.md` | MCP tool contract |
| `tietrace-v2-redesign.md` | Design: tiers, graph model, queries, evaluation plan |
| `checkpoints.md` | Progress tracker and who owns what |
| `AI_USE.md` | Log of what the AI wrote and what we verified |

## Status

Checkpoints 0-2 (setup, toy graph, loader) are done. Checkpoint 3 (queries and Verifier) is in review. The MCP server, the memory graph, and the evaluation (E1-E11) are not built yet, so there are no evaluation results to report. See [checkpoints.md](checkpoints.md).

## Team

Abdul and Aaryan. See `checkpoints.md` and `AI_USE.md` for who did what.

## AI use

Most code is written with Claude Code, using a Builder/Reviewer loop. We log every AI-assisted change and what we verified in [AI_USE.md](AI_USE.md).

## Data and licences

Research data comes from OpenAlex (CC0). FalkorDB's licence still needs to be confirmed on its repo before submission.
