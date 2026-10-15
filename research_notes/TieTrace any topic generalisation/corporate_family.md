# Corporate-family candidate generation for an arbitrary company (TieTrace "target" orgs)

Probe date: 2026-10-15. "VERIFIED" = I ran the call or read the doc page this session. "INFERRED" = my reasoning. All live calls were light (a handful of GETs, polite User-Agent). .env not touched.

## 1. OpenAlex: do institution records expose lineage / parents for companies?

### Takeaway
OpenAlex lineage and associated_institutions are copied from ROR, so they only exist where ROR has parent/child links. The links exist for multinationals that ROR modelled by country (Pfizer, ExxonMobil, PMI, Nestle) and are absent for Coca-Cola, Altria and most acquired or unusual entities. They are never a complete family list, which confirms why COKE_FAMILY is hand-checked.

### Cited Findings
- Docs: `lineage` = the institution's own OpenAlex ID plus ancestors, taken from ROR's hierarchy (ROR parents, plus the successor of an institution ROR records as having exactly one successor). A split institution does not roll up. "Filtering lineage:<id> finds an institution and everything beneath or before it." — [OpenAlex institution object](https://help.openalex.org/api-entities/institutions/institution-object)
- Docs: `associated_institutions[].relationship` is one of parent, child, related, predecessor, successor. Data comes from ROR. It is a column only, not a filter. — same page
- Docs: `roles` lists the other entity roles of the same organisation (institution / funder / publisher), filterable by `roles.id`. — same page
- Docs: `works_count` on an institution covers only works affiliated with that record itself. To include child units, filter works on `authorships.institutions.lineage`. — same page
- VERIFIED, live `/institutions?search=`:
  - Pfizer (United States), I180857899: 19 `child` country records plus Wyeth as `predecessor`; the children's lineage = [self, I180857899].
  - ExxonMobil (United States), I923382979: children Qatar, Germany, Norway, Canada, UK and others, each with the parent in its lineage.
  - Philip Morris International (United States), I4210088192: children Switzerland I2800621710 and Germany I4210114941.
  - Nestle: only a stub "Nestle (Portugal)" I4405257486 came up under search "Nestle". Its parent is "Nestlé (Switzerland)" (a mis-encoded name), I4210112383, which did not appear in the search results.
  - Altria (United States), I4210158204: no associated institutions.
  - Coca Cola (United States), I1312732213: no associated institutions. The Japanese bottlers I4405274843 and I4405273583 have no parent link.
  - Searches for "Altria Client Services", "Philip Morris Products", "Coca-Cola Europacific", "Coca-Cola FEMSA", "Nestle Research" returned nothing. Swedish Match AB is I4405266907 and JUUL Labs is I4405267303; both have lineage = [self], no parent.
- VERIFIED lineage-filter semantics on `/works` count:
  - `authorships.institutions.lineage:I4210088192` (PMI US) = 985 works.
  - `authorships.institutions.id:I4210088192` = 139 works.
  - `lineage:I180857899` (Pfizer US) = 54,840, vs 41,344 for `.id`.
  - `lineage:I1312732213` (Coca-Cola) = 647, about equal to its own works_count of 649, so no uplift.
  - So lineage is a ROR-ancestry roll-up for each author-affiliation institution. It is only as good as the ROR parent links.
- VERIFIED funders (`/funders`):
  - PMI = F4320315260.
  - Foundation for a Smoke-Free World (FSFW) = F4320318650. OpenAlex's description is "front organization of tobacco corporation Philip Morris", which matches the Wikidata item description for Q106505816. That text is Wikidata-sourced and editorial.
  - Coca-Cola Foundation = F4320308599 and Coca-Cola = F4320307807 are separate funders (796 and 91 works) with different Crossref funder IDs (100005609 vs 100004367). BOTH carry the SAME ROR (00yc8qw13) and the same Wikidata Q3295867, and both have role "institution" = I1312732213.
  - Pfizer has separate funders for Pfizer, Pfizer Foundation (F4320332718, same ROR as Pfizer), Pfizer UK, Japan, Canada and others (18 hits on the search "Pfizer").
  - Takeaway: a ROR-based crosswalk collapses a foundation into its corporate parent. Keep funder IDs separate, with their own label.

### Inferences
- Usable as a seed layer only: take OpenAlex institution + `lineage` as candidates and mark them `source: openalex/ror`. For Coca-Cola-like cases (no ROR links) this layer returns nothing beyond the seed.
- Because the PMI count differs by about 7x between `.id` and `lineage`, a user-visible config should say which key (id vs lineage) a count used.
- The Nestle stub shows `search=` is unreliable. Resolve the seed via ROR ID or Wikidata Q, not by name search.

### Gaps
- I did not test whether `grants`/`funders` records in works carry a lineage-like roll-up. I believe not, since funders have no lineage field.
- I did not check Crossref Funder Registry parent/child links (funder hierarchy). Possible extra source; not probed.

## 2. ROR (Research Organization Registry)

### Takeaway
ROR v2 gives typed relationships (parent, child, related, predecessor, successor) and a clean crosswalk to OpenAlex, Wikidata and FundRef. Company coverage is thin and uneven (country-level subsidiary records for some companies, nothing for others). It is a good ID backbone, not a family source. Licence is CC0.

### Cited Findings
- VERIFIED, unauthenticated `GET https://api.ror.org/v2/organizations?query=...` and `/v2/organizations/{id}` worked with no client ID.
- ROR docs on limits:
  - 2,000 requests per 5 min with a `client-id` header; 50 per 5 min without. — [ROR client-ID docs via search](https://ror.readme.io/docs/client-id)
  - The ROR registration page says client-ID registration is paused and no rate limits are currently enforced. — [ror.org/api-client-id](https://ror.org/api-client-id)
  - The ReadMe docs say a client ID would be required "in Q3 of 2026". That date has passed and I saw no enforcement, so the status is unclear. Design for 50 requests per 5 min and cache.
- VERIFIED records:
  - Coca Cola (US) 00yc8qw13: types company+funder, relationships [], external IDs include fundref 100004367 and grid; no Wikidata ID.
  - PMI (US) 005rxfw29: 2 children (Germany, Switzerland), fundref 100014729.
  - Altria 04sme7s65: no relationships, Wikidata Q445007.
  - Pfizer 01xdqrp08: 20 relationships, last modified 2026-09-22.
  - FSFW 03p2hmv20: types funder+nonprofit, no relationship to PMI, fundref 100019021.
  - Search "Nestlé": 19 results, country records carry a `parent` link to Nestlé (Switzerland).
  - Most records had last_modified 2024-12-11, which suggests the company records are static.
- Licence: ROR data is CC0 (stated in ROR docs; INFERRED from memory, not re-fetched this session).

### Inferences
- ROR is the right ID hub: ROR -> OpenAlex institution (`ror` field, filterable) and ROR -> Wikidata/FundRef (`external_ids`).
- Treat ROR relationships as reliable but incomplete. They hold only subsidiaries someone chose to register as research-publishing orgs.

### Gaps
- No quantified ROR company coverage (e.g. share of Fortune 500). Not measured.

## 3. Wikidata

### Takeaway
Wikidata has the broadest cross-ID coverage (ROR via P6782, OpenAlex via P10283) but its ownership properties are very noisy. P127 "owned by" is mostly brands, products and patents. A class filter and a human pass are mandatory. Licence is CC0.

### Cited Findings
- Licence: structured data is public domain under CC0. — [Wikidata:Licensing](https://www.wikidata.org/wiki/Wikidata:Licensing)
- Query service limits: 60 s query timeout; 60 s of processing per 60 s per client (User-Agent+IP); 5 parallel queries per IP; HTTP 429 with Retry-After; a good User-Agent is required or you can be blocked. — [WDQS user manual](https://www.mediawiki.org/wiki/Wikidata_Query_Service/User_Manual)
- VERIFIED, one SPARQL per company, union of `wd:X wdt:P355 ?x` (subsidiary), `?x wdt:P749 wd:X` (has parent), `?x wdt:P127 wd:X` (owned by) and `wd:X wdt:P1830 ?x` (owner of), with no class filter. Rows returned (distinct entities x relation):
  - Coca-Cola (Q3295867): 133 rows (90 P127, 26 P749, 17 P355). Noise: Thums Up, Schweppes, Dasani, Minute Maid, Monster Beverage, "World of Coca-Cola" (a museum), a "Baltimore Branch Factory", Weintraub Entertainment Group (historical), plus unlabeled Q-IDs. Real family members present: Coca-Cola HBC, Coca-Cola Europacific Partners, Coca-Cola FEMSA, Coca-Cola Consolidated, Coca-Cola Beverages Africa, Coca-Cola Bottlers Japan Holdings, Coca-Cola Amatil, Panamco. No ROR ID on any returned item; one had an OpenAlex ID (C-type, i.e. concept, not an institution).
  - PMI (Q1852556): 35 rows. Includes Swedish Match (ROR 00a6vpp92), PMI Switzerland (03z9zz970), PMI Germany (022jdjx35), Sampoerna, PMFTC, Papastratos, Vectura Group, and brands (Zyn, Iqos, Virginia Slims, VEEV). It also lists Foundation for a Smoke-Free World (ROR 03p2hmv20) via P749 (parent), i.e. Wikidata asserts "PMI is parent of FSFW".
  - Altria (Q445007): 174 rows, almost all P127 and mostly PATENT items ("Aerosol-generating device..."). Real ones: Juul (ROR 05fcgnx79) and Anheuser-Busch InBev (an ownership stake in the other direction).
  - Pfizer (Q206921): 129 rows (79 P127: drugs, patents, vaccines). Real: Wyeth (035j24j42), Pfizer UK (04x4v8p40), Pfizer Japan (05pm71w80), Zoetis, Coty (both mentioned as subsidiaries; historical).
  - Nestle (Q160746): 321 rows (248 P127: brands such as Kit Kat, Nespresso, Maggi). Real: L'Oreal (stake, ROR 00nb3j622), Nestle US (0368qtc02), Nestle Canada, Alcon, Cereal Partners Worldwide.
- The class restriction I did not complete: it is feasible to require `?x wdt:P31/wdt:P279* wd:Q4830453` (business) or Q43229 (organization). I wrote it but did not run it, so noise reduction is not measured.

### Inferences
- Of 133 raw Coca-Cola rows, I'd guess about 15 to 25 are plausible organisations (bottlers, holdings, historical subsidiaries) and the rest are brands, products, places or patents. This is an estimate from reading the list, not a count.
- Wikidata is a candidate source, not evidence for a "target" flag. Every claim carries a statement-level reference at best; many have none.
- Direction issues: P127 in the "x owned by company" direction includes minority/historical stakes (Coca-Cola owned a stake in the entity; or the company is owned by someone else). The relationship type (majority-owned / minority / historical) is not preserved in these properties alone; qualifiers (P1107 proportion, P580/P582 dates) must be read.

### Gaps
- Did not read SPARQL results with qualifiers (stake %, end dates). Needed before using Wikidata ownership to mark "target".

## 4. SEC EDGAR Exhibit 21, GLEIF, OpenCorporates

### Takeaway
Exhibit 21 is the best auditable list of legally controlled subsidiaries for US filers (a primary document with a filing date), and it is far longer than GLEIF/ROR. It does not list independent bottlers or equity-method affiliates. GLEIF is global but sparse because it depends on parents reporting. OpenCorporates is fine for lookup but licence-constrained.

### Cited Findings
- EDGAR fair access: max 10 requests/s; declare a User-Agent with a company name and contact email; JSON APIs on data.sec.gov. — [SEC accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
- VERIFIED fetch path: `https://data.sec.gov/submissions/CIK##########.json` -> latest 10-K accession -> `https://www.sec.gov/Archives/edgar/data/<cik>/<accession-no-dashes>/index.json` -> exhibit file. The file name is NOT standard: PMI `pm-ex21_123125xq4.htm` (FY2025 10-K filed 2026-02-06); Coca-Cola `a20251231exhibit211.htm` (filed 2026-02-20; guessing `...exhibit21.htm` gave a NoSuchKey error). Pick the file by name regex `ex-?21`, or by the EX-21 document type in the filing index.
- VERIFIED size/quality:
  - PMI Exhibit 21: about 240 legal entities (485 text lines, name + jurisdiction pairs), alphabetical, e.g. "ABAL HERMANOS S.A. Uruguay", "AO PHILIP MORRIS IZHORA", "ASPEYA..". It is HTML tables, so a simple tag-strip works.
  - Coca-Cola Exhibit 21.1: about 38 entities (87 lines), e.g. Atlantic Industries (Cayman), BA Sports Nutrition LLC, Barlan Inc, Beverage Financial Centre, CCHBC Grouping Inc. Independent bottlers (CCEP, FEMSA, CCH) are absent since they are not subsidiaries.
  - Exhibit 21 gives legal names + jurisdiction only. There are no IDs, so matching to OpenAlex needs name normalisation (and many shell/finance entities never appear in OpenAlex).
- GLEIF API (VERIFIED): `GET https://api.gleif.org/api/v1/lei-records?filter[entity.legalName]=...` then `/lei-records/{lei}/ultimate-children` and `/direct-children` (need `curl -g` for brackets). Golden copy date in response 2026-10-15. Results:
  - PMI Inc. (LEI HL3H1H2BGXWVG3BSWR90): 23 ultimate children.
  - The Coca-Cola Company (LEI UWJKFUJFZ02DKWI3RY53): 13 ultimate children (Coca-Cola Export Corp, Coca-Cola Beverages Africa, Hindustan Coca-Cola, Innocent, Costa, etc.).
  - Name collision: the first hit for "COCA-COLA COMPANY" is an Australian entity of the same name (8945007BLGJ517U74C54); the real US parent is UWJKFUJFZ02DKWI3RY53.
- GLEIF Level 2: direct and ultimate accounting consolidating parents, only where the parent has an LEI; separate reporting-exception file (about 6.2M exception records vs about 673k relationship records) covers "no parent", "parent has no LEI", "non-public". Not beneficial ownership. — [GLEIF Level 2](https://www.gleif.org/en/lei-data/access-and-use-lei-data/level-2-data-who-owns-whom)
- GLEIF licence: data "provided under the CC0 licence"; terms forbid claiming IP, forbid bypassing the official access service, forbid implying endorsement; no warranty; Swiss law. — [GLEIF terms](https://www.gleif.org/en/meta/lei-data-terms-of-use)
- OpenCorporates: API key required; free access for open-data projects under share-alike/attribution (ODbL); journalists, NGOs and academics can apply for free access; commercial use needs a paid plan. — [OpenCorporates API reference](https://api.opencorporates.com/documentation/API-Reference), [Bellingcat guide](https://www.bellingcat.com/resources/2023/08/24/following-the-money-a-beginners-guide-to-using-the-opencorporates-api/). Third-party free-tier numbers (about 50/day) are unconfirmed.

### Inferences
- Ex.21 has more entities than any other source (240 vs 23 GLEIF vs 2 ROR for PMI) but most are shell/finance entities with no research output. So the human step is to tick the entities that matter, not to check each.
- Ex.21 + GLEIF cover only controlled subsidiaries. Bottlers and franchise partners (Coca-Cola's tier A2 type) come from Wikidata, press and the company's own 10-K text, and need human sourcing.
- Skip OpenCorporates for the hackathon: key + licence friction, little gain over Ex.21 + GLEIF.
- Non-US companies (Nestle, etc.): no Exhibit 21 (Nestle files no 10-K). Use GLEIF + Wikidata + the company's annual-report subsidiary list, fetched via WebFetch and attributed with URL.

### Gaps
- I did not parse the Ex.21 files programmatically beyond counting lines, so the counts are approximate (about ±5%).
- Did not verify GLEIF rate limits (docs not read).
- Did not check whether older Ex.21 (pre-2001, or filed as plain text) parse as easily.

## 5. Foundations, funding vehicles, trade groups: detection and labelling

### Takeaway
These entities should be added as separate typed nodes with a relationship label like `funded_by` / `grant`, never folded into the company's ID set silently. The tool should state "funded by X per source" and leave independence to the reader. Wikidata and OpenAlex already attach editorial language (e.g. "front organization"), which TieTrace should not repeat as its own claim.

### Cited Findings
- FSFW: OpenAlex funder F4320318650 (100 works); Wikidata Q106505816 describes it as a "front organization of tobacco corporation Philip Morris"; Wikidata lists PMI as parent via P749. ROR has FSFW as a standalone funder + nonprofit with no relationship to PMI. VERIFIED above.
- Public-health criticism and disclosure evidence:
  - WHO statement on the Philip Morris-funded Foundation. — [WHO](https://www.who.int/news/item/28-09-2017-who-statement-on-philip-morris-funded-foundation-for-a-smoke-free-world)
  - Johns Hopkins public health school will not accept FSFW funding. — [JHU](https://publichealth.jhu.edu/2018/statement-on-the-foundation-for-a-smoke-free-world)
  - FSFW says it is independent, critics say PMI is the sole funder; PMI planned about USD 80M a year over 12 years (advocacy-leaning sources). — search summary of [Truth Initiative report](https://truthinitiative.org/research-resources/tobacco-industry-marketing/new-report-tobacco-industry-funded-group-obscures-its) and [document analysis, PMC11228203](https://pmc.ncbi.nlm.nih.gov/articles/PMC11228203/)
  - Two-thirds of authors with tobacco-industry funding gave incomplete or no COI declarations (as summarised in search results; I did not open the study).
- Coca-Cola: Global Energy Balance Network (GEBN) was seeded with USD 1.5M from Coca-Cola, disclosed through open-records requests, and was discontinued in Nov 2015. — [CNBC 2015](https://www.cnbc.com/2015/08/10/coca-cola-funds-scientists-who-shift-blame-for-obesity-away-from-bad-diets.html), [Wikipedia GEBN](https://en.wikipedia.org/wiki/Global_Energy_Balance_Network). ILSI is an industry-funded science nonprofit with Coca-Cola among its contributors, per advocacy reports based on tax records (not verified against primary filings). — [US Right to Know](https://usrtk.org/pesticides/ilsi-is-a-food-industry-lobby-group/), [Greenhalgh, Inside ILSI](https://susan-greenhalgh.com/wp-content/uploads/2024/04/Inside-ILSI-2021-JHPPL.pdf)
- OpenAlex: "Coca-Cola Foundation" has 796 works vs "Coca-Cola" 91 works, so foundation grants are the larger signal for Coca-Cola. VERIFIED above.

### Inferences
- Suggested edge types (labels, not assertions):
  - `PARENT_OF rel=subsidiary|bottler` (legal control or franchise)
  - `FUNDED_BY rel=foundation|front_vehicle_alleged|trade_association` (money flow only)
  - Output wording: "this funder is described in <source URL> as ...", never TieTrace's own judgement.
- Candidate detection rules (deterministic):
  - the same ROR/Wikidata ID as the company but a distinct funder ID (the Coca-Cola Foundation pattern);
  - funder or org names containing the company name or the family brand + "Foundation|Institute|Fund|Trust|Council";
  - Wikidata P749/P127 to the company where the instance-of is nonprofit/foundation (this is how FSFW shows up);
  - Crossref/OpenAlex funder descriptions that mention the company.
- Trade associations and science fronts (ILSI, American Beverage Association, GEBN) have no formal link to the company in ROR, Wikidata, Ex.21 or GLEIF. They are found only by documents (Tobacco Tactics, UCSF Industry Documents, investigative reports). Flag as `needs_human_source`, and give a manual-add field in the config.
- "Independent" and "conflicted" are banned output words in the project. The same discipline applies to labels such as "front group": quote them only as a source's phrase with the URL.

### Gaps
- I did not find a systematic dataset of industry-funded vehicles across sectors (tobacco, pharma, food). Tobacco Tactics (University of Bath) and UCSF Industry Documents Library are the likely sources; I did not open them this session.
- Did not check pharma examples (Pfizer Foundation is a funder in OpenAlex but its role is not examined) or ExxonMobil-linked vehicles.

## 6. Effort estimate and minimal human approval step

### Takeaway
A generator can produce about 30-300 raw candidates per company, but only 5-30 matter for OpenAlex (institution IDs that actually have affiliated works, plus funder IDs). An hour of human checking is feasible if the table is sorted by evidence strength and OpenAlex works counts, and the human only handles the items in the "has OpenAlex ID" bucket plus the flagged foundations.

### Cited Findings
- Raw counts from my probes: Wikidata unfiltered 35 (PMI), 129 (Pfizer), 133 (Coca-Cola), 174 (Altria), 321 (Nestle); Ex.21 about 240 (PMI) and about 38 (Coca-Cola); GLEIF 23 (PMI) and 13 (Coca-Cola); ROR relationships 2 (PMI), 20 (Pfizer), 0 (Coca-Cola, Altria). All VERIFIED above.
- Funder side (OpenAlex search): Coca-Cola 2 funder records; PMI/FSFW 2; Pfizer 18 (country entities and a foundation).

### Inferences
- Proposed pipeline (all deterministic code; Claude Code only reads PDFs/web pages for the long tail):
  1. Resolve the seed: ROR search for the company name, Wikidata Q from ROR external_ids, LEI from GLEIF, CIK from the SEC (human confirms the seed in one step; GLEIF name collision shows why).
  2. Collect: ROR relationships; Wikidata P355/P749/P127 restricted to organisation classes; GLEIF ultimate-children; Ex.21 names (US filers); OpenAlex institution + funder search by company and Ex.21 names (fuzzy match, normalised legal names).
  3. Crosswalk to OpenAlex IDs: first by ROR ID or Wikidata ID (P6782/P10283), then by normalised name with country, flagged `name_match`.
  4. Drop candidates with no OpenAlex institution/funder ID (record them as `no_openalex_record`, which supports honest "scope" text).
  5. Write `candidates.json`: each row has name, oa_id, kind (institution|funder), proposed rel (subsidiary|bottler|affiliate|foundation), sources[] with URLs and retrieval date, works_count, status `proposed`.
  6. Human approval: Claude Code prints a table (sorted by works_count, sources as short links); the human edits the `status` field to approved|rejected and fills `rel` for bottlers/foundations, then commits the JSON. The loader reads only `approved` rows, so COKE_FAMILY becomes a config file, not code.
- Expected human load (estimate, unmeasured): for a company like PMI, about 15-40 rows with OpenAlex IDs; Coca-Cola about 10-20 (bottlers + 2 funders + foundation). Review time per row about 1-2 min, so under an hour.
- Failure modes: name collisions (the Australian "Coca-Cola Company"; "Philip Morris" vs "Altria" vs old names, with Altria being the US tobacco spin-off with a separate ROR and Wikidata ID); Wikidata P127 noise; OpenAlex one-record-per-country splits; stale or mis-encoded names (Nestle); ROR record updates lagging real acquisitions (Swedish Match is under PMI in Wikidata, with no parent link in OpenAlex); bottlers that are legally independent (do not flag as "owned").
- Name normalisation (inferred): casefold, strip accents, drop legal suffixes (Inc, Ltd, GmbH, S.A., AG, Co.), drop a trailing "(Country)", and match on token sets plus a country check. Always show the match type in the table.

### Gaps
- No empirical measure of how many Ex.21 names resolve to OpenAlex institutions; I did not run the name matching.
- No time-study of human review; the one-hour estimate is a guess.
- Did not examine second-tier cases (private companies, non-US filers with no Ex.21, state-owned companies).
