# Closed-issue coverage matrix

Review date: 2026-09-22

Frozen baseline at review start: local `main` = live `origin/main` = `4487ae3`,
with 125 closed issues and 21 open issues. The final baseline has 124 closed
issues and 22 open issues because the review reopened #137 as partial. The
issue body is the source specification. Closure comments are evidence, not
substitutes for the current code and tests.

## Evidence keys

- **D**: duplicate or specification routing from the Phase 0 inventory.
- **C1**: canonical #33-#47 review plus later on-main repairs.
- **C2**: updated canonical #48-#123 review, repair records #152-#155, and the
  restored 16-phase #114 gate.
- **S**: setup, credentials, and migrations review. Focused result: 346 passed,
  8 opt-in external-provider checks skipped.
- **P**: #63 private-profile review. Focused result: 36 passed.
- **T**: Stage 2 conversation review. Focused result: 132 passed plus rendered
  Chromium checks at 1280x720 and 390x844.
- **E**: evaluator review. Focused result: 69 passed. Fresh fake profiles remain
  unaccepted: early web 9 passed and 3 missing; full Stage 2 9 passed, 8
  missing, and 3 deliberately bad detector cases retained as failed.
- **G**: final repository gate reported below.

Every commit described as an ancestor was checked against `origin/main` or is a
patch-equivalent current-history replacement documented in the linked review.
Off-main prototype and historical evidence commits are named explicitly.

## Matrix

