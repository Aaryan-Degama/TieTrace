# OpenAlex entity discovery and download for an arbitrary company + topic + people (as of 2026-10-15)

Legend: [DOCS] = stated in official docs; [LIVE] = I ran it unauthenticated on 2026-10-15 (read-only); [INFER] = my inference.
Doc sites: developers.openalex.org now 301-redirects to help.openalex.org. Every help page has a raw `.md` version; agent index at https://help.openalex.org/llms.txt . Old docs.openalex.org still exists but is stale (it showed an older pricing model).

## 1. Resolving a company name to funder IDs and institution IDs

### Takeaway
Two separate lookups are needed because funders and institutions are different entity types with different IDs, and neither has a reliable name-to-company link. Name search finds candidates cheaply, but picking the right ones (parent vs subsidiary vs front-organisation foundation) needs a judgement step. Funders are flat (no hierarchy); institutions have a parent/child `lineage`.

### Cited Findings
- Funders: `GET /funders?search=<name>`; the list is grounded in the Crossref Open Funder Registry, enriched with ROR/Wikidata. Funders are flat: no parent/child/lineage field, so subsidiaries cannot be rolled up. Duplicates may remain unmerged and small funders may be missing. A funder mentioned only in noisy free text may not match, so a funded work can show no funder — [Funders doc](https://help.openalex.org/data/funders/)
- [LIVE] `/funders?search=philip morris` returned 2 rows: `F4320315260` "Philip Morris International" (works_count 990, ror 005rxfw29, crossref 100014729, description "American global cigarette and tobacco company") and `F4320318650` "Foundation for a Smoke-Free World" (works_count 100; description "front organization of tobacco corporation Philip Morris"). So a text search for the company name also returned a PM-funded foundation, a different legal entity. Whether it counts as "the company" is a human/editorial call (same kind of call as Coca-Cola Foundation = Tier A2 in loader.py).
- [LIVE] `/funders?search=altria` -> only `F4320308222` "Altria Group" (93 works). Altria is the US parent of Philip Morris USA, but a search for "philip morris" does not return it. Name search does NOT find corporate-family relations; a human/LLM must supply the alias list (Altria, Philip Morris USA, PMI, Reynolds American, British American Tobacco...).
- [LIVE] `/funders?search=reynolds american` -> 0 results (cost_usd 0.0: empty searches appear free). `/funders?search=british american tobacco` -> `F4320321461` (415 works). Absent funders are normal; "no funder record" must be reported as unknown, not as "no funding".
- [LIVE] `/autocomplete/funders?q=philip morris` returns `id, display_name, hint (description), works_count, cited_by_count, external_id (ROR)`, `cost_usd 0.0`. Cheap candidate list. Same endpoint family exists for authors, institutions, topics. Docs: https://help.openalex.org/api/autocomplete/ (listed in llms.txt; I did not fetch its body)
- [LIVE] `/institutions?search=philip morris` returned 4 rows incl. `I2800621710` "Philip Morris International (Switzerland)" (type company, works_count 1244, lineage [I2800621710, I4210088192], associated_institutions: parent I4210088192), `I4210114941` (Germany, 90 works, parent I4210088192), and `I4210088192` "Philip Morris International (United States)" (141 works, ROR 005rxfw29, children = the Swiss and German entities). The "parent" has fewer works than a child: the parent is a ROR-structure artefact, not the biggest node.
- [LIVE] `/institutions?search=altria` -> `I4210158204` "Altria (United States)", type company, 1098 works, lineage only itself (no link to Philip Morris entities).
- Institution filter that follows hierarchy: `authorships.institutions.lineage:<ID>` — [LIVE] `lineage:I4210088192` gave 985 works (parent plus children). Direct affiliation records: `authorships.institutions.id`.
- [LIVE] Works filter `funders.id:F4320315260` -> 977 works (funder record says 990; the 13 gap is unexplained). `funders.id:F4320315260|F4320318650` (OR with `|`) -> 1,077. `awards.funder_id` is also a valid works filter (appears in the API's field-error list); the funders doc says use `funders.id` and does not document `awards.funder_id`.
- [LIVE] Overlap: works with institution lineage PMI-US AND funder PMI = 247 (of 977 funded / 985 affiliated). Funded-by and employed-by are largely distinct sets; a tier scheme needs both queries.
- [LIVE] Raw affiliation text: `/works?filter=raw_affiliation_strings.search:"philip morris"` -> 1,834 works (search price $0.001). The long form `authorships.raw_affiliation_strings.search` is NOT valid (error). 1.9x the institution-lineage count (985): catches affiliations OpenAlex did not map to an institution, but also false positives.
- ROR: funder and institution records expose `ror`; the same ROR (005rxfw29) appears on the funder and the US institution [LIVE], so ROR is a reliable join key for the same legal entity. Funder `roles` link the organisation to its institution/publisher record but not to other funders — [Funders doc](https://help.openalex.org/data/funders/)
- Filters accept up to 100 OR values (`a|b|c`); per_page max 100 — [Authentication/limits guide](https://help.openalex.org/guides/authentication)

### Inferences
- Detecting a wrong match deterministically: (1) compare `description`/`type`/`country_code`/ROR; (2) check `type == company` for institutions (foundations may carry other types); (3) sanity-check `works_count` scale; (4) sample ~5 recent works per candidate and check the funder/affiliation string; (5) always present the full candidate list including rejected ones. Choosing which candidates are the company is judgement (Claude Code or human); fetching is code.
- Safe design: code returns `candidates` (id, name, type, country, ROR, works_count, parent/children, description); Claude picks and records `company_family = {id: role}` with role in {parent, subsidiary, foundation, bottler-like}; human confirms. Family expansion via `associated_institutions` is deterministic but incomplete (Altria not linked to PMI; Coca-Cola parent had none per loader.py comments).
- A Philip Morris story may legitimately need Altria IDs: an editorial scoping decision to log in the scope field.

### Gaps
- No quantified precision/recall of funder name matching found; only the docs' statement that matching can fail.
- Did not enumerate every PMI-related institution (e.g. Philip Morris Products SA); `display_name.search` can enumerate more.

## 2. Resolving a free-text topic

### Takeaway
Use `/topics?search=` and `/keywords?search=` to get candidate IDs for Claude/human selection, then filter works either by topic ID (broad, cheap) or by `title_and_abstract.search` phrase (narrow, search-priced). Topics are coarse, so a topic ID is much broader than the story's topic.

### Cited Findings
- Search semantics: stemming on, stop words dropped, whole-word matches, uppercase `AND/OR/NOT`, quoted phrases, `~N` proximity, `search.exact` disables stemming; topics/keywords searched on display name and description; works search covers title, abstract, full text and keywords, and also matches papers tagged with a named keyword. URL limit ~4 KB. Search calls cost 10x list calls — [Searching](https://help.openalex.org/api/searching/)
- [LIVE] `/topics?search=lung cancer screening` -> exactly 1 topic: `T10202` "Lung Cancer Diagnosis and Treatment", works_count 205,297, subfield 2740 "Pulmonary and Respiratory Medicine", keywords include "lung cancer screening" (score 0.64), "low-dose CT", "pulmonary nodules". The topic is much wider than screening (surgery, lobectomy etc.).
- [LIVE] `/autocomplete/topics?q=lung cancer screening` -> 0 results (autocomplete missed the multi-word query that regular search matched). Use `/topics?search=` or `/keywords?search=`.
- [LIVE] `/keywords?search=lung cancer screening` -> 9 keywords; top `keywords/lung-cancer-screening` with alternative names and `primary_topic` T10202 (score 0.64). Works filter `keywords.id` exists. Keywords are finer than topics.
- [LIVE] Counts for one concept: `topics.id:T10202` 478,814 works (any of a work's topics); `primary_topic.id:T10202,publication_year:2021-2026` 38,318; `title_and_abstract.search:"lung cancer screening"` 12,703 (cost $0.001). Valid related filters: `primary_topic.id`, `topics.id`, `topics.subfield.id`, `topics.field.id`, `keywords.id`, `keyword.search`, `title_abstract_keywords.search`, `abstract.search`, `topics_count`.
- Existing loader: topics T10010 and T10352 were hand-picked; ~260k works, capped to 30,000 (most cited per year over 17 years) — loader.py lines 35-43.
- `search.semantic` matches by meaning, cannot be combined with other search params; limited to 1 req/s — [Searching](https://help.openalex.org/api/searching/), [limits](https://help.openalex.org/guides/authentication)

### Inferences
- Topic-ID filter: high recall, low precision, 10x cheaper per call, deterministic. Phrase search: precise for the literal term, misses synonyms, 10x call price but fewer works (12.7k vs 478k). Best: topic ID for the download, `title_and_abstract.search` phrase as an on-topic/off-topic label (matches the existing Tier A on/off-topic labelling).
- Which topics are right is judgement: code returns top-N topics, subfields and keywords with counts; Claude picks; human confirms. Expect 1-3 topics, ~10^5 works, so cap by year or restrict to authors of interest.

### Gaps
- Did not check the legacy "concepts" endpoint status; treat as legacy.
- Did not fetch the Topics doc page for classification accuracy.

## 3. Author name search and disambiguation

### Takeaway
Name search returns many same-name people (Maria Garcia: 16,059 hits; Rajesh Kumar: ~3,100-3,400), so author resolution cannot be fully automatic. Code can rank candidates with signals; Claude/human must confirm, and the system must show alternatives.

### Cited Findings
- [LIVE] `/authors?search=maria garcia` -> count 16,059; hits included loosely matching names ("Isabel Sánchez", "José María García-Mina"; alternative names and stemming are searched). Always re-filter on normalised exact names client-side.
- [LIVE] `/authors?filter=display_name.search:rajesh kumar` -> 3,111 (search price $0.001). `/autocomplete/authors?q=rajesh kumar` -> 3,388, free (`cost_usd 0.0`), returns `hint` (institution, country), works_count, cited_by_count, ORCID in `external_id`, and `filter_key` `authorships.author.id`. Top hits: Rajesh Kumar, Univ. of Delhi (993 works); Rajesh Kumar, Univ. of Johannesburg (917); another at BARC.
- [LIVE] Combined filter `display_name.search:rajesh kumar,last_known_institutions.id:I4210088192` is valid syntax (0 results there). Useful when the story gives an affiliation.
- Disambiguation is ML over six signals (name similarity, co-authors, affiliations, topics, citations, ORCID). Two failure modes: splitting (one person across profiles) and merging (several people in one). July 2023 re-ID of all authors (IDs numerically above 5000000000); `A9999999999` = NULL author (undisambiguated authorships); `A5317838346` = deleted. ORCID is rarer than people assume and not applied retroactively. Fix path is curations. No accuracy figure on the page — [Disambiguation](https://help.openalex.org/data/authors/disambiguation/)
- [LIVE] Works by author: `authorships.author.id:A…` and `author.id:A…` both valid. A5040420180 (Rajesh Kumar, Delhi) -> 979 works; first work returned has topic "Nanofluid Flow and Heat Transfer", i.e. a topic-overlap check would expose a wrong Rajesh Kumar for a lung-cancer story.
- ORCID filters exist: `author.orcid`, `authorships.author.orcid` (from the API field list) [LIVE].
- `raw_author_name.search` matches bylines (quote a name for one byline; unquoted terms can match across different authors of one work); catches unlinked authorships — [Searching](https://help.openalex.org/api/searching/)
- Existing loader already treats works_count > 2000 as a likely merged profile (WORKS_COUNT_ANOMALY), a guess not a study — loader.py line 61.

### Inferences
- Deterministic candidate card: works_count, ORCID present, last_known_institutions vs story affiliation, topic overlap with chosen topic IDs (`group_by=primary_topic.id` on the author's works, ~$0.0001), count of works matching the story topic, share of works with funder data. Return `status: unique | ambiguous | not_found`; never guess. Present top 3-5 and require confirmation.
- Merge risk inflates ties (false positive); split risk hides ties (false negative). Report profile IDs used and list sibling profiles with the same name and institution.

### Gaps
- No quantitative OpenAlex author-disambiguation accuracy retrieved in this pass.
- Did not test `last_known_institutions` vs the newer `affiliations` field (doc page does not cover it).

## 4. Funder-based works vs institution lineage vs raw affiliation; coverage

### Takeaway
Three different, partially overlapping routes to "tied to company"; none is complete. Funder coverage is clearly partial (about a third of 2023 articles have any funder), so every "no tie found" must carry that caveat.

### Cited Findings
- OpenAlex does not observe funding directly; it reads funding mentions in work metadata (Crossref, funder grant records, other sources) and matches them to funders/awards — [Funders doc](https://help.openalex.org/data/funders/)
- [LIVE] Works with any funder (`funders.id:!null`): 2023: 1,980,450 of 10,893,698 works (18.2%; against 5,987,822 type:article = 33%); 2015: 1,107,886 of 10,153,935 (10.9%). `has_funder` is not a valid filter; use `funders.id:!null`. Counts drift over time.
- Studies (all from search snippets, not read in full): Crossref funder metadata incomplete, ~67% of an NWO-funded sample carried funding info — [Septentrio](https://septentrio.uit.no/index.php/SCS/article/view/6656); OpenAlex has lower funding coverage than Web of Science and Dimensions overall, sparser in parts of Africa and Central Asia — [arXiv 2603.24147](https://arxiv.org/pdf/2603.24147); a 2024 comparison reported OpenAlex finding less than half the records Dimensions did for an NSF search (second-hand, unverified); funder metadata was historically Crossref-only, Wellcome now ingested directly (unverified snippet) — [Rogue Scholar OpenAlex vs OpenAIRE](https://rogue-scholar.org/records/96nnw-cfy12)
- [LIVE] PMI comparison: funder works 977; institution-lineage works 985; overlap 247; raw-affiliation-string search 1,834. Institution links depend on OpenAlex mapping raw strings to institutions.
- [LIVE] Single work W1969670934 has `funders` = [Prostate Cancer Foundation, Philip Morris International] but `awards` = [] : award detail is often missing even when the funder is present.
- Funder record works_count (990) vs filter count (977) differ slightly [LIVE].
- List endpoints cut authorships at 100 per work; singleton returns all (loader.py comment, lines 219-221).

### Inferences
- Honest "no tie" scope: state route, filter string, date, and the denominator (share of the expert's works carrying any funder: two count calls, `authorships.author.id:A…` with and without `funders.id:!null`, about $0.0002 per expert).
- Treat raw-affiliation search as a low-confidence recall booster for human review, not as an automatic edge.
- Coverage varies by year and region; pre-2010 and Global-South works have less funder data.

### Gaps
- No official coverage percentage is published; the numbers above are my own live counts for 2015 and 2023 only.
- Did not read the Awards page.

## 5. Budget, limits, pricing (2026) and a case estimate

### Takeaway
A free key gives $1/day: about 10,000 list/filter calls OR 1,000 search calls; singleton lookups are free. A new case of ~1,000 funded works + ~30,000 topic works + ~95,000 author lookups costs about $0.15 and runs in minutes.

### Cited Findings
- Per 1,000 calls: singleton (ID or DOI) free; list+filter $0.10; search $1; semantic search $1; group_by $0.10 (list) / $1 (search); content download $10; rerank +$1 — [Example costs](https://help.openalex.org/access/example-costs/). Page does not list cursor paging or autocomplete costs; live `cost_usd` was 0.0 for autocomplete.
- Free account: API key with $1 of usage per day, no payment method; resets midnight UTC; prepaid in $1 steps, used after free budget; annual: Member $5,000 ($20/day), Member+ $10,000 ($100/day, daily snapshot) — [Pricing](https://help.openalex.org/access/pricing/)
- No key: shared $0.10/day per network IP; live notice: "shares one free daily budget ($0.10) with everyone on your network's IP address. Get a free key with its own $1/day" [LIVE]. Each response has `meta.cost_usd`: filter list 0.0001, search 0.001, autocomplete 0.0, empty search 0.0.
- Rate limits: 10 req/s no key, 30 req/s free key, 50 annual; semantic search 1 req/s; searches with more than 5 boolean operators 1/s (no key) or 5/s (free key); per_page max 100; 100 OR values per filter; `sample` max 10,000; basic paging stops at 10,000 results; cursor paging beyond that needs a key; headers `X-RateLimit-Limit/Remaining/Credits-Used/Reset`; `/rate-limit?api_key=` (unauthenticated -> "Authentication required" [LIVE]) — [Auth guide](https://help.openalex.org/guides/authentication)
- Snapshot: ~626M records per format; JSONL ~745 GB compressed (works ~615 GB), Parquet ~770 GB; public snapshot free, quarterly (second Wednesday of Jan/Apr/Jul/Oct), AWS CLI `--no-sign-request`; daily snapshot paid only — [Snapshot](https://help.openalex.org/access/snapshot/) (page showed the September 2026 release)
- Conflicting: casrai.org (July 2026) claims keys became mandatory in Feb 2026; contradicts official docs and the live notice, looks low quality, ignore — [casrai.org](https://casrai.org/news/openalex-api-keys-mandatory-usage-based-pricing-2026)
- Existing loader: `get()` retries 5x with backoff, fatal on 429 with daily/budget/credit/quota wording, API key as query param, per_page=100 cursor paging with 0.05 s sleep, 5-thread pool for 100-id chunks, cache in data/raw/*.jsonl (294 MB for Coca-Cola), `fetch_full_works` uses free singleton `works/{id}`; works_count lookups via `authors?filter=ids.openalex:A|A…` 100 per call — loader.py lines 77-228.

### Estimate (my arithmetic from the prices above; [INFER])
- Discovery searches: ~40 search calls = $0.04 (autocomplete free).
- Funded works: 1,000 / 100 = 10 list calls = $0.001.
- Topic works: 30,000 / 100 = 300 list calls = $0.03.
- Author works_count: 95,000 / 100 = 950 list calls = $0.095 (Coca-Cola scale).
- Per-expert extras: ~3-5 calls each, ~$0.002.
- Singleton full-works for >100-author papers: free.
- Total about $0.17, ~1,300 list calls plus ~40 searches; 10x under the 10,000-list-call cap, so ~5 cases per free-key day. Search cap (1,000/day) is not binding.
- Wall-clock: at 5 threads and ~5-15 req/s, 1,300 calls take ~2-4 minutes; the 300 cursor pages are sequential (~0.1-0.2 s each), 1-2 minutes. Estimate only; I did not read recorded timings from the Coca-Cola run.
- Snapshot (700+ GB) is not worthwhile for a single case.

### Inferences
- Binding constraints are the 10,000-result basic paging limit (cursor needs a key) and keeping search calls small, not cost.
- Because topic works are sampled, the graph's coverage statement must record the cap and sampling rule (as loader.py does).

### Gaps
- The call-count equivalent of the free budget is derived by me from price x $1/day.
- No official API changelog page found (help.openalex.org lists only /api/deprecations/); behaviour above is from live calls and may change.
- Did not test hitting the unauthenticated 429.

## 6. Deterministic vs judgement steps (server makes no LLM calls)

### Takeaway
Everything that fetches, counts and ranks is deterministic code returning candidates with evidence; every "which one is it" decision is made by Claude Code (client) and confirmed by the human, then written to a small per-case config the loader consumes.

### Cited Findings
- Name searches return loose multi-entity results (PM funder plus a front-organisation foundation; 16,059 Maria Garcias; topic autocomplete miss) — [LIVE] sections 1-3.
- OpenAlex gives no funder family links (flat) and incomplete institution links (PMI linked, Altria not) — [Funders doc](https://help.openalex.org/data/funders/), [LIVE].

### Inferences (proposed split)
Deterministic (MCP tools, no LLM):
1. `find_funders(q)`: `/funders?search` + `/autocomplete/funders`; id, name, ROR, country, description, works_count, funded-works count via `works?filter=funders.id:ID` (count only).
2. `find_institutions(q)`: `/institutions?search`, `display_name.search`; type, ROR, lineage/associated_institutions, works_count; optional `raw_affiliation_strings.search` count as recall check.
3. `find_topics(q)`: `/topics?search`, `/keywords?search`, counts for `topics.id`, `primary_topic.id`, and the phrase search, so the human sees download size up front.
4. `find_authors(name, affiliation?)`: `/autocomplete/authors` + `/authors?filter=display_name.search`; per candidate works_count, ORCID, last_known_institutions, topic overlap, funder-coverage share; status unique|ambiguous|not_found.
5. `build_case(config)`: download funded works (`funders.id:A|B`), institution works (`authorships.institutions.lineage:`), topic works by year with cap, author works, author works_count lookups; cache JSONL under data/raw/<case>/ with the header-params scheme from loader.py; write coverage stats.
Judgement (Claude Code + human):
- Which funder and institution IDs make up "the company", and their roles/tiers (parent, subsidiary, foundation, bottler-like; Altria vs PMI).
- Which topic IDs/keywords/phrase define the story topic.
- Which author candidate is the named expert; ask the journalist if ambiguous.
- Acceptable cap/sampling and the coverage-caveat wording.
- Record decisions in a per-case JSON config (`decided_by`, date) so reruns are deterministic and auditable.

### Gaps
- Not implemented or tested end to end; design inferred from API behaviour.

## Appendix: exact syntax confirmed live (2026-10-15, unauthenticated)
- `GET https://api.openalex.org/funders?search=philip%20morris&select=id,display_name,works_count,ids,country_code,description&per_page=8`
- `GET /institutions?search=philip%20morris&select=id,display_name,type,ror,works_count,country_code,lineage,associated_institutions`
- `GET /topics?search=lung%20cancer%20screening&select=id,display_name,works_count,subfield,keywords`; `GET /keywords?search=...`; `GET /subfields?search=pulmonary`
- `GET /autocomplete/{funders|authors|institutions|topics}?q=...` (free; topics multi-word missed)
- `GET /authors?filter=display_name.search:rajesh%20kumar,last_known_institutions.id:I...`
- `GET /works?filter=funders.id:F1|F2`; `authorships.institutions.lineage:I...`; `topics.id:T..`; `primary_topic.id:T..,publication_year:2021-2026`; `title_and_abstract.search:"phrase"`; `raw_affiliation_strings.search:"phrase"`; `funders.id:!null`; `authorships.author.id:A...`; `&group_by=publication_year`
- Invalid: `has_funder`, `authorships.raw_affiliation_strings.search`.
- Every response carries `meta.cost_usd`; meta also has `x_query` (an OQL version of the query).
