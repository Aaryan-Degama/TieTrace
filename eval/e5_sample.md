*Last updated by Aaryan, Oct 17 2026: added the column `AI suggestion (not a verdict)` from `eval/e5_precheck.py`; the human columns stay empty. Before that, Abdul, Oct 16 2026: drew the sample with eval/e5_sample.py; verdict columns are empty for a human.*

# E5 profile guard: hand-check sample (50 random experts)

*Drawn by `eval/e5_sample.py` (seed 20261016, 82319 authors of topic papers in `research:obesity-coke`). The `merged / split / ok` and `note` columns are for a HUMAN to fill in; nothing here is a verdict.*

How to check: open the OpenAlex link, look at the works list. Mark **merged** if the profile mixes two people, **split** if the same person has another profile in OpenAlex (a profile with the same name is a hint, column `same-name profiles`), **ok** otherwise. Error rate = (merged + split) / 50.

**The last column is an AI pre-check, not a verdict.** `eval/e5_precheck.py` reads each profile from the OpenAlex API and applies a fixed written rule (see the script's docstring): **merged** if the works span more than 60 years, two research domains share no co-author, or 3+ countries of affiliation appear in one year; **split** if another OpenAlex profile with the same surname and first initial shares an institution or a frequent co-author; **ok** if neither; **unclear** if both or the evidence is missing. Its `same-name` search covers all of OpenAlex, so it finds more than the graph-only `same-name profiles` column. A person must open the link and confirm or reject each suggestion before marking the human column. No error rate is claimed from the AI column; the error rate comes only from the human column.

| # | OpenAlex | ORCID | works_count | loaded works | profile_flag | same-name profiles | merged / split / ok | note | AI suggestion (not a verdict) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | [A5000599118](https://openalex.org/A5000599118) | 0000-0003-2132-4445 | 447 | 1 | ok | 0 |  |  | unclear: both kinds of signal: 4 countries in 2019 (DE, ES, NL, US); 1 same-name profile(s) linked, e.g. A5083222391 shares institution I136199984 |
| 2 | [A5005241986](https://openalex.org/A5005241986) | 0000-0001-8073-6664 | 198 | 1 | ok | 0 |  |  | unclear: both kinds of signal: 5 countries in 2025 (DK, GB, PL, SE, XK); 2 same-name profile(s) linked, e.g. A5048688296 shares institution I126596746; A5126253292 shares institution I166182418 |
| 3 | [A5009885905](https://openalex.org/A5009885905) | 0000-0002-5272-548X | 18 | 1 | ok | 0 |  |  | ok: no signal (span 2020-2023, 5 fields in 16 works, 3 same-name profiles share nothing) |
| 4 | [A5011190493](https://openalex.org/A5011190493) | 0000-0001-9826-2348 | 335 | 1 | ok | 0 |  |  | merged: 4 countries in 2008 (IN, SG, TW, US) |
| 5 | [A5011419324](https://openalex.org/A5011419324) |  | 35 | 1 | no_orcid | 0 |  |  | ok: no signal (span 2016-2023, 5 fields in 35 works, 0 same-name profiles share nothing) |
| 6 | [A5017306126](https://openalex.org/A5017306126) | 0000-0002-6503-4709 | 322 | 1 | ok | 0 |  |  | merged: 3 countries in 2017 (NO, QA, US) |
| 7 | [A5018251265](https://openalex.org/A5018251265) | 0000-0002-1902-7567 | 578 | 4 | ok | 0 |  |  | ok: no signal (span 1988-2025, 7 fields in 200 works, 0 same-name profiles share nothing) |
| 8 | [A5022291875](https://openalex.org/A5022291875) | 0000-0001-9584-7631 | 71 | 2 | ok | 0 |  |  | ok: no signal (span 2015-2026, 11 fields in 69 works, 0 same-name profiles share nothing) |
| 9 | [A5024550364](https://openalex.org/A5024550364) | 0000-0002-8132-2268 | 11 | 1 | ok | 0 |  |  | merged: Health Sciences and Social Sciences works share no co-author |
| 10 | [A5025288876](https://openalex.org/A5025288876) | 0000-0003-3897-3636 | 23 | 1 | ok | 0 |  |  | ok: no signal (span 1999-2025, 6 fields in 21 works, 0 same-name profiles share nothing) |
| 11 | [A5027072646](https://openalex.org/A5027072646) | 0000-0002-4638-1190 | 151 | 2 | ok | 0 |  |  | split: 2 same-name profile(s) linked, e.g. A5135653469 shares institution I114027177; A5126863987 shares institution I114027177 |
| 12 | [A5027437305](https://openalex.org/A5027437305) | 0009-0005-8697-1514 | 8 | 2 | ok | 0 |  |  | ok: no signal (span 2022-2026, 1 fields in 8 works, 0 same-name profiles share nothing) |
| 13 | [A5030484951](https://openalex.org/A5030484951) | 0000-0002-0690-8728 | 136 | 2 | ok | 0 |  |  | split: 1 same-name profile(s) linked, e.g. A5102798964 shares institution I124357947 |
| 14 | [A5031917269](https://openalex.org/A5031917269) | 0000-0002-6349-0946 | 32 | 1 | ok | 0 |  |  | ok: no signal (span 2014-2025, 3 fields in 31 works, 0 same-name profiles share nothing) |
| 15 | [A5040877604](https://openalex.org/A5040877604) | 0000-0003-0489-4534 | 12 | 1 | ok | 0 |  |  | split: 3 same-name profile(s) linked, e.g. A5108359086 shares institution I141584323; A5133773347 shares institution I141584323 |
| 16 | [A5042195505](https://openalex.org/A5042195505) | 0000-0002-3441-4055 | 3 | 1 | ok | 0 |  |  | ok: no signal (span 2022-2025, 1 fields in 3 works, 2 same-name profiles share nothing) |
| 17 | [A5042340054](https://openalex.org/A5042340054) | 0000-0002-9676-7497 | 296 | 2 | ok | 0 |  |  | merged: 4 countries in 2023 (CZ, EE, ES, RO) |
| 18 | [A5046179442](https://openalex.org/A5046179442) | 0000-0003-3896-043X | 59 | 2 | ok | 0 |  |  | ok: no signal (span 2013-2024, 4 fields in 50 works, 0 same-name profiles share nothing) |
| 19 | [A5049227536](https://openalex.org/A5049227536) | 0000-0003-2346-0598 | 317 | 1 | ok | 0 |  |  | merged: 3 countries in 2022 (DE, FR, GB) |
| 20 | [A5054335812](https://openalex.org/A5054335812) | 0000-0002-1199-7884 | 50 | 1 | ok | 0 |  |  | ok: no signal (span 2013-2026, 5 fields in 50 works, 0 same-name profiles share nothing) |
| 21 | [A5057438021](https://openalex.org/A5057438021) | 0000-0001-7810-748X | 89 | 3 | ok | 0 |  |  | ok: no signal (span 2016-2026, 8 fields in 74 works, 0 same-name profiles share nothing) |
| 22 | [A5063647357](https://openalex.org/A5063647357) | 0000-0003-3594-7854 | 21 | 1 | ok | 0 |  |  | split: 2 same-name profile(s) linked, e.g. A5018250227 shares institution I4626487; A5121682495 shares institution I2800102685 |
| 23 | [A5065657304](https://openalex.org/A5065657304) |  | 105 | 1 | no_orcid | 0 |  |  | ok: no signal (span 1981-2025, 8 fields in 106 works, 0 same-name profiles share nothing) |
| 24 | [A5065963484](https://openalex.org/A5065963484) | 0000-0003-0950-0725 | 133 | 11 | ok | 1 |  |  | unclear: both kinds of signal: 5 countries in 2012 (CH, GB, IR, KR, US); 1 same-name profile(s) linked, e.g. A5107910952 shares institution I47508984 |
| 25 | [A5072258594](https://openalex.org/A5072258594) | 0000-0002-8448-1264 | 2721 | 4 | works_count_anomaly | 0 |  |  | unclear: both kinds of signal: 10 countries in 2013 (AU, CA, CN, ES, GB, JP, NL, SG, TW, US); 13 same-name profile(s) linked, e.g. A5110543736 shares institution I165932596; A5143755623 shares institution I165779595 |
| 26 | [A5073791023](https://openalex.org/A5073791023) |  | 20 | 1 | no_orcid | 0 |  |  | ok: no signal (span 2019-2026, 2 fields in 20 works, 0 same-name profiles share nothing) |
| 27 | [A5075118359](https://openalex.org/A5075118359) | 0000-0003-1277-775X | 64 | 2 | ok | 0 |  |  | ok: no signal (span 2013-2026, 4 fields in 48 works, 0 same-name profiles share nothing) |
| 28 | [A5075186341](https://openalex.org/A5075186341) | 0000-0002-9340-7592 | 168 | 1 | ok | 0 |  |  | merged: 3 countries in 2008 (BE, FR, GB) |
| 29 | [A5077201683](https://openalex.org/A5077201683) | 0000-0002-2355-2719 | 143 | 2 | ok | 0 |  |  | merged: 3 countries in 2015 (CA, GB, NZ) |
| 30 | [A5079084055](https://openalex.org/A5079084055) |  | 10 | 1 | no_orcid | 0 |  |  | ok: no signal (span 2018-2025, 3 fields in 10 works, 3 same-name profiles share nothing) |
| 31 | [A5081562717](https://openalex.org/A5081562717) | 0000-0002-9704-6608 | 28 | 2 | ok | 0 |  |  | split: 1 same-name profile(s) linked, e.g. A5108265798 shares institution I98251732 |
| 32 | [A5085647619](https://openalex.org/A5085647619) | 0000-0003-0133-0194 | 137 | 1 | ok | 0 |  |  | ok: no signal (span 2001-2026, 5 fields in 137 works, 2 same-name profiles share nothing) |
| 33 | [A5086705091](https://openalex.org/A5086705091) | 0000-0002-8702-3437 | 39 | 1 | ok | 0 |  |  | unclear: both kinds of signal: Health Sciences and Social Sciences works share no co-author; 2 same-name profile(s) linked, e.g. A5041370430 shares institution I90267481; A5060346496 co-authored W640561575 with a frequent co-author |
| 34 | [A5087236737](https://openalex.org/A5087236737) | 0000-0002-0179-6253 | 109 | 1 | ok | 1 |  |  | split: 21 same-name profile(s) linked, e.g. A5087717847 shares institution I126520041; A5026930111 shares institution I126520041 |
| 35 | [A5092121621](https://openalex.org/A5092121621) |  | 12 | 1 | no_orcid | 0 |  |  | ok: no signal (span 2023-2023, 1 fields in 1 works, 0 same-name profiles share nothing) |
| 36 | [A5100343930](https://openalex.org/A5100343930) | 0000-0003-0735-1148 | 111 | 2 | ok | 2 |  |  | split: 17 same-name profile(s) linked, e.g. A5100343920 shares institution I159948400; A5045493285 shares institution I24185976 |
| 37 | [A5100425327](https://openalex.org/A5100425327) | 0000-0001-9291-793X | 123 | 1 | ok | 1 |  |  | split: 20 same-name profile(s) linked, e.g. A5100425320 shares institution I103890011; A5115591404 shares institution I103890011 |
| 38 | [A5100660455](https://openalex.org/A5100660455) | 0009-0002-5003-8157 | 108 | 1 | ok | 1 |  |  | split: 9 same-name profile(s) linked, e.g. A5100660452 shares institution I149240348; A5100660449 shares institution I177933477 |
| 39 | [A5101079491](https://openalex.org/A5101079491) | 0009-0004-4700-9077 | 84 | 1 | ok | 0 |  |  | split: 16 same-name profile(s) linked, e.g. A5101919615 shares institution I27781120; A5066774275 shares institution I19820366 |
| 40 | [A5101520599](https://openalex.org/A5101520599) | 0000-0003-3529-7994 | 120 | 2 | ok | 0 |  |  | unclear: both kinds of signal: 4 countries in 2023 (AE, CN, DK, US); 12 same-name profile(s) linked, e.g. A5018009756 shares institution I129708740; A5101520598 shares institution I115592961 |
| 41 | [A5102016610](https://openalex.org/A5102016610) | 0009-0003-1687-8352 | 18 | 1 | ok | 1 |  |  | split: 7 same-name profile(s) linked, e.g. A5090200628 shares institution I139660479; A5101871772 shares institution I187400657 |
| 42 | [A5103104820](https://openalex.org/A5103104820) | 0000-0002-9685-761X | 6 | 1 | ok | 0 |  |  | split: 1 same-name profile(s) linked, e.g. A5062663914 shares institution I4210167315 |
| 43 | [A5104545283](https://openalex.org/A5104545283) | 0009-0006-0996-9194 | 12 | 1 | ok | 0 |  |  | ok: no signal (span 2024-2026, 4 fields in 12 works, 0 same-name profiles share nothing) |
| 44 | [A5107863169](https://openalex.org/A5107863169) |  | 1 | 1 | no_orcid | 0 |  |  | split: 5 same-name profile(s) linked, e.g. A5101714430 shares institution I2800199158; A5107869864 shares institution I2800199158 |
| 45 | [A5108231719](https://openalex.org/A5108231719) |  | 20 | 1 | no_orcid | 0 |  |  | ok: no signal (span 2011-2024, 5 fields in 20 works, 5 same-name profiles share nothing) |
| 46 | [A5111761395](https://openalex.org/A5111761395) |  | 44 | 1 | no_orcid | 0 |  |  | ok: no signal (span 1972-2022, 5 fields in 44 works, 0 same-name profiles share nothing) |
| 47 | [A5112416235](https://openalex.org/A5112416235) | 0000-0002-8996-4425 | 23 | 1 | ok | 0 |  |  | ok: no signal (span 2015-2026, 8 fields in 23 works, 0 same-name profiles share nothing) |
| 48 | [A5114016848](https://openalex.org/A5114016848) |  | 39 | 1 | no_orcid | 0 |  |  | merged: 3 countries in 2015 (CN, IN, US) |
| 49 | [A5115506931](https://openalex.org/A5115506931) | 0009-0005-6548-0193 | 3 | 2 | ok | 0 |  |  | ok: no signal (span 2026-2026, 1 fields in 3 works, 0 same-name profiles share nothing) |
| 50 | [A5154485829](https://openalex.org/A5154485829) |  | 1 | 1 | no_orcid | 0 |  |  | split: 1 same-name profile(s) linked, e.g. A5036434488 shares institution I2801357902 |