| Issue and title | Classification / canonical | Spec and closure source | Cited or patch-equivalent commit / ancestry | Focused evidence and unrun gates | Standards | Spec and current disposition |
| --- | --- | --- | --- | --- | --- | --- |
| [#2](https://github.com/snikmas/reckoning-3.0/issues/2) 01: Send the first local Simon message | implementation; #2 | body; 1 closure/evidence comment | cited `9afb95f`; patch-equivalent `6386160`; ancestor: yes | G | Pass | Pass |
| [#3](https://github.com/snikmas/reckoning-3.0/issues/3) 02: Enforce Simon's protected persona contract | duplicate; #34 | body; 1 closure/evidence comment | See #34; routed commit is on `origin/main` | D: routed to #34 | N/A or routed | Routed; use #34 Spec verdict |
| [#4](https://github.com/snikmas/reckoning-3.0/issues/4) 03: Turn messy input into a first reckoning | duplicate; #35 | body; 1 closure/evidence comment | See #35; routed commit is on `origin/main` | D: routed to #35 | N/A or routed | Routed; use #35 Spec verdict |
| [#5](https://github.com/snikmas/reckoning-3.0/issues/5) 04: Correct and confirm a reckoning | duplicate; #36 | body; 1 closure/evidence comment | See #36; routed commit is on `origin/main` | D: routed to #36 | N/A or routed | Routed; use #36 Spec verdict |
| [#6](https://github.com/snikmas/reckoning-3.0/issues/6) 05: Resume a decision and record its outcome | duplicate; #37 | body; 1 closure/evidence comment | See #37; routed commit is on `origin/main` | D: routed to #37 | N/A or routed | Routed; use #37 Spec verdict |
| [#7](https://github.com/snikmas/reckoning-3.0/issues/7) 06: Run the continuity loop with one real cloud provider | duplicate; #38 | body; 1 closure/evidence comment | See #38; routed commit is on `origin/main` | D: routed to #38 | N/A or routed | Routed; use #38 Spec verdict |
| [#8](https://github.com/snikmas/reckoning-3.0/issues/8) 07: Record private-slice trial evidence | duplicate; #39 | body; 1 closure/evidence comment | See #39; routed commit is on `origin/main` | D: routed to #39 | N/A or routed | Routed; use #39 Spec verdict |
| [#9](https://github.com/snikmas/reckoning-3.0/issues/9) 08: Remember and retrieve a corrected multilingual fact | duplicate; #40 | body; 1 closure/evidence comment | See #40; routed commit is on `origin/main` | D: routed to #40 | N/A or routed | Routed; use #40 Spec verdict |
| [#10](https://github.com/snikmas/reckoning-3.0/issues/10) 09: Archive, forget, and permanently delete personal context | duplicate; #41 | body; 1 closure/evidence comment | See #41; routed commit is on `origin/main` | D: routed to #41 | N/A or routed | Routed; use #41 Spec verdict |
| [#11](https://github.com/snikmas/reckoning-3.0/issues/11) 10: Run safe memory maintenance and pattern review | duplicate; #42 | body; 1 closure/evidence comment | See #42; routed commit is on `origin/main` | D: routed to #42 | N/A or routed | Routed; use #42 Spec verdict |
| [#12](https://github.com/snikmas/reckoning-3.0/issues/12) 11: Manage directions, goals, and the feasible goal set | duplicate; #43 | body; 1 closure/evidence comment | See #43; routed commit is on `origin/main` | D: routed to #43 | N/A or routed | Routed; use #43 Spec verdict |
| [#13](https://github.com/snikmas/reckoning-3.0/issues/13) 12: Plan across horizons and learn from check-ins | duplicate; #44 | body; 1 closure/evidence comment | See #44; routed commit is on `origin/main` | D: routed to #44 | N/A or routed | Routed; use #44 Spec verdict |
| [#14](https://github.com/snikmas/reckoning-3.0/issues/14) 13: Complete a bounded sourced research request | duplicate; #45 | body; 1 closure/evidence comment | See #45; routed commit is on `origin/main` | D: routed to #45 | N/A or routed | Routed; use #45 Spec verdict |
| [#15](https://github.com/snikmas/reckoning-3.0/issues/15) 14: Create an evidence-based goal forecast | duplicate; #46 | body; 1 closure/evidence comment | See #46; routed commit is on `origin/main` | D: routed to #46 | N/A or routed | Routed; use #46 Spec verdict |
| [#16](https://github.com/snikmas/reckoning-3.0/issues/16) 15: Propose a forecast revision after material change | duplicate; #47 | body; 1 closure/evidence comment | See #47; routed commit is on `origin/main` | D: routed to #47 | N/A or routed | Routed; use #47 Spec verdict |
| [#17](https://github.com/snikmas/reckoning-3.0/issues/17) 16: Compile and confirm a recurring command | duplicate; #48 | body; 1 closure/evidence comment | See #48; routed commit is on `origin/main` | D: routed to #48 | N/A or routed | Routed; use #48 Spec verdict |
| [#18](https://github.com/snikmas/reckoning-3.0/issues/18) 17: Execute a reliable routine with receipts | duplicate; #49 | body; 1 closure/evidence comment | See #49; routed commit is on `origin/main` | D: routed to #49 | N/A or routed | Routed; use #49 Spec verdict |
| [#19](https://github.com/snikmas/reckoning-3.0/issues/19) 18: Watch for a material goal change | duplicate; #50 | body; 1 closure/evidence comment | See #50; routed commit is on `origin/main` | D: routed to #50 | N/A or routed | Routed; use #50 Spec verdict |
| [#20](https://github.com/snikmas/reckoning-3.0/issues/20) 19: Deliver a selective scheduled briefing | duplicate; #51 | body; 1 closure/evidence comment | See #51; routed commit is on `origin/main` | D: routed to #51 | N/A or routed | Routed; use #51 Spec verdict |
| [#21](https://github.com/snikmas/reckoning-3.0/issues/21) 20: Complete a read-only connector lifecycle | duplicate; #52 | body; 1 closure/evidence comment | See #52; routed commit is on `origin/main` | D: routed to #52 | N/A or routed | Routed; use #52 Spec verdict |
| [#22](https://github.com/snikmas/reckoning-3.0/issues/22) 21: Govern an external write with exact or standing permission | duplicate; #53 | body; 1 closure/evidence comment | See #53; routed commit is on `origin/main` | D: routed to #53 | N/A or routed | Routed; use #53 Spec verdict |
| [#23](https://github.com/snikmas/reckoning-3.0/issues/23) 22: Reject instructions hidden in external content | duplicate; #54 | body; 1 closure/evidence comment | See #54; routed commit is on `origin/main` | D: routed to #54 | N/A or routed | Routed; use #54 Spec verdict |
| [#24](https://github.com/snikmas/reckoning-3.0/issues/24) 23: Open the Home command center and task-based areas | duplicate; #55 | body; 1 closure/evidence comment | See #55; routed commit is on `origin/main` | D: routed to #55 | N/A or routed | Routed; use #55 Spec verdict |
| [#25](https://github.com/snikmas/reckoning-3.0/issues/25) 24: Inspect operational truth through Control | duplicate; #56 | body; 1 closure/evidence comment | See #56; routed commit is on `origin/main` | D: routed to #56 | N/A or routed | Routed; use #56 Spec verdict |
| [#26](https://github.com/snikmas/reckoning-3.0/issues/26) 25: Continue Reckoning through Telegram | duplicate; #57 | body; 1 closure/evidence comment | See #57; routed commit is on `origin/main` | D: routed to #57 | N/A or routed | Routed; use #57 Spec verdict |
| [#27](https://github.com/snikmas/reckoning-3.0/issues/27) 26: Run in personal-server and hybrid limited modes | duplicate; #58 | body; 1 closure/evidence comment | See #58; routed commit is on `origin/main` | D: routed to #58 | N/A or routed | Routed; use #58 Spec verdict |
| [#28](https://github.com/snikmas/reckoning-3.0/issues/28) 27: Recover a clean installation through the bounded CLI | duplicate; #59 | body; 1 closure/evidence comment | See #59; routed commit is on `origin/main` | D: routed to #59 | N/A or routed | Routed; use #59 Spec verdict |
| [#29](https://github.com/snikmas/reckoning-3.0/issues/29) 28: Delegate one bounded worker task | duplicate; #60 | body; 1 closure/evidence comment | See #60; routed commit is on `origin/main` | D: routed to #60 | N/A or routed | Routed; use #60 Spec verdict |
| [#30](https://github.com/snikmas/reckoning-3.0/issues/30) 29: Install a public single-user Reckoning instance | duplicate; #61 | body; 1 closure/evidence comment | See #61; routed commit is on `origin/main` | D: routed to #61 | N/A or routed | Routed; use #61 Spec verdict |
| [#31](https://github.com/snikmas/reckoning-3.0/issues/31) 30: Select or author a persona within the protected contract | duplicate; #62 | body; 1 closure/evidence comment | See #62; routed commit is on `origin/main` | D: routed to #62 | N/A or routed | Routed; use #62 Spec verdict |
| [#32](https://github.com/snikmas/reckoning-3.0/issues/32) Build Reckoning as a private continuity agent with governed autonomy | duplicate; #1 | body; 1 closure/evidence comment | none; metadata only | D: specification duplicate; no runtime gate | N/A or routed | Duplicate of open product spec #1 |
| [#33](https://github.com/snikmas/reckoning-3.0/issues/33) 01: Send the first local Simon message | implementation; #33 | body; 1 closure/evidence comment | `6386160`; ancestor: yes | C1 + G | Pass | Pass |
| [#34](https://github.com/snikmas/reckoning-3.0/issues/34) 02: Enforce Simon's protected persona contract | implementation; #34 | body; 1 closure/evidence comment | `597b395`; ancestor: yes | C1 + G | Pass | Pass |
| [#35](https://github.com/snikmas/reckoning-3.0/issues/35) 03: Turn messy input into a first reckoning | implementation; #35 | body; 2 closure/evidence comments | `bc4b0de`; repair `c217589`; ancestor: yes | C1 + G | Pass | Pass after repair |
| [#36](https://github.com/snikmas/reckoning-3.0/issues/36) 04: Correct and confirm a reckoning | implementation; #36 | body; 1 closure/evidence comment | `7756fff`; ancestor: yes | C1 + G | Pass | Pass |
| [#37](https://github.com/snikmas/reckoning-3.0/issues/37) 05: Resume a decision and record its outcome | implementation; #37 | body; 2 closure/evidence comments | `ad59fba`, `fd36e4b`; ancestor: yes | C1 + G | Pass | Pass |
| [#38](https://github.com/snikmas/reckoning-3.0/issues/38) 06: Run the continuity loop with one real cloud provider | implementation; #38 | body; 3 closure/evidence comments | `0b306ec`, `548732c`, `3b44dd8`; ancestor: yes | C1 + G | Pass | Pass with historical live evidence; no live rerun |
| [#39](https://github.com/snikmas/reckoning-3.0/issues/39) 07: Record private-slice trial evidence | stage gate; #39 | body; 3 closure/evidence comments | `878ac63`; ancestor: yes | C1 + G | N/A or routed | Evidence gate only; does not prove product value |
| [#40](https://github.com/snikmas/reckoning-3.0/issues/40) 08: Remember and retrieve a corrected multilingual fact | service foundation; #40 | body; 2 closure/evidence comments | `f85c516`; ancestor: yes | C1 + G | Pass | Partial; service or prototype is not a complete user journey |
| [#41](https://github.com/snikmas/reckoning-3.0/issues/41) 09: Archive, forget, and permanently delete personal context | service foundation; #41 | body; 5 closure/evidence comments | `f85c516`; repair `c217589`; ancestor: yes | C1 + G | Pass | Partial; service or prototype is not a complete user journey |
| [#42](https://github.com/snikmas/reckoning-3.0/issues/42) 10: Run safe memory maintenance and pattern review | service foundation; #42 | body; 3 closure/evidence comments | `f85c516`; repair `c217589`; ancestor: yes | C1 + G | Pass | Partial; service or prototype is not a complete user journey |
| [#43](https://github.com/snikmas/reckoning-3.0/issues/43) 11: Manage directions, goals, and the feasible goal set | service foundation; #43 | body; 2 closure/evidence comments | `bf6fb91`; ancestor: yes | C1 + G | Pass | Partial; service or prototype is not a complete user journey |
| [#44](https://github.com/snikmas/reckoning-3.0/issues/44) 12: Plan across horizons and learn from check-ins | service foundation; #44 | body; 2 closure/evidence comments | `bf6fb91`; ancestor: yes | C1 + G | Pass | Partial; service or prototype is not a complete user journey |
| [#45](https://github.com/snikmas/reckoning-3.0/issues/45) 13: Complete a bounded sourced research request | prototype; #45 | body; 3 closure/evidence comments | `b8411c5`; repair `c217589`; ancestor: yes | C1 + G | Pass | Partial; service or prototype is not a complete user journey |
| [#46](https://github.com/snikmas/reckoning-3.0/issues/46) 14: Create an evidence-based goal forecast | prototype; #46 | body; 2 closure/evidence comments | `b8411c5`; ancestor: yes | C1 + G | Pass | Partial; service or prototype is not a complete user journey |
| [#47](https://github.com/snikmas/reckoning-3.0/issues/47) 15: Propose a forecast revision after material change | prototype; #47 | body; 2 closure/evidence comments | `b8411c5`; ancestor: yes | C1 + G | Pass | Partial; service or prototype is not a complete user journey |
| [#48](https://github.com/snikmas/reckoning-3.0/issues/48) 16: Compile and confirm a recurring command | implementation; #48 | body; 1 closure/evidence comment | `9a36e7e`, `95a6ce3`; ancestor: yes | C2 + G | Pass | Pass |
| [#49](https://github.com/snikmas/reckoning-3.0/issues/49) 17: Execute a reliable routine with receipts | implementation; #49 | body; 1 closure/evidence comment | `9a36e7e`, `95a6ce3`; #118 repair; ancestor: yes | C2 + G | Pass | Pass |
| [#50](https://github.com/snikmas/reckoning-3.0/issues/50) 18: Watch for a material goal change | service foundation; #50 | body; 2 closure/evidence comments | `9a36e7e`, `95a6ce3`; ancestor: yes | C2 + G | Pass | Pass as a service; not a user-facing capability |
| [#51](https://github.com/snikmas/reckoning-3.0/issues/51) 19: Deliver a selective scheduled briefing | service foundation; #51 | body; 2 closure/evidence comments | `9a36e7e`, `95a6ce3`; ancestor: yes | C2 + G | Pass | Pass as a service; not a user-facing capability |
| [#52](https://github.com/snikmas/reckoning-3.0/issues/52) 20: Complete a read-only connector lifecycle | service foundation; #52 | body; 3 closure/evidence comments | `9a36e7e`, `95a6ce3`; #152 `8a5b7f8`, `d73f732`; ancestor: yes | C2 + G | Pass | Pass as a service; not a user-facing capability |
| [#53](https://github.com/snikmas/reckoning-3.0/issues/53) 21: Govern an external write with exact or standing permission | implementation; #53 | body; 1 closure/evidence comment | `9a36e7e`, `95a6ce3`; #119 repair; ancestor: yes | C2 + G | Pass | Pass |
| [#54](https://github.com/snikmas/reckoning-3.0/issues/54) 22: Reject instructions hidden in external content | implementation; #54 | body; 1 closure/evidence comment | `9a36e7e`, `95a6ce3`; ancestor: yes | C2 + G | Pass | Pass |
| [#55](https://github.com/snikmas/reckoning-3.0/issues/55) 23: Open the Home command center and task-based areas | implementation; #55 | body; 1 closure/evidence comment | `9a36e7e`, `95a6ce3`; ancestor: yes | C2 + G | Pass | Pass |
| [#56](https://github.com/snikmas/reckoning-3.0/issues/56) 24: Inspect operational truth through Control | implementation; #56 | body; 2 closure/evidence comments | `9a36e7e`, `95a6ce3`; ancestor: yes | C2 + G | Pass | Pass |
| [#57](https://github.com/snikmas/reckoning-3.0/issues/57) 25: Continue Reckoning through Telegram | implementation; #57 | body; 2 closure/evidence comments | `9a36e7e`, `95a6ce3`; ancestor: yes | C2 + G | Pass | Pass deterministic; live Telegram unrun |
| [#58](https://github.com/snikmas/reckoning-3.0/issues/58) 26: Run in personal-server and hybrid limited modes | implementation; #58 | body; 2 closure/evidence comments | `9a36e7e`, `95a6ce3`; ancestor: yes | C2 + G | Pass | Pass |
| [#59](https://github.com/snikmas/reckoning-3.0/issues/59) 27: Recover a clean installation through the bounded CLI | implementation; #59 | body; 2 closure/evidence comments | `9a36e7e`, `95a6ce3`; ancestor: yes | C2 + G | Pass | Pass |
| [#60](https://github.com/snikmas/reckoning-3.0/issues/60) 28: Delegate one bounded worker task | service foundation; #60 | body; 4 closure/evidence comments | `9a36e7e`, `95a6ce3`; #154 `9f5e65f`; ancestor: yes | C2 + G | Pass | Pass as a service; not a user-facing capability |
| [#61](https://github.com/snikmas/reckoning-3.0/issues/61) 29: Install a public single-user Reckoning instance | evidence record; #61 | body; 4 closure/evidence comments | `9a36e7e`, `95a6ce3`; ancestor: yes | C2 + G | N/A or routed | Historical evidence only |
| [#62](https://github.com/snikmas/reckoning-3.0/issues/62) 30: Select or author a persona within the protected contract | evidence record; #62 | body; 3 closure/evidence comments | `9a36e7e`, `95a6ce3`; ancestor: yes | C2 + G | N/A or routed | Historical evidence only |
| [#63](https://github.com/snikmas/reckoning-3.0/issues/63) 31: Bootstrap Simon with a private user profile | implementation; #63 | body; 1 closure/evidence comment | cited `bef9040`; patch-equivalent `340f2de`; ancestor: yes | P: 36 passed + G | Pass | Pass |
| [#64](https://github.com/snikmas/reckoning-3.0/issues/64) Add terminal setup and local Telegram polling | implementation; #64 | body; current review evidence comment | patch-equivalent `667daea`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass through current setup/gateway replacement; live Telegram unrun |
| [#65](https://github.com/snikmas/reckoning-3.0/issues/65) Enter a model-provider API key during terminal setup | implementation; #68-#71 | body; 1 closure/evidence comment | patch-equivalent `b5e9890`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass under current inactive-until-verified contract |
| [#66](https://github.com/snikmas/reckoning-3.0/issues/66) Rework reckoning setup into a multi-provider interactive wizard | roll-up; #67-#71 | body; 1 closure/evidence comment | `336a391`, `dfed1e0`, `28c0faa`, `f4c9a4c`, `ca3c880`, `55033b1`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Completed through children, then partly superseded |
| [#67](https://github.com/snikmas/reckoning-3.0/issues/67) Connector section loop skeleton in setup, Telegram only selectable | superseded; #80 | body; 2 closure/evidence comments | `336a391`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Historically delivered; current behavior governed by #80 |
| [#68](https://github.com/snikmas/reckoning-3.0/issues/68) Multi-provider credential store with default and auto-migration | implementation; #68 | body; 1 closure/evidence comment | `dfed1e0`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#69](https://github.com/snikmas/reckoning-3.0/issues/69) Setup provider loop with "add another?" and explicit default choice | superseded; #80/#89 | body; 1 closure/evidence comment | `28c0faa`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Historically delivered; current behavior governed by #80/#89 |
| [#70](https://github.com/snikmas/reckoning-3.0/issues/70) Key verification failure recovery: retry, save anyway, or abort | superseded; #80/#89 | body; 1 closure/evidence comment | `f4c9a4c`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Historically delivered; current behavior governed by #80/#89 |
| [#71](https://github.com/snikmas/reckoning-3.0/issues/71) Setup re-run management menu: add, replace, remove, change default | superseded; #80/#89 | body; 2 closure/evidence comments | `ca3c880`, `55033b1`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Historically delivered; current behavior governed by #80/#89 |
| [#72](https://github.com/snikmas/reckoning-3.0/issues/72) Unify the CLI into one reckoning binary with a verify-first setup wizard (v0.2.0) | roll-up; #73-#79 | body; 1 closure/evidence comment | patch-equivalent `6067cfc`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Completed through children; later setup redesign governs |
| [#73](https://github.com/snikmas/reckoning-3.0/issues/73) Unified reckoning dispatcher with grouped help; ops commands move over | implementation; #73 | body; 1 closure/evidence comment | patch-equivalent `6067cfc`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#74](https://github.com/snikmas/reckoning-3.0/issues/74) reckoning gateway runs the configured channels | implementation; #74 | body; 1 closure/evidence comment | patch-equivalent `6067cfc`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#75](https://github.com/snikmas/reckoning-3.0/issues/75) Five-step setup wizard: instance, persona preset, provider, channels | superseded; #80 | body; 1 closure/evidence comment | patch-equivalent `6067cfc`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Historically delivered; current behavior governed by #80 |
| [#76](https://github.com/snikmas/reckoning-3.0/issues/76) Proof step: setup proves the core continuity loop or reports incomplete | superseded; #80 | body; 2 closure/evidence comments | patch-equivalent `6067cfc`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Historically delivered; current behavior governed by #80 |
| [#77](https://github.com/snikmas/reckoning-3.0/issues/77) Non-interactive setup flags and doctor --ping | implementation; #77 | body; 1 closure/evidence comment | patch-equivalent `6067cfc`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#78](https://github.com/snikmas/reckoning-3.0/issues/78) rich presentation across wizard, --help, and doctor | superseded; #80/#101 | body; 2 closure/evidence comments | patch-equivalent `6067cfc`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Historically delivered; current behavior governed by #80/#101 |
| [#79](https://github.com/snikmas/reckoning-3.0/issues/79) README rewrite and v0.2.0 release bookkeeping | repair; #79 | body; 3 closure/evidence comments | `6067cfc`; README repair `c3c2dda`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#80](https://github.com/snikmas/reckoning-3.0/issues/80) Replace setup with a clear guided terminal onboarding flow | implementation; #80 | body; 1 closure/evidence comment | `947b388`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#81](https://github.com/snikmas/reckoning-3.0/issues/81) Setup 01: Separate setup decisions from terminal rendering | implementation; #81 | body; 1 closure/evidence comment | patch-equivalent `947b388`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#82](https://github.com/snikmas/reckoning-3.0/issues/82) Setup 02: Complete Quick Setup in Demo mode | superseded; #80 | body; 2 closure/evidence comments | patch-equivalent `947b388`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Historically delivered; current behavior governed by #80 |
| [#83](https://github.com/snikmas/reckoning-3.0/issues/83) Setup 03: Resume setup and activate installed state atomically | implementation; #83 | body; 1 closure/evidence comment | `065481f`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#84](https://github.com/snikmas/reckoning-3.0/issues/84) Setup 04: Configure and verify current providers through a versioned registry | implementation; #84 | body; 1 closure/evidence comment | patch-equivalent `947b388`; `7b0407c`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#85](https://github.com/snikmas/reckoning-3.0/issues/85) Setup 05: Complete Custom Setup with explicit placement | superseded; #80 | body; 1 closure/evidence comment | patch-equivalent `947b388`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Historically delivered; current behavior governed by #80 |
| [#86](https://github.com/snikmas/reckoning-3.0/issues/86) Setup 06: Author and preview a desired-self persona | implementation; #86 | body; 1 closure/evidence comment | patch-equivalent `947b388`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#87](https://github.com/snikmas/reckoning-3.0/issues/87) Setup 07: Onboard a personal profile through setup | implementation; #87 | body; 1 closure/evidence comment | patch-equivalent `947b388`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#88](https://github.com/snikmas/reckoning-3.0/issues/88) Setup 08: Verify and pair Telegram without starting the Gateway | implementation; #88 | body; 1 closure/evidence comment | patch-equivalent `947b388`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#89](https://github.com/snikmas/reckoning-3.0/issues/89) Setup 09: Show configured state and repair one section | implementation; #89 | body; 1 closure/evidence comment | `065481f`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#90](https://github.com/snikmas/reckoning-3.0/issues/90) Setup 10: Migrate existing setup state safely | implementation; #90 | body; 1 closure/evidence comment | `7b0407c`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#91](https://github.com/snikmas/reckoning-3.0/issues/91) Setup 11: Manage setup sections through focused commands | superseded; #80/#89 | body; 1 closure/evidence comment | no literal commit; replacements `947b388`, `065481f`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Historically delivered; current behavior governed by #80/#89 |
| [#92](https://github.com/snikmas/reckoning-3.0/issues/92) Setup 12: Reset setup state with an exact preview | implementation; #92 | body; 1 closure/evidence comment | `7b0407c`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#93](https://github.com/snikmas/reckoning-3.0/issues/93) Setup 13: Match interactive setup in non-interactive mode | implementation; #93 | body; 1 closure/evidence comment | `065481f`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#94](https://github.com/snikmas/reckoning-3.0/issues/94) Setup 14: Add OpenAI and custom OpenAI-compatible providers | waiver; #94 | body; 5 closure/evidence comments | `065481f`, `7b0407c`, `048759c`; ancestor: yes | S: 346 passed, 8 skipped + G | N/A or routed | Waiver only; OpenAI/custom remain unavailable |
| [#101](https://github.com/snikmas/reckoning-3.0/issues/101) Setup 21: Implement the Signal terminal presentation | implementation; #101 | body; 4 closure/evidence comments | patch-equivalent `28a13b6`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#103](https://github.com/snikmas/reckoning-3.0/issues/103) Setup 23: Show setup prompts before waiting for input | repair; #103 | body; 1 closure/evidence comment | patch-equivalent `b12d011`, `c340205`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#104](https://github.com/snikmas/reckoning-3.0/issues/104) Investigate cross-component correctness gaps from the 2026-09-05 review | investigation; #105-#114 | body; 2 closure/evidence comments | investigation record `45fd116`; ancestor: yes | C2 + G | N/A or routed | Investigation complete; fixes judged in follow-ups |
| [#105](https://github.com/snikmas/reckoning-3.0/issues/105) Trust 01: Preserve model-run receipts across instances and clean recovery | repair; #105 | body; 1 closure/evidence comment | `d1e8987`, `427a739`; ancestor: yes | C2 + G | Pass | Pass |
| [#106](https://github.com/snikmas/reckoning-3.0/issues/106) Trust 02: Keep corrected and deleted personal context authoritative across instances | repair; #106 | body; 1 closure/evidence comment | `5a8d7b2`; ancestor: yes | C2 + G | Pass | Pass |
| [#107](https://github.com/snikmas/reckoning-3.0/issues/107) Trust 03: Preserve concurrent decisions, corrections, and outcome check-ins | repair; #107 | body; 2 closure/evidence comments | `8f575b3`; ancestor: yes | C2 + G | Pass | Pass |
| [#108](https://github.com/snikmas/reckoning-3.0/issues/108) Trust 04: Enforce personal-data processing grants at every model destination | repair; #108 | body; 1 closure/evidence comment | `8f575b3`; ancestor: yes | C2 + G | Pass | Pass |
| [#109](https://github.com/snikmas/reckoning-3.0/issues/109) Trust 05: Reject unauthorized web mutations before state changes | repair; #109 | body; 1 closure/evidence comment | `8f575b3`; ancestor: yes | C2 + G | Pass | Pass |
| [#110](https://github.com/snikmas/reckoning-3.0/issues/110) Trust 06: Return a sent web message to its visible Simon reply | repair; #110 | body; 1 closure/evidence comment | `8f575b3`; current mobile reply repair; ancestor: yes | C2 + G | Pass | Pass |
| [#111](https://github.com/snikmas/reckoning-3.0/issues/111) Trust 07: Resume Telegram processing and delivery without repeating completed work | repair; #111 | body; 1 closure/evidence comment | `4f3fd0d`; ancestor: yes | C2 + G | Pass | Pass |
| [#112](https://github.com/snikmas/reckoning-3.0/issues/112) Trust 08: Require an explicit decision before reporting trial acceptance | repair; #112 | body; 1 closure/evidence comment | `8f575b3`; ancestor: yes | C2 + G | Pass | Pass |
| [#113](https://github.com/snikmas/reckoning-3.0/issues/113) Trust 09: Identify remaining shared-state failures before concurrent product use | investigation; #117-#119 | body; 1 closure/evidence comment | diagnostic record; fixes in #117-#119; ancestor: yes | C2 + G | N/A or routed | Investigation complete; fixes judged in follow-ups |
| [#114](https://github.com/snikmas/reckoning-3.0/issues/114) Trust 10: Verify the repaired installation before adding responsibility journeys | stage gate; #114 | body; 3 closure/evidence comments | `ad13a00`; #153 `70cf1a7`, `5305e26`, `0e34780`; ancestor: yes | C2 + G | Pass | Reproducible 16-phase gate; external gates unrun |
| [#115](https://github.com/snikmas/reckoning-3.0/issues/115) Fix DeepSeek V4 setup verification under default thinking mode | repair; #115 | body; 1 closure/evidence comment | `c4154f2`, merge `f2aeb75`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass deterministic; real DeepSeek verification unrun |
| [#117](https://github.com/snikmas/reckoning-3.0/issues/117) Preserve channel state across independent applications | repair; #117 | body; 1 closure/evidence comment | `5a7d4e5`, roll-up `6cb697f`; ancestor: yes | C2 + G | Pass | Pass |
| [#118](https://github.com/snikmas/reckoning-3.0/issues/118) Preserve routine authority and execution records across workers | repair; #118 | body; 1 closure/evidence comment | `df22791`, roll-up `6cb697f`; ancestor: yes | C2 + G | Pass | Pass |
| [#119](https://github.com/snikmas/reckoning-3.0/issues/119) Keep external-write revocation authoritative | repair; #119 | body; 1 closure/evidence comment | `3e33d53`, roll-up `6cb697f`; ancestor: yes | C2 + G | Pass | Pass |
| [#123](https://github.com/snikmas/reckoning-3.0/issues/123) S2-01: Prototype the ordinary web continuity journey | prototype; #123 | body; 5 closure/evidence comments | off-main prototype `450edfd`; no current-main implementation | prototype browser evidence; no current product gate | N/A or routed | Accepted only as prototype evidence |
| [#124](https://github.com/snikmas/reckoning-3.0/issues/124) S2-02: Propose, correct, confirm, and reopen a web decision | implementation; #124 | body; 3 closure/evidence comments | `f93aa22`, repair `5333505`; #134/#135 repairs; ancestor: yes | T: 132 passed + Chromium + G | Pass | Pass |
| [#127](https://github.com/snikmas/reckoning-3.0/issues/127) S2-05: Define and run versioned conversation quality evaluation | evidence record; #136/#137 | body; 3 closure/evidence comments | off-main `90b7a74`; replacements #136/#137 | E: 69 passed + fresh fake profiles + G | N/A or routed | Historical off-main attempt; current evaluator judged in #136/#137 |
| [#134](https://github.com/snikmas/reckoning-3.0/issues/134) S2-R1: Require explicit consent bound to the displayed proposal | repair; #134 | body; current review evidence comment | `0b03c94`, `35701fe`, merge `d4925da`; ancestor: yes | T: 132 passed + Chromium + G | Pass | Pass |
| [#135](https://github.com/snikmas/reckoning-3.0/issues/135) S2-R2: Preserve conversational mutation identity across retry and restart | repair; #135 | body; current review evidence comment | `0b03c94`, `35701fe`, merge `d4925da`; ancestor: yes | T: 132 passed + Chromium + G | Pass | Pass |
| [#136](https://github.com/snikmas/reckoning-3.0/issues/136) S2-E1: Integrate evaluation against the actual Stage 2 web journey | repair; #136 | body; 2 closure/evidence comments | `aab82a0`, `4bf73bb`; ancestor: yes | E: 69 passed + fresh fake profiles + G | Pass | Pass deterministic; human and live quality unrun |
| [#138](https://github.com/snikmas/reckoning-3.0/issues/138) Create the processing grant automatically at provider activation | implementation; #138 | body; current review evidence comment | `cc5ee00`, equivalent `f16c33f`; ancestor: yes | S: 346 passed, 8 skipped + G | Pass | Pass |
| [#145](https://github.com/snikmas/reckoning-3.0/issues/145) S2-C2: Make conversation sessions visible and controllable | implementation; #145 | body; 2 closure/evidence comments | `4bc7320`, repair `1d9ea34`; ancestor: yes | T: 132 passed + Chromium + G | Pass | Pass |
| [#146](https://github.com/snikmas/reckoning-3.0/issues/146) S2-C3: Bound model history and render replies for each channel | implementation; #146 | body; 2 closure/evidence comments | `4bc7320`, repair `1d9ea34`; current mobile repair; ancestor: yes | T: 132 passed + Chromium + G | Pass | Pass |
| [#147](https://github.com/snikmas/reckoning-3.0/issues/147) S2-S1: Replace phrase-matching failures at protected conversation boundaries | implementation; #147 | body; 2 closure/evidence comments | `4bc7320`, repair `1d9ea34`; ancestor: yes | T: 132 passed + Chromium + G | Pass | Pass |
| [#152](https://github.com/snikmas/reckoning-3.0/issues/152) Keep connector revocation authoritative across instances | repair; #152 | body; 1 closure/evidence comment | `8a5b7f8`, `d73f732`; ancestor: yes | C2 + G | Pass | Pass |
| [#153](https://github.com/snikmas/reckoning-3.0/issues/153) Restore and modernize the #114 integration gate | repair; #153 | body; 1 closure/evidence comment | `70cf1a7`, `5305e26`, `0e34780`; ancestor: yes | C2 + G | Pass | Pass |
| [#154](https://github.com/snikmas/reckoning-3.0/issues/154) Record actual delegated model cost after quote violations | repair; #154 | body; 1 closure/evidence comment | `9f5e65f`; ancestor: yes | C2 + G | Pass | Pass |
| [#155](https://github.com/snikmas/reckoning-3.0/issues/155) Classify service-only capabilities as foundations | classification decision; #155 | body; 1 closure/evidence comment | issue-record decision; merge `4487ae3`; ancestor: yes | C2 + G | N/A or routed | Decision complete; no user capability added |

## Reconciliation

- Matrix rows: **124**. Live closed-issue count: **124**.
- Every closed issue number appears once. No open issue appears as accepted
  delivery.
- Duplicate rows route to a canonical row. They are not collapsed into it.
- Off-main #123 and #127 commits are named as prototype or historical evidence,
  not current delivery.
- Every partial verdict has a disposition. #137 was removed from the closed
  matrix, reopened, and labeled `partial` because its per-attempt spending
  receipt and quote contract remains unresolved.
- Live providers, paid evaluation, live Telegram, macOS, WSL, private persona
  evaluation, and dated human language judgments remain explicitly unrun unless
  an older issue carries separate historical evidence.
- #152, #153, #154, and #155 are the repair or decision records for the four
  Phase 2 findings.

## Open issue moved out of the retrospective set

[#137](https://github.com/snikmas/reckoning-3.0/issues/137) was part of the
125-issue frozen export. The review found that current `main` lacks the required
per-attempt provider-reported-cost receipt and quote-overrun evidence. The issue
is now open with the `partial` label and a current evidence comment. Its full
verdict remains in `2026-09-22-stage-2-post-delivery-review.md`.

## Final verification

The first full run after the mobile reply repair found one stale test
expectation: the profile onboarding test still expected `/simon` rather than
the repaired `/simon#latest-reply` redirect. That run recorded `862 passed, 15
skipped, 1 failed`. The assertion was updated to the current visible-reply
contract and the complete suite was rerun.

| Check | Result | Classification |
| --- | --- | --- |
| Full non-live pytest | 863 passed, 15 skipped, 0 failed | Pass with skips reported separately |
| Mypy, Python 3.13 | 48 source files, no issues | Pass |
| Ruff E4/E7/E9/F | All checks passed | Pass |
| Compileall | Exit 0 | Pass |
| `git diff --check` | Exit 0 | Pass |
| Restored #114 scenario | 16 phases passed | Pass; synthetic fake transports |
| Setup focused suite | 346 passed, 8 skipped | Pass with opt-in external checks skipped |
| #63 focused suite | 36 passed | Pass |
| Stage 2 conversation suite | 132 passed | Pass |
| Evaluator and spending suite | 69 passed | Pass for implemented deterministic behavior; #137 verdict remains partial |
| Chromium normal and 390x844 | proposal binding, stale-form rejection, literal HTML escaping, no horizontal overflow, clickable Inspector, latest reply visible | Manual pass |
| Fake `early-web` profile | Exit 1; 9 product passed, 3 product missing, no detector cases | Expected non-acceptance |
| Fake `full-stage-2` profile | Exit 1; 9 product passed, 8 product missing, 3 detector failed | Expected non-acceptance |
| Product-spec and grilling scripts | Not rerun; #153 records missing required documentation inputs | Excluded known failed checks, not passes |
| Live or paid providers | Not run | Unrun, authorization required |
| Live Telegram | Not run | Unrun |
| macOS and WSL | Not run | Unrun |
| Private persona and profile evaluation | Not run | Unrun by policy |
| Naturalness, usefulness, and multilingual human judgment | Not run | Unrun; fake output cannot establish them |

The 15 pytest skips remain skipped evidence. They are not included in the 863
passes.
