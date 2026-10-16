*Last updated by Abdul, Oct 16 2026: drew the sample with eval/e5_sample.py; verdict columns are empty for a human.*

# E5 profile guard: hand-check sample (50 random experts)

*Drawn by `eval/e5_sample.py` (seed 20261016, 82319 authors of topic papers in `research:obesity-coke`). The last three columns are for a HUMAN to fill in; nothing here is a verdict.*

How to check: open the OpenAlex link, look at the works list. Mark **merged** if the profile mixes two people, **split** if the same person has another profile in OpenAlex (a profile with the same name is a hint, column `same-name profiles`), **ok** otherwise. Error rate = (merged + split) / 50.

| # | OpenAlex | ORCID | works_count | loaded works | profile_flag | same-name profiles | merged / split / ok | note |
|---|---|---|---|---|---|---|---|---|
| 1 | [A5000599118](https://openalex.org/A5000599118) | 0000-0003-2132-4445 | 447 | 1 | ok | 0 |  |  |
| 2 | [A5005241986](https://openalex.org/A5005241986) | 0000-0001-8073-6664 | 198 | 1 | ok | 0 |  |  |
| 3 | [A5009885905](https://openalex.org/A5009885905) | 0000-0002-5272-548X | 18 | 1 | ok | 0 |  |  |
| 4 | [A5011190493](https://openalex.org/A5011190493) | 0000-0001-9826-2348 | 335 | 1 | ok | 0 |  |  |
| 5 | [A5011419324](https://openalex.org/A5011419324) |  | 35 | 1 | no_orcid | 0 |  |  |
| 6 | [A5017306126](https://openalex.org/A5017306126) | 0000-0002-6503-4709 | 322 | 1 | ok | 0 |  |  |
| 7 | [A5018251265](https://openalex.org/A5018251265) | 0000-0002-1902-7567 | 578 | 4 | ok | 0 |  |  |
| 8 | [A5022291875](https://openalex.org/A5022291875) | 0000-0001-9584-7631 | 71 | 2 | ok | 0 |  |  |
| 9 | [A5024550364](https://openalex.org/A5024550364) | 0000-0002-8132-2268 | 11 | 1 | ok | 0 |  |  |
| 10 | [A5025288876](https://openalex.org/A5025288876) | 0000-0003-3897-3636 | 23 | 1 | ok | 0 |  |  |
| 11 | [A5027072646](https://openalex.org/A5027072646) | 0000-0002-4638-1190 | 151 | 2 | ok | 0 |  |  |
| 12 | [A5027437305](https://openalex.org/A5027437305) | 0009-0005-8697-1514 | 8 | 2 | ok | 0 |  |  |
| 13 | [A5030484951](https://openalex.org/A5030484951) | 0000-0002-0690-8728 | 136 | 2 | ok | 0 |  |  |
| 14 | [A5031917269](https://openalex.org/A5031917269) | 0000-0002-6349-0946 | 32 | 1 | ok | 0 |  |  |
| 15 | [A5040877604](https://openalex.org/A5040877604) | 0000-0003-0489-4534 | 12 | 1 | ok | 0 |  |  |
| 16 | [A5042195505](https://openalex.org/A5042195505) | 0000-0002-3441-4055 | 3 | 1 | ok | 0 |  |  |
| 17 | [A5042340054](https://openalex.org/A5042340054) | 0000-0002-9676-7497 | 296 | 2 | ok | 0 |  |  |
| 18 | [A5046179442](https://openalex.org/A5046179442) | 0000-0003-3896-043X | 59 | 2 | ok | 0 |  |  |
| 19 | [A5049227536](https://openalex.org/A5049227536) | 0000-0003-2346-0598 | 317 | 1 | ok | 0 |  |  |
| 20 | [A5054335812](https://openalex.org/A5054335812) | 0000-0002-1199-7884 | 50 | 1 | ok | 0 |  |  |
| 21 | [A5057438021](https://openalex.org/A5057438021) | 0000-0001-7810-748X | 89 | 3 | ok | 0 |  |  |
| 22 | [A5063647357](https://openalex.org/A5063647357) | 0000-0003-3594-7854 | 21 | 1 | ok | 0 |  |  |
| 23 | [A5065657304](https://openalex.org/A5065657304) |  | 105 | 1 | no_orcid | 0 |  |  |
| 24 | [A5065963484](https://openalex.org/A5065963484) | 0000-0003-0950-0725 | 133 | 11 | ok | 1 |  |  |
| 25 | [A5072258594](https://openalex.org/A5072258594) | 0000-0002-8448-1264 | 2721 | 4 | works_count_anomaly | 0 |  |  |
| 26 | [A5073791023](https://openalex.org/A5073791023) |  | 20 | 1 | no_orcid | 0 |  |  |
| 27 | [A5075118359](https://openalex.org/A5075118359) | 0000-0003-1277-775X | 64 | 2 | ok | 0 |  |  |
| 28 | [A5075186341](https://openalex.org/A5075186341) | 0000-0002-9340-7592 | 168 | 1 | ok | 0 |  |  |
| 29 | [A5077201683](https://openalex.org/A5077201683) | 0000-0002-2355-2719 | 143 | 2 | ok | 0 |  |  |
| 30 | [A5079084055](https://openalex.org/A5079084055) |  | 10 | 1 | no_orcid | 0 |  |  |
| 31 | [A5081562717](https://openalex.org/A5081562717) | 0000-0002-9704-6608 | 28 | 2 | ok | 0 |  |  |
| 32 | [A5085647619](https://openalex.org/A5085647619) | 0000-0003-0133-0194 | 137 | 1 | ok | 0 |  |  |
| 33 | [A5086705091](https://openalex.org/A5086705091) | 0000-0002-8702-3437 | 39 | 1 | ok | 0 |  |  |
| 34 | [A5087236737](https://openalex.org/A5087236737) | 0000-0002-0179-6253 | 109 | 1 | ok | 1 |  |  |
| 35 | [A5092121621](https://openalex.org/A5092121621) |  | 12 | 1 | no_orcid | 0 |  |  |
| 36 | [A5100343930](https://openalex.org/A5100343930) | 0000-0003-0735-1148 | 111 | 2 | ok | 2 |  |  |
| 37 | [A5100425327](https://openalex.org/A5100425327) | 0000-0001-9291-793X | 123 | 1 | ok | 1 |  |  |
| 38 | [A5100660455](https://openalex.org/A5100660455) | 0009-0002-5003-8157 | 108 | 1 | ok | 1 |  |  |
| 39 | [A5101079491](https://openalex.org/A5101079491) | 0009-0004-4700-9077 | 84 | 1 | ok | 0 |  |  |
| 40 | [A5101520599](https://openalex.org/A5101520599) | 0000-0003-3529-7994 | 120 | 2 | ok | 0 |  |  |
| 41 | [A5102016610](https://openalex.org/A5102016610) | 0009-0003-1687-8352 | 18 | 1 | ok | 1 |  |  |
| 42 | [A5103104820](https://openalex.org/A5103104820) | 0000-0002-9685-761X | 6 | 1 | ok | 0 |  |  |
| 43 | [A5104545283](https://openalex.org/A5104545283) | 0009-0006-0996-9194 | 12 | 1 | ok | 0 |  |  |
| 44 | [A5107863169](https://openalex.org/A5107863169) |  | 1 | 1 | no_orcid | 0 |  |  |
| 45 | [A5108231719](https://openalex.org/A5108231719) |  | 20 | 1 | no_orcid | 0 |  |  |
| 46 | [A5111761395](https://openalex.org/A5111761395) |  | 44 | 1 | no_orcid | 0 |  |  |
| 47 | [A5112416235](https://openalex.org/A5112416235) | 0000-0002-8996-4425 | 23 | 1 | ok | 0 |  |  |
| 48 | [A5114016848](https://openalex.org/A5114016848) |  | 39 | 1 | no_orcid | 0 |  |  |
| 49 | [A5115506931](https://openalex.org/A5115506931) | 0009-0005-6548-0193 | 3 | 2 | ok | 0 |  |  |
| 50 | [A5154485829](https://openalex.org/A5154485829) |  | 1 | 1 | no_orcid | 0 |  |  |
