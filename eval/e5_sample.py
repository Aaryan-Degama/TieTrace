"""Last updated by Abdul, Oct 16 2026: wrote the sample drawer.
E5 (profile guard), step 1: draw the 50 random experts a HUMAN then hand-checks (redesign section 7).
This script only draws the sample and lists facts from the graph; it decides nothing. The columns for the
human's verdict (merged profile? split profile? fine?) are left empty, and the error rate is computed from them.
Population: authors of at least one topic paper. Fixed seed. Names are left out (experts are "Expert #n");
open the OpenAlex link to look the person up.
Run:  FALKORDB_PORT=6381 uv run python eval/e5_sample.py   -> eval/e5_sample.md
"""
import random
from pathlib import Path

import common as c

SEED, N = 20261016, 50
works = c.load_works()
topic_papers = [w for w, v in works.items() if v["topic"] and v["paper"]]
people = sorted({p for ps in c.load_authors(topic_papers).values() for p in ps})
sample = sorted(random.Random(SEED).sample(people, N))
rows = c.by_ids("MATCH (p:Person) WHERE p.oa_id IN $ids "
                "OPTIONAL MATCH (q:Person) WHERE q.name_norm = p.name_norm AND q.oa_id <> p.oa_id "
                "OPTIONAL MATCH (p)-[:AUTHORED]->(w:Work) "
                "RETURN p.oa_id, p.orcid, p.works_count, p.profile_flag, count(DISTINCT q), count(DISTINCT w)", sample)
by_id = {r[0]: r for r in rows}
lines = ["# E5 profile guard: hand-check sample (50 random experts)", "",
         f"*Drawn by `eval/e5_sample.py` (seed {SEED}, {len(people)} authors of topic papers in `{c.GRAPH}`). The last three columns "
         "are for a HUMAN to fill in; nothing here is a verdict.*", "",
         "How to check: open the OpenAlex link, look at the works list. Mark **merged** if the profile mixes two people, "
         "**split** if the same person has another profile in OpenAlex (a profile with the same name is a hint, "
         "column `same-name profiles`), **ok** otherwise. Error rate = (merged + split) / 50.", "",
         "| # | OpenAlex | ORCID | works_count | loaded works | profile_flag | same-name profiles | merged / split / ok | note |",
         "|---|---|---|---|---|---|---|---|---|"]
for i, pid in enumerate(sample, 1):
    _, orcid, wc, flag, same, loaded = by_id[pid]
    lines.append(f"| {i} | [{pid}](https://openalex.org/{pid}) | {orcid or ''} | {wc} | {loaded} | {flag} | {same} |  |  |")
Path(__file__).with_name("e5_sample.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote eval/e5_sample.md: {len(sample)} experts of {len(people)}")
