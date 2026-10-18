# TieTrace intro video script (90 seconds)

*Last updated by Aaryan, Oct 18 2026: first version. Every number below is from `eval/results.md` or the README; do not add one that was not computed. Record in real Claude Code (checkpoints.md, section 9).*

About 220 spoken words. Times are targets, not hard cuts.

| Time | On screen | Voice-over |
|---|---|---|
| 0:00-0:10 | A news story quoting a scientist on sugar and obesity | "A story quotes an expert. Has that expert been paid by the company the story is about? Reporters rarely have time to check." |
| 0:10-0:22 | README diagram: Claude Code → `server.py` → FalkorDB (`research:obesity-coke`, `newsroom:alpha`) | "TieTrace checks it in a research graph built from OpenAlex and stored in FalkorDB: authors, papers, funders and co-author links. Claude Code is the agent; our MCP server makes no LLM calls and only runs read-only Cypher on the research graph." |
| 0:22-0:42 | Claude Code: paste the story, `open_case` then `check_case` run; the tool output shows the Cypher it executed | "I paste the story. TieTrace opens a case and checks each expert. Every answer shows the exact Cypher it ran." |
| 0:42-0:55 | One Tier A1 result: the paper, its DOI as a link, the company funder | "A tie comes with its paper and DOI. Tier A means company money or affiliation, with the evidence attached." |
| 0:55-1:05 | A "no tie found" result with its scope block (hops, years, funder coverage) | "No tie found is never a clean bill of health. It says how far it searched, which years, and that only 56.3% of these papers list any funder." |
| 1:05-1:15 | The permission prompt on `record_verdict`, then `recall` showing the earlier session's check | "Only a human can rule out a paper or approve the note: Claude Code asks every time. And the newsroom remembers: a new session sees earlier checks." |
| 1:15-1:30 | Evaluation table in the README, E3 and E1/E2 rows highlighted | "We tested it honestly. It found all 4 publicly confirmed leaders of a known Coca-Cola-funded network as Tier A. Our network score failed its tests, so we show it as context only. Ties found in the research record: with sources, with limits." |

## Recording checklist

- FalkorDB running with `research:obesity-coke` loaded (`./install.sh` does both).
- Claude Code in normal permission mode, never bypass mode, so the `record_verdict` prompt is visible.
- Experts appear only as "Expert #n" (no names on screen, except the public GEBN case).
- Never say or show the words the project bans for people; say "ties found in the research record".
