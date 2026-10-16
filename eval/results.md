# TieTrace evaluation results (checkpoint 4: E3, E7, E1, E2)

*Last updated by Abdul, Oct 17 2026: added E4 (max flow vs aggregate, `eval/e4.py`). Before that, Abdul, Oct 16: added E6 (mega-paper ablation, `eval/e6.py`). Before that, Aaryan, Oct 16: 5f funder share now says "topic works" and gives the Meta counts. Before that: added the checkpoint 5f end-to-end run (Philip Morris × lung cancer). Earlier: Abdul, Oct 16: Reviewer fixes (anonymised control, placebo-spread headline interval for E1, paper counts for E3, banned-word cleanup); first full run of `eval/e3_e7.py`, `eval/e1.py`, `eval/e2.py` on `research:obesity-coke` (data version openalex@2026-10-15). The Reviewer has not checked this yet.*

Re-run: `FALKORDB_PORT=6380 uv run python eval/e3_e7.py` (7 s), `eval/e1.py` (82 s), `eval/e2.py` (20 s). All seeds are fixed (20261016), so the numbers repeat.
Pass rules are copied from `tietrace-v2-redesign.md` section 7 and were not changed after seeing results. The score formula is the fixed one (section 4).

| Test | Result |
|---|---|
| E7 coverage | reported (see below) |
| E3 known case | **4 of 4 publicly confirmed GEBN leaders found as Tier A with DOIs; the "five" could not be confirmed (4 names found in public sources), so the rule "all five" is not strictly testable** |
| E1 placebo lift | **FAIL** (Tier B lift 0.96x, interval 0.84-1.09) |
| E2 temporal hold-out | **FAIL** (119 positives; weighted score does not beat volume or the plain count) |

**What this means for the README.** Tier A (explicit funding or affiliation records with DOIs) works and is the headline. The README must NOT say that Tier B or the weighted score "predicts" future ties, or that Tier B or C links are more common around Tier-A people than by chance. Tiers B and C stay as labelled context in the evidence viewer. This is the "E1 and E2 both fail" branch of the plan: keep the evidence-viewer framing and cut the Tier B claims.

---

## E7: coverage and load scope

**Method.** Recounted from the graph with separate Cypher (`eval/e3_e7.py`), then compared with the numbers the loader stored in the `Meta` node.

| Quantity | Graph recount | Meta (loader) |
|---|---|---|
| Topic works (have an `ABOUT` edge) | 30,140 | 30,140 |
| ...with funder data (any funder) | 16,965 = **56.3%** | 16,965 |
| ...with funder or affiliation data | 29,122 = **96.6%** | 29,122 |
| ...affiliation data only / neither | 12,157 / 1,018 | |
| Junk records (F1000 reports, corrections, retraction notices) | 19 | 19 |
| Index / Front Matter records (`non_paper`) | 4 | 4 |
| Untitled records | 11 | 11 |
| Topic works with a cut author list (100 listed) | 25 | 25 |
| Possible duplicate groups (same title and year) | 55 groups, 116 records | 55 / 116 |
| Tier B/C links that include a leftover duplicate | not recounted | 204 |
| Exact duplicates the loader removed | | 39 |

Records in the graph: 31,291 works (29,867 from the topic load, 817 from the funded load, 607 from the family-affiliation load). Tier A people: 3,035.

**Load scope.** All works funded by the Coca-Cola company or foundation (any topic, any year) and all works with a Coca-Cola-family affiliation (any topic) are loaded in full. **The topic part is a per-year SAMPLE, not the whole field:** the 1,764 most-cited works per year for 2010-2026 whose primary topic is T10010 or T10352. Co-authors' other works are not loaded. Loaded / total per year (Meta, primary topic): 2010 1760/11286, 2011 1766/12402, 2012 1762/13738, 2013 1768/14340, 2014 1771/15290, 2015 1774/16206, 2016 1769/16364, 2017 1770/16273, 2018 1762/16116, 2019 1772/16615, 2020 1772/17338, 2021 1766/15921, 2022 1763/14463, 2023 1763/14365, 2024 1763/15510, 2025 1765/17757, 2026 1761/16705. In total 30,027 of 260,689 (11.5%). The sample is chosen by citation count, so it favours well-cited papers.

Coverage by year ranges from 45% (2010) to 64% (2019) for funder data, and from 95.5% to 98.8% for funder-or-affiliation data. 2026 is partial (funder 35.2%, funder-or-affiliation 86.7%).

**What the README may say.** "56.3% of the loaded topic works have funder data, 96.6% have funder or affiliation data (affiliation data says little about funding)". Every "no tie found" must carry this and the sample note. It may NOT say the graph covers the field: it holds about 11.5% of the topic works.

---

## E3: known case (GEBN)

**Pass rule.** The five GEBN scientists appear as Tier A with DOIs; all five must be found.

**Who the scientists are.** Sources I could open: Public Health Nutrition (Cambridge), "Evaluating Coca-Cola's attempts to influence public health 'in their own words'" (https://www.cambridge.org/core/journals/public-health-nutrition/article/evaluating-cocacolas-attempts-to-influence-public-health-in-their-own-words-analysis-of-cocacola-emails-with-public-health-academics-leading-the-global-energy-balance-network/03A12A2379B132AFBDBE7A462ECB4041) names four GEBN leaders: James Hill (president), Steven Blair, Gregory Hand and John Peters (vice-presidents). English Wikipedia ("Global Energy Balance Network") lists Hill, Blair and Hand. The 2015 reporting (New York Times, republished at CNBC: https://www.cnbc.com/2015/08/10/coca-cola-funds-scientists-who-shift-blame-for-obesity-away-from-bad-diets.html) says Coca-Cola funded Blair and Hand since 2008 and that two universities disclosed gifts that started the group; I could not open that page directly, so this comes from search summaries. **I found no public source for a fifth scientist.** One other author (kept anonymous here, "Control #1", OpenAlex id A5088704695) is not named in any public GEBN source I could open; the table shows that person only as a control and does not count them.

**Method.** `resolve_experts` for each name, then `tie_report` with `public_case=true` on the profile chosen below.

| Scientist | OpenAlex id (chosen) | Tier | Evidence |
|---|---|---|---|
| James O. Hill | A5031318756 | **A2** | 7 evidence items on 7 papers, all with DOIs: Coca-Cola **Foundation** grants (2014-2020), 4 on-topic. No company-funded work in the data. First: 10.1017/s0007114520001798 |
| Steven N. Blair | A5105003630 | **A1** | 71 evidence items on 68 papers (a paper funded by both funders counts twice), all with DOIs (6 A1 company-funded or affiliation, 65 A2), 59 on-topic. First: 10.1016/j.pmedr.2018.07.013 |
| Gregory A. Hand | A5046953968 | **A1** | 26 evidence items on 25 papers, all with DOIs (3 A1, 23 A2), 20 on-topic. First: 10.1016/j.pmedr.2018.07.013 |
| John C. Peters | A5045252457 | **A2** | 1 evidence item on 1 paper: Coca-Cola Foundation grant, 10.1002/oby.20737 (2014), off-topic |
| *Control #1* (not named in any source) | A5088704695 | B | no Tier A evidence; Tier B only (not a finding) |

**Result: 4 of 4 publicly confirmed leaders are Tier A with DOIs. The "all five" rule cannot be fully tested because only four names could be confirmed. Status: PASS for the four, five-name rule UNVERIFIED.** The fifth name must be confirmed from a public source (for example the 2015 New York Times article) before the README says "five".

**Wording that is supported.** "OpenAlex records these authors on papers funded by the Coca-Cola company or foundation, consistent with what journalists reported in 2015." Hill and Peters appear only through the **foundation** (Tier A2, label shown); the data does not show company-funded work for them. The tool did not reproduce the investigation: the 2015 reports concern gifts to universities, which OpenAlex funder fields do not record.

**Split profiles (hand check).**
- `resolve_experts("Gregory A. Hand")` returned AMBIGUOUS with two ids: A5046953968 (34 loaded works, last known Wichita State, Tier A1) and A5109223784 (one 2014 paper, University of South Carolina, 78 lifetime works). I chose A5046953968 because it holds the funded papers listed above. The other record's one loaded paper (10.1055/s-0034-1385878, 2014) shares 4 of its 5 co-authors with the chosen profile, so it is very likely the same person; it is **not** Tier A and no merge has been applied (that needs a `PROFILE_MERGE` verdict).
- Blair: the main profile A5105003630 also has two split records, "Steve N. Blair" (A5112298044; one 2014 paper, 3 of 4 co-authors shared with the main profile) and "S. N. Blair" (A5087575650; one 2014 paper, 0 of 5 shared, University of South Carolina). None has an ORCID. The first looks like the same person, the second cannot be confirmed from the loaded data. Neither is Tier A. Because the main profiles are already Tier A, the split does not change the E3 outcome; it does mean a reporter checking Blair under another spelling would see "no Tier A evidence", which is a known profile-split gap (E5/E8).
- Another record, "James Hill" (A5143596507, one 2026 paper, University of Alabama at Birmingham) is Tier A2 by a foundation grant; I did not merge it with James O. Hill (no basis).

Names are shown here only because this is the documented public case. Live-case experts stay as "Expert #n".

---

## E1: placebo lift (Tier B)

**Pass rule.** Tier B lift >= 1.5x AND the 95% interval excludes 1.0.

**Method actually run (`eval/e1.py`).**
- Population U = 81,373 people who authored a real topic paper, minus the 3,035 Tier A people and the company staff. A person is Tier B when they have a `COAUTH_B` link (2+ recent small papers together: since 2021, 25 or fewer authors) to a member of the Tier A set.
- Real rate = Tier B share of U relative to the Tier A set.
- Placebo circle = 3,035 random members of U (same size as the Tier A set), with the same number from each works_count band (bands fixed in advance: under 10, 10-24, 25-49, 50-99, 100-199, 200-499, 500-999, 1000-1999, 2000+). 100 circles, seed 20261016. A circle member is never a Tier A person. The placebo rate for a circle = Tier B share of U without the circle, relative to the circle, with the same filters.
- Lift = real rate / mean placebo rate.
- **Headline interval:** the real rate divided by each of the 100 circle rates gives 100 lifts; the 2.5th and 97.5th percentiles of those are the interval. Why this one: the real uncertainty is which 3,035 people and which random circles we happened to draw, and this spread shows exactly that. The Tier A set itself is treated as fixed (there is only one). It is wider than the bootstrap below, which only reflects which experts were sampled.
- **Secondary interval (kept for reference only):** bootstrap, 1,000 replicates. In each replicate every expert is counted 0, 1, 2... times at random (on average once, which is resampling experts with replacement; the same counts are used for the real group and for every circle, so the comparison stays paired), and the 100 circles are re-picked with replacement. The 2.5th and 97.5th percentiles of the lifts form the interval. This captures the chance in which experts were sampled and in which circles were drawn. **Limits:** experts who co-author are correlated, so this interval is too narrow; the Tier A set is treated as fixed; and the number of circle members inside a resample is replaced by its expectation. As a distribution-free check, the real rate is also compared with the 100 placebo rates directly.

**Numbers.**

| | Real | Placebo mean (sd; min-max) | Lift (95% interval) |
|---|---|---|---|
| **Tier B (primary)** | 1,299 / 81,373 = 1.60% | 1.67% (0.11; 1.40-1.94) | **0.96x (0.84-1.09)** |
| Tier C, 2-3 hops (context only, no pass rule) | 2,070 = 2.54% | 3.33% (0.09; 3.04-3.57) | 0.76x (0.73-0.81) |

Interval = spread of the per-circle lifts across the 100 circles. The secondary expert-resampling bootstrap gives 0.91-1.01 for Tier B (0.74-0.79 for Tier C); it is too narrow and is not used for the verdict. 71 of the 100 placebo circles have a Tier B rate at or above the real rate.

**Result: FAIL.** The lift is below 1.5 and the headline interval (0.84-1.09) includes 1.0. Being linked to Tier A people through repeated recent collaboration is no more common than around a random, works_count-matched group. Tier C is even lower than chance (0.76x), mostly because Tier A people are less active in recent small papers than random topic authors (the activity-matched check below, 1.10x for Tier B, covers this).

**Sensitivity checks (not part of the pass rule, reported because they could change the story).**
- Count only links that rest on 2+ topic papers (Tier A people also have many non-topic papers loaded, placebo people do not): Tier B lift 0.94x (0.83-1.06); Tier C 0.77x.
- Match on works_count band AND "has a recent small paper in the loaded data" (only 1,001 of 3,035 Tier A people do, against 49% of U): Tier B lift **1.10x (0.96-1.28)**; Tier C 0.77x. That is not clearly above 1.0 and far below 1.5, so it would still fail.
- The lift depends on how many Tier A people are used as sources and which ones (real neighbours cluster in research groups). With a random half of the Tier A people as sources (1,517) I get 0.98x; the Reviewer saw about 1.13 with another half. Both are far below 1.5.

**Separate check.** A separate Cypher count of non-Tier-A people with a `COAUTH_B` link to a Tier-A person gave 1,309 (1,299 of them are topic authors), matching the 1,299 above.

**What the README may say.** "We tested Tier B against matched random groups; the lift was 0.96x, so Tier B is not a signal of unusual closeness. Tier B and C are shown as labelled context only." It may not use Tier B or C counts as evidence that the network around Tier-A people is dense.

**Not verified.** The result depends on the loaded sample (top-cited topic works per year). A different sample could give a different rate, and the placebo and real groups are differently loaded (Tier A people have all their funded works loaded; others do not), which the first sensitivity partly addresses.

---

## E2: temporal hold-out

**Pass rule.** Weighted Tier B beats both the volume baseline and the plain-count baseline by >= 0.05 AUC; at least 30 positives, else UNDERPOWERED.

**Method actually run (`eval/e2.py`).**
- **Cut year Y = 2018**, the redesign's own example; it leaves 5 full years (2019-2023) of later data and over 100 positives. I did not search for a better year; other years are shown as sensitivity.
- Everything is rebuilt from the graph's raw data and **only works with year <= Y** feed the predictors. Tier-A-at-Y = people with Tier A evidence (company or foundation funded work, or Coca-Cola-family affiliation on a work) from works up to Y, with the same rules as `research._tier_a`. The `COAUTH_B` fields are NOT used (they are computed for 2021 onwards); co-author weights are recomputed from `AUTHORED` and `Work.year`. Check: with no year cut the rebuild gives exactly the 3,035 Tier A people with the same A1/A2 labels as `research._tier_a`.
- Experts at Y = authors of a topic paper in year <= Y who are not Tier-A-at-Y: 38,530. **Positive = first Tier A evidence from a work in 2019-2023** (any of A1/A2, since A2 is also Tier A): 119. Negatives: 38,411.
- Predictors: **volume** = loaded papers up to Y; **direct-only** = already having Tier A evidence at Y, which is always 0 here because experts are by definition not Tier A at Y, so it is a constant (AUC 0.500) and carries no information; **plain count** = number of distinct Tier-A-at-Y co-authors, in two forms decided before running (all papers up to Y, and "filtered": same window and 2-25 authors as the score); **weighted score** = sum of 1/(n_authors-1) over papers with 2-25 authors in the window **Y-4..Y** (the prompt's window; the shipped 2021-2026 is six calendar years, and a Y-5..Y variant is shown below) for each Tier-A-at-Y co-author. Tier weight is 0 for B/C so the formula reduces to this sum.
- A pass requires beating volume, plain(all) and plain(filtered), each by 0.05. This reading of "the plain-count baseline" (all three) was fixed before the run.
- AUC by rank-sum with ties counted half. Bootstrap (500 replicates) resamples positives and negatives with replacement and recomputes all AUCs on the same resample.

**Numbers (Y = 2018, window 2014-2018).**

| Predictor | AUC |
|---|---|
| Volume (papers up to Y) | 0.720 |
| Direct-only | 0.500 (constant) |
| Plain count, all Tier-A co-authors | 0.749 |
| Plain count, same window and size filter | 0.712 |
| **Weighted score** | **0.716** |
| Tier first (B), then score | 0.715 |
| Volume by today's lifetime works_count (uses information from after Y, shown for reference) | 0.699 |

Differences with 95% bootstrap intervals: weighted - volume **-0.004** [-0.054, +0.048]; weighted - plain(all) **-0.033** [-0.065, -0.002]; weighted - plain(filtered) **+0.004** [+0.001, +0.007].

**Result: FAIL.** 119 positives (enough power). The weighted score is about equal to the volume baseline and below the unfiltered plain count; it beats the filtered count by only 0.004. None of the margins reaches 0.05.

**Sensitivity (no pass rule).**
- Window Y-5..Y: weighted 0.725 vs volume 0.720 (+0.005), plain(all) 0.749 (-0.025), plain(filtered) 0.721 (+0.004).
- Other cut years (positives, weighted - volume, - plain(all)): 2016 (149, +0.057, -0.010), 2017 (134, +0.039, -0.024), 2019 (90, +0.014, -0.006). The margin over volume reaches 0.05 only for Y = 2016, and the plain-count baseline is never beaten.
- Target = company-funded or non-bottler affiliation evidence only (A1) at Y = 2018: 12 positives, **UNDERPOWERED** (weighted 0.751 vs volume 0.638, not interpretable).
- Supplementary "re-link" population (all 39,172 topic authors at 2018, including 642 who are already Tier A; positive = new Tier A evidence in 2019-2023; 293 positives): volume 0.826, direct-only (tier weight) 0.792, plain count (filtered) 0.877, full score 0.880. Prior ties help once people who are already Tier A are included, but a simple count of Tier-A co-authors does as well as the weighted score (+0.003).

**What the README may say.** "Prior co-authorship with Tier-A people predicts later Tier A links somewhat better than chance (AUC about 0.72-0.75), but no better than how much a person publishes, and the weighted score adds nothing over a plain count. We therefore do not claim the score predicts." It must NOT say the score predicts or ranks risk.

**Data limits and bias.**
- Topic works are a per-year sample of the top-cited papers, chosen by today's citation counts (which carry some information from after Y). The volume baseline counts only loaded papers.
- Funded and family works are loaded in full, so positives (new Tier A evidence) are complete for the funded works but experts' histories on non-funded papers are incomplete. Positives come almost entirely from funded or affiliation records, not from the topic sample.
- Many positives are co-authors of one funded paper (a research group), so positives are correlated; the bootstrap treats persons as separate draws, which makes the intervals too narrow.
- "Direct-only" is degenerate in this design (see above).
- The bottler affiliations and foundation grants count as Tier A2 targets, as in the shipped tier rules.

---

## E6: mega-paper ablation (Tier A and Tier B, with vs without the 25-author cap)

*Run by Abdul, Oct 16 (`FALKORDB_PORT=6381 uv run python eval/e6.py`, 3 s, read-only on `research:obesity-coke`, openalex@2026-10-15). Reviewed (round 1 findings fixed); the recount in `tests/test_eval.py` is the separate recount. All four suites passed on Oct 16.*

Rule (fixed before running, redesign section 7: report the share, keep the cap). The cap is `Meta.small_paper` = 25 authors. A larger paper is "large". With the cap, a large company-funded or affiliation paper gives only A2, and a large paper does not count towards a Tier B link (2+ recent papers, 2021 onwards). Without the cap both count in full. Tier A membership (A1 or A2) does not depend on the cap, only the A1/A2 split does. The capped Tier A set was checked equal to `research._tier_a`.

| | With cap (25) | Without cap | Only exists without the cap |
|---|---|---|---|
| Tier A people (A1 + A2) | 3,035 | 3,035 | 0 |
| Tier A1 people | 949 | 1,069 | 120 (11.2% of no-cap A1) |
| Tier A2 people | 2,086 | 1,966 | |
| Tier B people (not Tier A) | 1,309 | 1,889 | 580 (30.7% of no-cap B) |
| A1 + B people together | 2,258 | 2,958 | 700 (23.7% of no-cap) |

**What this means.** About a quarter of the A1 and B ties you would get without the cap rest only on papers with more than 25 authors; for Tier B alone it is nearly a third. The cap is kept, and these are the numbers to quote. Tier A as a whole is not affected: 3,035 people either way, so the headline (Tier A with DOIs) does not depend on the cap, but "A1" (company-funded, small paper) does for 11% of the people who would be A1 without it. `tests/test_eval.py::test_e6_ablation_numbers` recomputes all five counts from the raw nodes with separate code and checks they appear in this section.

---

## E4: max flow vs the Cypher aggregate

*Run by Abdul, Oct 17 2026 (`FALKORDB_PORT=6381 uv run python eval/e4.py`, 18 s, read-only on `research:obesity-coke`, openalex@2026-10-15). Reviewer PASS (4 nits applied).*

Rule (fixed before running, redesign sections 5.3 and 7): flow earns a claim only if Spearman rho is below 0.95 or the top 10 changes; otherwise the max-flow claim is dropped and README says it was tested.

**Method.** Population: 7,539 people who are not Tier A and have at least one `COAUTH_B` link. Sample: 200 of them, seed 20261016. Strength of a link = `recent_small_papers` (a whole number). For each person:
- *aggregate* (Cypher): the sum, over all paths of 1 to 3 links to a Tier-A person, of the path's smallest link strength;
- *flow*: `algo.maxFlow` from the person to all 3,035 Tier-A people over `COAUTH_B`, capacity = link strength.
Top 10 ties are broken by author id.

**Two things found while setting it up.**
1. `algo.maxFlow` refused the decimal weights (`recent_small_w` and `w`): "capacity range too wide (max >= min * 2^32)", although the stored range is only 0.087 to 6.3 (`w` up to 19.7). It runs with the whole-number `recent_small_papers`. So the flow cannot use the 1/(n-1) weights the score uses.
2. `algo.maxFlow` has no depth limit. Of the 200 people, 77 reach a Tier-A person within 3 links (aggregate > 0), 29 have aggregate 0 but flow > 0 (the flow found a route longer than 3 links), and 94 have both 0. The flow is therefore not a "depth-3" number. A probe on the golden-test person with no tie found (3 hops, `A5067322664`) with capacity `n_papers` also returned flow 2.0 although the 3-hop search finds nothing.

**Result.**
| Subset | n | Spearman rho | top-10 overlap | flow equals aggregate |
|---|---|---|---|---|
| all 200 | 200 | 0.8597 | 6 of 10 | 96 (94 are both 0) |
| aggregate > 0 (within 3 links) | 77 | 0.7273 | 6 of 10 | 2 |

Time per person: aggregate 0.026 s, flow 0.051 s. No query failed.

**Reading it honestly.** By the fixed rule the ranking does change (rho below 0.95 and 4 of the top 10 differ), so the rule does not force the claim to be dropped. But the two numbers measure different things: the aggregate adds up every short path, the flow counts how much can pass at once and also uses longer routes. The fixed rule is easy to meet, because a sum over paths and a max flow are different quantities; and the path sum can count walks that revisit a person (FalkorDB paths are edge-unique, not node-unique). A different ranking does not show the flow is better; E1 and E2 failed, so neither ranking is validated, and tier B/C stay context only. The README should say: max flow was tested, gives a different ranking from the path sum, is not limited to 3 links, needs whole-number capacities, and is not used for any finding. `bridges` does not use it.

---

## 5f: end-to-end run on a second case (Philip Morris × lung cancer)

*Run by Aaryan, Oct 16, in a fresh Claude Code session (server `tietrace-alpha`, FalkorDB v4.22.1, graph `research:philip-morris-lung`, data version openalex@2026-10-16). Numbers below are what that session reported; they were not re-computed by a script.*

Prompt: "I'm writing about lung cancer screening and Philip Morris. Suggest 5 experts with no ties found and check any cigarette company."

| What | Result |
|---|---|
| Scope honesty | Pass. It said only Philip Morris (PMI funder and institutions, plus FSFW as A2) was checked, named what was left out on purpose (Altria, Vectura, Swedish Match) and what is not in the graph at all (BAT, Japan Tobacco, Imperial Brands), and said a new case needs human approval to build. |
| Experts with no ties found | 5 returned as Expert #1-#5 (25, 22, 19, 18, 18 papers in the graph), plus 4 spares. Scope stated: 3 co-author hops, 2021-2026, topic coverage 0.97. |
| Funder coverage | 37.5% of topic works in this graph list any funder (7,482 of 19,964, from the graph's `Meta` node; obesity-coke: 56%), so "no tie found" is weaker here; the reply said so and told the reporter to ask each expert directly. |
| Base rate (session-reported, not a stored number) | 73 authors checked to find 9 with no ties: 52 Tier C, 11 Tier B, 1 Tier A1. |
| Wording | No banned words. |

**Findings to act on:**
- **Tier C saturates in this field.** 52 of 73 top authors are Tier C. The session blamed big multi-author papers, but Tier C runs over `COAUTH_B` (2+ recent papers with 25 or fewer authors), so that explanation is not right as stated. Needs a check before any claim about it: is Tier C still informative when most of a field has it?
- **Topic is wider than screening.** OpenAlex has no screening topic; T10202 also covers diagnosis and treatment. The session ranked by the phrase "lung cancer screening" in titles, which is not a stored query.

## Judgement calls for the Reviewer to challenge
1. E3: only four names could be confirmed publicly; I did not guess a fifth. Hill and Peters are Tier A2 (foundation), so "Tier A" is read as A1 or A2.
2. E1 population: all topic authors who are not Tier A or company staff (including people with no recent papers); the same population is used for both rates. Bands were fixed before running.
3. E1 bootstrap: Poisson-weighted resampling of experts plus re-picking circles; the expected-count shortcut for circle members is a stated approximation.
4. E2: Y = 2018, window Y-4..Y, population and target definitions, the three-baseline pass reading, and the constant "direct-only" baseline.
