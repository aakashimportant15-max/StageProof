# StageProof — TASKS.md

**Authority:** This document owns **what is built and in what order**: task IDs, dependencies, acceptance criteria, estimates, gates and cut order.
**Defers to:** ARCHITECTURE.md (files/interfaces), RULES.md (behavior and thresholds), DESIGN.md (UI), PRD.md (scope/priority).
**For the coding agent (Qoder):** Execute tasks in the **Execution order** below, not in phase-number order. Phases are logical groupings; dependencies are binding. Do not start a task until all its dependencies are done and their acceptance criteria pass.

Priority: **P0** must work · **P1** strengthens submission, cut first if late. Estimates are agent-assisted elapsed hours (code generation + review + running). Totals: **P0 ≈ 21.8 h · P1 ≈ 2.0 h · reserve ≈ 6.2 h** for integration and debugging (30 h solo budget).

### Global Definition of Done (applies to every task)
1. Acceptance criteria met and demonstrated by a command or test.
2. Imports respect ARCHITECTURE.md §3 layering; no thresholds hard-coded (N-15); no `print` in library code (use `logging`); type hints on public functions.
3. Pure logic has unit tests; no test needs the network.
4. If an interface or format changed, ARCHITECTURE.md §15/§13 is updated in the same change; if behavior changed, RULES.md is updated **first** (and the change flagged to the user).
5. The task checkbox below is ticked and the **Current status** section of MEMORY.md is updated at each gate.
6. Never mark partial work as done.

---

## Execution order (critical path)

Priority of the critical path: **data/model → simulation → checks/evidence → decision → pipeline → security/audit → estimator/response → scenarios → tests → dashboard → polish.** The dashboard MUST NOT start until Gate G3 (scenarios A, C, D, F pass from the CLI).

1. T001 · 2. T002 · 3. T003 · 4. T005 · 5. T004 · 6. T006 · 7. T007 · 8. T008 · 9. T009 *(Gate G1)*
10. T012 · 11. T013 · 12. T014 · 13. T015 *(Gate G2)* · 14. T016
15. T010 · 16. T034 · 17. T035 · 18. T036 *(simulation core, after transport)*
19. T018 · 20. T019 · 21. T021 · 22. T022 · 23. T023 · 24. T024
25. T017 · 26. T025 · 27. T031 · 28. T011 · 29. T032
30. T026 · 31. T027 · 32. T028 · 33. T029 · 34. T030 · 35. T033
36. T037 · 37. T038 · 38. T041 · 39. T042 *(Gate G3)*
40. T044 · 41. T045 · 42. T046 · 43. T047 · 44. T048 · 45. T049
46. T051 · 47. T052 · 48. T053 *(Gate G4)* · 49. T054 · 50. T055 · 51. T056
52. T039 (P1) · 53. T040 (P1) · 54. T043 (P1) · 55. T020 (P1) · 56. T050 (P1) · 57. T057 (P1)
58. T058 · 59. T059 *(Gate G5)* · 60. T060 · 61. T061 · 62. T062

Every task appears exactly once; each task's dependencies appear earlier in this list. Phase headings below group tasks by topic only.

**Gates**
- **G1 (after T009):** data feasibility — aligned `reach.csv` with a flood and several moderate events exists. If not, use the contingency in T006.
- **G2 (T015):** model fit gates pass (ARCHITECTURE §6.7). Do not proceed until they pass or the decision to relax is recorded.
- **G3 (T042, "MVP-0"):** scenarios A, C, D, F pass from the CLI. Dashboard work starts only after this.
- **G4 (T053):** Live screen plays scenarios A and C end-to-end (MVP-1).
- **G5 (T059):** full demo works offline.

## Acceptance catalog (referenced by tasks)

| ID | Criterion |
|---|---|
| **AC-A** (Scenario A) | CLI exit 0 and expectations pass: no committed FAULT/ATTACK anywhere; B committed `REAL_FLOOD` from the first tick with `context == CONSISTENT` and `notable`; alert reaches PROVISIONAL (basis OBSERVED) ≤ 2 ticks after observed stage ≥ `action_stage`; reaches CONFIRMED after `downstream == YES`; rolling-z baseline flags ≥ 1 tick; no retraction; audit verifies. |
| **AC-C** (Scenario C) | All B packets pass transport (no HARD flags) while corrupted; `context` becomes PHANTOM; `POSSIBLE_CYBER_ATTACK/FABRICATED` committed within `persistence.fault_attack_ticks` ticks after the first tick `z_mean ≥ z_implausible` (manifest tolerance applies); B QUARANTINED; actions include `QUARANTINE_SENSOR`, `RAISE_SECURITY_ALERT`, `PRESERVE_EVIDENCE`, `NOTIFY_OFFICER`, `USE_ESTIMATE`; alert never exceeds WATCH; **no `SEND_PUBLIC_WARNING` action exists in the run**; estimate `stage_hat` stays below `action_stage`; threshold baseline fires at least once (shows the false alarm that was avoided). |
| **AC-D** (Scenario D) | `POSSIBLE_CYBER_ATTACK/SUPPRESSION` committed; B QUARANTINED; alert reaches PROVISIONAL with basis ESTIMATE and a `SEND_PUBLIC_WARNING` with basis ESTIMATE exists; observed B stays below `action_stage` while truth is above (threshold baseline silent); no `REAL_FLOOD` committed from B once suppression is in effect. |
| **AC-F** (Scenario F) | `UNSIGNED_INJECT` packet → `SIG_INVALID`, packet rejected (not used as observation), `POSSIBLE_CYBER_ATTACK/TRANSPORT` committed on the same tick; `REPLAY_PACKET` → `SEQ_REPLAY`; B QUARANTINED; estimate in use; `TRANSPORT_FAILURE` audit events carry the flags; audit chain verifies; tampered copy fails. |
| **AC-TT** (transport tamper) | Altered stage, altered ts, altered unit, wrong key → `SIG_INVALID`; channel ≠ station id → `STATION_MISMATCH`; unregistered station → `UNKNOWN_STATION`; reused or decreasing seq → `SEQ_REPLAY`; seq gap → soft `SEQ_GAP`; `|ts − tick| >` limit → `TS_SKEW`; non-increasing ts → `TS_NONMONOTONIC`; signature comparison uses `hmac.compare_digest` (static check). |
| **AC-AT** (audit tamper) | Untouched chain verifies; editing payload/hash/prev_hash of event *k*, deleting event *k*, or swapping two events → `verify()` fails with `first_bad_index == k` (or first affected index); `tamper_copy` never alters the original; the same seeded run yields identical chain hashes. |
| **AC-NL** (no future leakage) | Prefix invariance: for sampled ticks *t* (including every state-transition tick), replacing every `TickInput` after *t* with garbage/NaN (validly signed) leaves all `TickRecord`s up to *t* identical; `build_features` asserts max index ≤ current; an hourly rain value stamped *H* is not visible before the tick with `ts ≥ H`. |
| **AC-QI** (quarantine isolation) | (1) After A is QUARANTINED, perturbing A's readings arbitrarily leaves B's prediction/estimate unchanged and `inputs_used` excludes A; (2) while B is QUARANTINED (estimate in use), perturbing B's readings leaves the `Estimate` unchanged; (3) across all scenario runs, `Prediction.inputs_used ⊆ trusted roles` at every tick; (4) no `SEND_PUBLIC_WARNING` with basis OBSERVED is created while B is not TRUSTED. |
| **AC-B / AC-E** | Scenarios B and E meet RULES §19. |
| **AC-DET** | Same seed ⇒ identical `TickRecord`s and audit hashes across two runs. |
| **AC-LAYER** | AST scan confirms every import obeys ARCHITECTURE §3. |

---

## Phase 0 — Repository inspection

### T001 — Inspect repository, docs and toolchain `[P0 · 0.1 h]` ☑
- **Purpose:** Establish ground truth before changing anything.
- **Files:** none modified; findings recorded in `docs/MEMORY.md → Current status`.
- **Depends:** –
- **Notes:** Confirm the seven docs exist and are readable; confirm Python ≥ 3.11 and pip; list existing files; do not delete anything. If an existing project structure differs from ARCHITECTURE §2.1, report it and follow the architecture.
- **Acceptance:** MEMORY.md status lists Python version, existing files and any discrepancies.

## Phase 1 — Environment and configuration

### T002 — Scaffold repository and tooling `[P0 · 0.25 h]` ☑
- **Purpose:** Create the skeleton exactly as ARCHITECTURE §2.1.
- **Files:** `pyproject.toml`, `Makefile`, `.gitignore`, `.env.example`, `.streamlit/config.toml`, `README.md` (stub), all package/dir stubs with `__init__.py`.
- **Depends:** T001
- **Notes:** Dependencies: numpy, pandas, scikit-learn, pyyaml, streamlit, plotly; extras `data` (requests, dataretrieval) and `dev` (pytest). Makefile targets: `setup data fit scenarios test demo eval cache clean`. `.env.example` has demo-only keys `STAGEPROOF_KEY_A|T|B|C` with a "DEMO ONLY" comment. `.gitignore` covers `.env`, `data/raw`, `artifacts/audit`, caches.
- **Acceptance:** `pip install -e .[dev]` succeeds; `pytest` collects 0 errors; `python -c "import stageproof"` works.

### T003 — Create configuration files `[P0 · 0.4 h]` ☑
- **Purpose:** Single place for all tunables.
- **Files:** `config/reach.yaml`, `config/thresholds.yaml`, `config/policy.yaml`, `config/messages.yaml`.
- **Depends:** T002
- **Notes:** `thresholds.yaml` = RULES §5 block verbatim. `policy.yaml` encodes RULES §12 tiers and trigger map plus `demo_auto_approve_tier1: true`. `messages.yaml` = DESIGN §9 templates, RULES Appendix A reason texts, four volunteers (V1–V4: id, name, language en/hi, station B, reliability 0.8). `reach.yaml` has the structure in ARCHITECTURE §13.2 with placeholders (null) for site-specific values.
- **Acceptance:** All four files parse as YAML; keys match the documents; no value is invented beyond RULES/DESIGN.

### T004 — Settings loader `[P0 · 0.25 h]` ☑
- **Purpose:** Typed, validated configuration.
- **Files:** `stageproof/settings.py`
- **Depends:** T003, T005
- **Notes:** Resolve threshold precedence (yaml non-null > `model.json` > error) lazily when a model is attached. Parse `.env` with a small internal parser (no extra dependency). Raise `ConfigError` with actionable messages.
- **Acceptance:** `load_settings()` returns a `Settings` with all RULES §5 keys; missing key raises `ConfigError` naming it.

## Phase 2 — Domain and data

### T005 — Domain objects `[P0 · 0.5 h]` ☑
- **Purpose:** One vocabulary for every module.
- **Files:** `stageproof/domain.py`
- **Depends:** T002
- **Notes:** Enums and dataclasses per ARCHITECTURE §11; `record_to_dict`/`record_from_dict` round-trip including enums and datetimes. Execute T005 before T004 despite numbering.
- **Acceptance:** Round-trip test of a populated `TickRecord`; no imports from other package modules.

### T006 — Select reach and probe data availability `[P0 · 0.75 h]` ☑ *(human decision)*
- **Purpose:** Choose three river gauges plus a tributary on one reach with a travel time of roughly 3–24 hours and a documented flood.
- **Files:** `config/reach.yaml` (stations, levels, splits, events), `docs/MEMORY.md` (assumptions)
- **Depends:** T003
- **Notes:** Criteria: same river stem; no dam or major confluence between A and C except gauged T; 15-minute stage and discharge available; approved data; ≥ 3 years with several moderate events and one large flood; flood stage available or define `watch/action/clear` by percentile. A candidate to test first (from memory — verify): Potomac at Point of Rocks → Monocacy tributary → Potomac at Little Falls. Define chronological `splits` (train / calibration / test) and ensure scenario `events` lie in `test`. Contingency if no suitable reach after the time-box: ask the user before building any synthetic fallback.
- **Acceptance:** `reach.yaml` fully filled; events and splits non-overlapping; assumptions logged in MEMORY.

### T007 — Data preparation: stage and discharge `[P0 · 0.5 h]` ☑
- **Purpose:** Download and cache 15-minute stage and discharge for A, T, B, C.
- **Files:** `scripts/prepare_data.py`
- **Depends:** T006, T009 helpers optional
- **Notes:** The only network-using script. Verify the current USGS access path at implementation time (legacy WaterServices are being retired; prefer the maintained client's current module, fall back to the legacy `nwis` module), accept an optional API key via env, cache raw responses in `data/raw/`, never re-download if cached unless `--force`.
- **Acceptance:** `data/raw/` contains per-station files covering all `splits`; script prints coverage and gap statistics.

### T008 — Data preparation: rainfall, alignment, `reach.csv` `[P0 · 0.4 h]` ☑
- **Purpose:** Produce the aligned dataset.
- **Files:** `scripts/prepare_data.py`
- **Depends:** T007
- **Notes:** Fetch hourly precipitation at the configured points, compute the weighted basin mean; convert everything to UTC; resample to a 15-minute grid; no silent fills beyond the configured 1-hour interpolation of tiny gaps; forward-fill `rain_mm_prev_hr` per ARCHITECTURE §13.1 (the hourly value stamped H is available from H); write `data/reach.csv` and `data_report.json`.
- **Acceptance:** `reach.csv` matches the schema; report lists missing percentages per column; rainfall timing rule verified by a unit check on a few rows.

### T009 — Data loaders and validation `[P0 · 0.15 h]` ☑
- **Purpose:** Safe runtime access to the dataset and model artifact.
- **Files:** `stageproof/data.py`
- **Depends:** T005, T004
- **Notes:** `load_dataset` validates columns, UTC monotonic index, 15-minute step; `load_model_artifact` validates required keys; slicing helper for scenario windows with warmup.
- **Acceptance:** Loading a corrupted CSV raises `ArtifactError` with a clear message. **Gate G1** closes here.

## Phase 3 — Security and audit

### T010 — Transport security `[P0 · 0.5 h]` ☑
- **Purpose:** Authentic packets and transport evidence.
- **Files:** `stageproof/security/transport.py`
- **Depends:** T005, T004
- **Notes:** Implement canonical string, `sign`, stateful `verify` (signature, station binding, unknown station, sequence replay/gap, timestamp skew/non-monotonic), `verify_stateless`, `load_keys`, `TransportState`. Use `hmac.compare_digest`. State advances only on valid packets. Flag classes follow RULES §3.1.
- **Acceptance:** **AC-TT**.

### T011 — Audit log `[P0 · 0.4 h]` ☑
- **Purpose:** Tamper-evident record.
- **Files:** `stageproof/security/audit.py`
- **Depends:** T005
- **Notes:** JSONL; canonical JSON; genesis `prev_hash` of 64 zeros; `append`, `verify`, `load`, `tamper_copy`. Simulated `ts` only. Fail closed on write error with `AuditError`.
- **Acceptance:** **AC-AT** (unit-level).

## Phase 4 — Models, checks, evidence

### T012 — Rating curves `[P0 · 0.4 h]` ☑
- **Purpose:** Convert stage ↔ discharge consistently everywhere.
- **Files:** `stageproof/models.py`
- **Depends:** T005
- **Notes:** `RatingCurve`: power-law with `h0` grid search on log-log fit; monotone-interpolation fallback; both directions; serialize to dict.
- **Acceptance:** Round-trip stage → Q → stage error is negligible; fit quality reported per station; extrapolation beyond range follows the power law.

### T013 — Feature builder `[P0 · 0.35 h]` ☑
- **Purpose:** Build lagged ln-discharge and rain features from a `Window` with no future access.
- **Files:** `stageproof/models.py`
- **Depends:** T012
- **Notes:** `build_features(window, variant_spec)` returns `None` if any needed lag is unavailable; asserts index bounds; rain features `ln(1 + P_n)` from completed hourly values only.
- **Acceptance:** Unit tests: lag alignment, missing handling, assertion fires if an out-of-bounds index is requested (part of **AC-NL**).

### T014 — Transfer model, calibration, serialization `[P0 · 0.5 h]` ☑
- **Purpose:** The Ridge integrity model and its calibration.
- **Files:** `stageproof/models.py`
- **Depends:** T013
- **Notes:** `TransferModel.fit/predict/to_dict/from_dict`; `RidgeCV` with `TimeSeriesSplit(5)`; standardization stored; calibration returns `scale_normal`, `scale_high`, high-flow threshold; optional `highflow_weight`.
- **Acceptance:** Fit on a small synthetic series recovers a known lag within tolerance; serialization round-trip reproduces predictions exactly.

### T015 — Fit script and gates `[P0 · 0.75 h]` ☑
- **Purpose:** Produce `artifacts/model.json` and `fit_report.json`.
- **Files:** `scripts/fit_models.py`
- **Depends:** T014, T009, T006
- **Notes:** Cross-correlation lag discovery (`τ_AB`, `τ_TB`, `τ_BC`); fit all upstream variants and the downstream model on the **train** range; calibrate on the **calibration** range; derive `spike_delta_max`, noise baseline, quantization step, rain `yes_mm/no_mm` (10th percentile of rainfall preceding historical large rises), `lag_BC_ticks`, `required_history_ticks`; assert no scenario/test window overlaps (N-17); evaluate gates G-fit-1..4 (ARCHITECTURE §6.7); non-zero exit on required-gate failure.
- **Acceptance:** `model.json` and `fit_report.json` written; gates pass or the failure is explained and the user decision recorded. **Gate G2.**

### T016 — Model bundle `[P0 · 0.35 h]` ☑
- **Purpose:** Inference API used by the pipeline.
- **Files:** `stageproof/models.py`
- **Depends:** T015
- **Notes:** `ModelBundle.from_dict`; `predict_target(window, trust)` selects the first variant whose inputs are all trusted and available; returns `Prediction` with `z`, `scale`, `regime`, `variant`, `inputs_used`, `degraded`; `predict_downstream(window)`; stage↔Q helpers; exceptions → `Prediction.error`.
- **Acceptance:** With a clean held-out window, `z` is near zero; removing A from the trust map switches the variant and sets `degraded`.

### T017 — Baselines `[P0 · 0.15 h]` ☑
- **Purpose:** Comparison detectors for the dashboard and evaluation.
- **Files:** `stageproof/models.py`
- **Depends:** T005, T004
- **Notes:** `StaticThresholdBaseline`, `RollingRobustZBaseline` per ARCHITECTURE §6.10. Pure, online, no model dependency.
- **Acceptance:** On a synthetic series the rolling-z flags a sharp rise; the threshold baseline fires exactly at `action_stage`.

### T018 — Sensor health checks `[P0 · 0.5 h]` ☐
- **Purpose:** Shape plausibility (RULES §3.2).
- **Files:** `stageproof/checks.py`
- **Depends:** T005, T004
- **Notes:** DROPOUT, RANGE, SPIKE (retroactive on revert), STUCK (target only, uses prediction-change input), NOISE (target only), RATE_EXCEEDED (info), NOISE_TOO_CLEAN (P1). Returns `HealthResult` with active flags and `shape_ok`. A rate violation alone never sets `shape_ok = False`.
- **Acceptance:** Unit tests with synthetic windows for each check, including "flash flood rise is NOT a fault" (N-10).

### T019 — Trend, rain support, drift, notable `[P0 · 0.35 h]` ☐
- **Purpose:** Context helpers (RULES §3.4, §3.6, §3.9, §3.10).
- **Files:** `stageproof/checks.py`
- **Depends:** T018
- **Notes:** `upstream_trend` (RISING → FALLING → FLAT order, UNKNOWN if A and T not trusted); `rain_support` with completed-hours and staleness; `drift_measure`; helper for notable level.
- **Acceptance:** Table-driven unit tests across boundary values.

### T020 — Replay match `[P1 · 0.15 h]` ☐
- **Purpose:** Verbatim replay detection (RULES §3.8).
- **Files:** `stageproof/checks.py`
- **Depends:** T018, T009
- **Notes:** Precompute the station archive windows at startup excluding ±`min_age_days`; exact-match tolerance in quantization steps; requires minimum range.
- **Acceptance:** An exact copy of an earlier flood window is flagged; a different real flood is not.

### T021 — Evidence builder `[P0 · 0.35 h]` ☐
- **Purpose:** One typed `Evidence` per tick.
- **Files:** `stageproof/evidence.py`
- **Depends:** T018, T019, T005
- **Notes:** Context classification from `z_mean` and variant rules (RULES §3.3), downstream classification with PENDING logic (§3.5), `degraded`, `notable`; no judgments about labels.
- **Acceptance:** Table-driven tests: every Context value reachable; INSUFFICIENT for rain-only variant or no prediction.

## Phase 5 — Decision engine

### T022 — Decision rules `[P0 · 0.5 h]` ☐
- **Purpose:** Implement RULES §4 exactly.
- **Files:** `stageproof/decision.py`
- **Depends:** T021
- **Notes:** R1, R2, R2b, R3, R4, R5, R6, R7 in order; PA1, PA2, PA3; reason codes from Appendix A; confidence per table. Pure function.
- **Acceptance:** See T024.

### T023 — Verdict tracker (persistence) `[P0 · 0.35 h]` ☐
- **Purpose:** Candidate → committed per RULES §6.
- **Files:** `stageproof/decision.py`
- **Depends:** T022
- **Notes:** REAL commits immediately and downgrades after `real_downgrade_ticks`; FAULT/ATTACK need `fault_attack_ticks` consecutive identical (label, subtype) except `immediate_subtypes`; UNCERTAIN immediate; counters reset on change; `changed` flag set when committed label/subtype changes.
- **Acceptance:** See T024.

### T024 — Decision tests `[P0 · 0.35 h]` ☐
- **Purpose:** Lock the truth table.
- **Files:** `tests/test_decision.py`
- **Depends:** T022, T023
- **Notes:** Hand-built `Evidence` objects covering every rule row and post-adjustment; persistence sequences; the "rate exceeded with consistent context ⇒ REAL" case; "R== NO and downstream != YES ⇒ UNCERTAIN"; "downstream NO ⇒ DOWNSTREAM_MISMATCH".
- **Acceptance:** All pass; each RULES §4 row has ≥ 1 test.

## Phase 6 — Estimator, response, community

### T025 — Estimator `[P0 · 0.35 h]` ☑
- **Purpose:** Substitute estimate (RULES §10).
- **Files:** `stageproof/estimator.py`
- **Depends:** T016
- **Notes:** Converts `Prediction` + scale to stage interval via the B rating inverse; confidence per variant; `in_use` passed in; no access to B's readings.
- **Acceptance:** Unit test: identical `Estimate` for different B readings (feeds **AC-QI**).

### T026 — Sensor state machines `[P0 · 0.5 h]` ☑
- **Purpose:** RULES §7.
- **Files:** `stageproof/response.py`
- **Depends:** T023, T005
- **Notes:** One machine per role; transitions and counters exactly per table; `needs_ack` for attack quarantines; relapse handling.
- **Acceptance:** Transition-table tests (P1 file `test_estimator_response.py`, but the logic must be correct now); verified indirectly by AC-C, AC-F.

### T027 — Alert state machine and best level `[P0 · 0.5 h]` ☑
- **Purpose:** RULES §8, §10.5, §11.
- **Files:** `stageproof/response.py`
- **Depends:** T026, T025
- **Notes:** `best_level`/`best_source`; chained transitions logged separately; retraction with `SEND_CORRECTION`; estimate-based warning streak; conservative clear using `stage_hi` for estimate basis; never auto-clear on lost data (N-16).
- **Acceptance:** Verified by AC-A/C/D/E scenario tests; unit tests in T050.

### T028 — Policy, actions, approvals `[P0 · 0.35 h]` ☑
- **Purpose:** RULES §12.
- **Files:** `stageproof/response.py`, `config/policy.yaml` (verify)
- **Depends:** T027
- **Notes:** Trigger → actions map; tiers; `demo_auto_approve_tier1`; officer methods `approve`, `reject`, `ack_release`, `officer_confirm`, `officer_clear`; Tier 2 never auto-approved; `RECOMMEND_EVACUATION` only if `evac_stage` configured.
- **Acceptance:** Policy file fully drives behavior (changing `demo_auto_approve_tier1` changes statuses); Tier 2 actions remain PENDING until a human call.

### T029 — Message rendering `[P0 · 0.15 h]` ☑
- **Purpose:** Template-based messages.
- **Files:** `stageproof/community.py`
- **Depends:** T003, T005
- **Notes:** `render(key, lang, **fields)` with missing-field errors; basis phrase lookup; no generated text.
- **Acceptance:** All DESIGN §9 templates render in en/hi with sample fields.

### T030 — Community verification `[P0 · 0.35 h]` ☑
- **Purpose:** RULES §13.
- **Files:** `stageproof/community.py`
- **Depends:** T029
- **Notes:** `VerificationManager`: open round (cooldown), prompts per volunteer language, replies (dedupe, registered only), odds update, resolution (`min_replies`, confirm/refute posteriors), timeout/escalation.
- **Acceptance:** Unit tests: two "1" replies → confirmed; one reply → unresolved; "3" ignored; unregistered ignored; timeout escalates.

## Phase 7 — Pipeline

### T031 — Runner core `[P0 · 0.75 h]` ☑
- **Purpose:** The per-tick loop (ARCHITECTURE §4.1 steps 1–10).
- **Files:** `stageproof/pipeline.py`
- **Depends:** T016, T017, T021, T023, T025, T019, T018
- **Notes:** Ring buffers, hourly rain history, trust map, `Window` construction, z-history buffers, `TickOrderError`, baselines, `TickRecord` assembly. Does not import `sim`. `replay_match` defaults to false until T020 (P1) is implemented.
- **Acceptance:** Feeding a clean replay of held-out data yields NORMAL verdicts with no transitions.

### T032 — Transport and audit integration `[P0 · 0.35 h]` ☑
- **Purpose:** Steps 2, 12, 13 (audit-before-execute).
- **Files:** `stageproof/pipeline.py`
- **Depends:** T031, T010, T011
- **Notes:** `SESSION_START` (scenario id, seed, model hash, config hash), `TRANSPORT_FAILURE`, `VERDICT_CHANGE` with evidence snapshot, `SESSION_END`; `AuditError` → `HALTED_AUDIT`; actions executed only after append.
- **Acceptance:** Forced audit write failure halts action execution and flags `halted_audit`.

### T033 — Response integration and error containment `[P0 · 0.5 h]` ☑
- **Purpose:** Steps 11, 14 and failure handling.
- **Files:** `stageproof/pipeline.py`
- **Depends:** T032, T026, T027, T028, T030
- **Notes:** Wire sensor/alert machines, policy, community, operator calls between ticks, `SENSOR_STATE`/`ALERT_STATE`/`ACTION`/`APPROVAL`/`VERIFICATION_REPLY`/`ESTIMATE_IN_USE`/`SYSTEM_ERROR` events; model exceptions contained (ARCHITECTURE §14).
- **Acceptance:** Injected model exception does not crash the run and produces `SYSTEM_ERROR` plus `UNCERTAIN`/`INSUFFICIENT_INPUTS` when notable.

## Phase 8 — Simulation and scenarios

### T034 — Sensor emulator and faults `[P0 · 0.35 h]` ☑
- **Purpose:** Realistic telemetry and fault injections.
- **Files:** `stageproof/sim/corrupt.py`
- **Depends:** T005, T004
- **Notes:** Noise (seeded) + quantization; DROPOUT, STUCK, SPIKE, NOISE_BURST, DRIFT, RANGE_OOB as pure functions with explicit start/end ticks.
- **Acceptance:** Plots/unit tests show each injection behaves as specified; deterministic for a seed.

### T035 — Attacks, transport attacks, outages `[P0 · 0.5 h]` ☑
- **Purpose:** Adversaries needed for scenarios C, D, E, F.
- **Files:** `stageproof/sim/corrupt.py`
- **Depends:** T034
- **Notes:** `FABRICATED_RAMP` (hydrograph-like rise within physical rate limits; params peak, rise hours, hold, decline); `SUPPRESSION` (blend toward baseline with strength); feed outages (A, T, C, RAIN); transport directives `UNSIGNED_INJECT` (alter value after signing), `REPLAY_PACKET` (resend an earlier signed packet).
- **Acceptance:** Attacks stay within per-tick rate limits (so they are shape-plausible); transport directives produce the intended mutations.

### T036 — Scenario model and signed stream `[P0 · 0.5 h]` ☑
- **Purpose:** Deterministic `(TickInput, TruthTick)` stream.
- **Files:** `stageproof/sim/scenarios.py`
- **Depends:** T035, T010, T009, T005
- **Notes:** Manifest IO; warmup ticks; signs packets with the correct key, or with the **compromised** (still valid) key for corrupted roles in key-compromise scenarios; applies transport directives after signing; truth kept separate; operator/volunteer scripts exposed by tick offset. **Must not import engine modules.**
- **Acceptance:** Same seed ⇒ identical stream; `TickInput` contains no truth fields (test).

### T037 — Expectation evaluator `[P0 · 0.35 h]` ☑
- **Purpose:** Machine-check RULES §19.
- **Files:** `stageproof/sim/scenarios.py`
- **Depends:** T036, T005
- **Notes:** `evaluate_expectations(scenario, records) -> list[ExpectationResult]` over committed labels, sensor states, alert outcomes, actions present/absent and baseline expectations, with tick tolerances.
- **Acceptance:** Unit test with handcrafted records covers pass and fail cases.

### T038 — Build scenario manifests `[P0 · 0.35 h]` ☐
- **Purpose:** Manifests A–F from real events.
- **Files:** `scripts/build_scenarios.py`, `data/scenarios/*.json`
- **Depends:** T037, T015
- **Notes:** Per ARCHITECTURE §9.3. Builder **asserts**: windows inside `test` split; C's `peak_stage ≥ action_stage + margin` and dry/no-rain/flat-upstream conditions; D's observed (suppressed) B stays below `action_stage` while truth exceeds it; B's true flow change satisfies the STUCK condition; E's outages cover A and T during the rise; F's transport directives scheduled with a clean margin. Compute `warmup_ticks ≥ required_history_ticks`. Volunteer script for E: V1 and V2 reply `1`.
- **Acceptance:** Six manifests written; builder fails loudly if an assertion cannot be satisfied (then escalate to the user, do not weaken RULES).

### T039 — Extended adversaries `[P1 · 0.35 h]` ☐
- **Purpose:** Held-out families for evaluation.
- **Files:** `stageproof/sim/corrupt.py`
- **Depends:** T035
- **Notes:** `REPLAY_COPY`, `SLOW_RAMP`, `STEALTH_BOUNDED`, `COORDINATED` (A and B consistently), adaptive noise.
- **Acceptance:** Unit tests for shape and bounds.

### T040 — Random labeled episodes `[P1 · 0.35 h]` ☐
- **Purpose:** Data for `evaluate.py`.
- **Files:** `stageproof/sim/scenarios.py`
- **Depends:** T039, T036
- **Notes:** Sampling in the **test** split only; tuning vs held-out families recorded in the episode metadata.
- **Acceptance:** Seeded generator reproduces identical episodes.

## Phase 9 — CLI scripts

### T041 — `run_scenario.py` `[P0 · 0.35 h]` ☐
- **Purpose:** Headless runs and debugging.
- **Files:** `scripts/run_scenario.py`
- **Depends:** T033, T038, T037
- **Notes:** Options `--scenario`, `--explain TICK`, `--export`, `--no-autopilot`; prints a compact verdict/alert/sensor timeline, key actions, expectation results; non-zero exit if any expectation fails; writes audit to `artifacts/audit/`. `--explain` prints the stored `Evidence` and `Verdict` for that tick.
- **Acceptance:** `python scripts/run_scenario.py --scenario A` exits 0 and prints the timeline.

### T042 — Gate G3: scenarios A, C, D, F pass from CLI `[P0 · 0.25 h + remediation from reserve]` ☐
- **Purpose:** MVP-0 checkpoint; no dashboard work before this.
- **Files:** none (fixes as needed in earlier modules)
- **Depends:** T041
- **Acceptance:** **AC-A, AC-C, AC-D, AC-F** pass. Record the result in MEMORY status. If a scenario fails, fix the engine or the scenario construction — never relax RULES without flagging it to the user.

### T043 — `evaluate.py` `[P1 · 0.65 h]` ☐
- **Purpose:** Comparative metrics and figures.
- **Files:** `scripts/evaluate.py`, `artifacts/eval/*`
- **Depends:** T040, T017, T042
- **Notes:** Metrics: real-extreme false-rejection rate (StageProof vs baselines), detection delay by family and magnitude, false-quarantine rate on clean episodes, estimate error and interval coverage, confusion matrix; list families not detected. Thresholds are never tuned on held-out families.
- **Acceptance:** Writes JSON and PNG outputs; report states which families failed.

## Phase 10 — Tests

### T044 — Test fixtures `[P0 · 0.15 h]` ☐
- **Files:** `tests/conftest.py`
- **Depends:** T042
- **Notes:** Session-scoped fixtures for settings, bundle, scenario runs; skip integration tests with a clear message if artifacts are missing.
- **Acceptance:** Fixtures importable; tests that need artifacts skip cleanly.

### T045 — Security tests `[P0 · 0.35 h]` ☐
- **Files:** `tests/test_security.py`
- **Depends:** T010, T011, T044
- **Acceptance:** **AC-TT** and **AC-AT** pass (including the static check for `compare_digest`).

### T046 — Scenario golden tests `[P0 · 0.5 h]` ☐
- **Files:** `tests/test_pipeline.py`
- **Depends:** T042, T044
- **Acceptance:** **AC-A, AC-B, AC-C, AC-D, AC-E, AC-F** asserted through `evaluate_expectations`.

### T047 — No-future-leakage test `[P0 · 0.15 h]` ☐
- **Files:** `tests/test_pipeline.py`
- **Depends:** T046
- **Acceptance:** **AC-NL**.

### T048 — Quarantine-isolation test `[P0 · 0.15 h]` ☐
- **Files:** `tests/test_pipeline.py`
- **Depends:** T046
- **Acceptance:** **AC-QI**.

### T049 — Determinism and layering tests `[P0 · 0.15 h]` ☐
- **Files:** `tests/test_pipeline.py`
- **Depends:** T046
- **Acceptance:** **AC-DET** and **AC-LAYER**.

### T050 — Estimator and response tests `[P1 · 0.35 h]` ☐
- **Files:** `tests/test_estimator_response.py`
- **Depends:** T025–T028
- **Acceptance:** State-machine transition tables and estimator behavior covered, including relapse, `needs_ack`, retraction, estimate-warning streak.

## Phase 11 — Dashboard

*(Starts only after Gate G3.)*

### T051 — Style and plots `[P0 · 0.5 h]` ☐
- **Files:** `dashboard/style.py`, `dashboard/plots.py`
- **Depends:** T042
- **Notes:** Palette, icons, label maps from DESIGN §5; figure builders for the four-panel chart, z-timeline, sequence plot; text summaries for accessibility.
- **Acceptance:** Figures render for a scenario's `TickRecord` history without errors.

### T052 — App shell, controls, playback `[P0 · 0.5 h]` ☐
- **Files:** `dashboard/app.py`
- **Depends:** T051
- **Notes:** Session state holds `Runner`, stream, history; sidebar per DESIGN §4.2; warmup fast-forward; timed playback (fragment with run-every); reset; header strip including audit chip; SIMULATED banner; error containment per tab.
- **Acceptance:** Selecting a scenario and pressing Play advances ticks; Reset reproduces the same run.

### T053 — Live Operations screen `[P0 · 0.75 h]` ☐
- **Files:** `dashboard/live.py`
- **Depends:** T052
- **Notes:** KPI row, main chart, why panel, sensors panel, baselines strip, action timeline, event ticker (DESIGN §6). **Gate G4:** scenarios A and C play end-to-end.
- **Acceptance:** Scenario C shows quarantine, "no public warning" and the baseline false alarm without scrolling on 1440×900.

### T054 — Community and officer panel `[P0 · 0.45 h]` ☐
- **Files:** `dashboard/live.py`
- **Depends:** T053
- **Notes:** Phone simulator with language switch, volunteer reply buttons, verification progress; officer Approve/Reject, Acknowledge & release, Confirm/Clear controls calling Runner operator methods; `DEMO ·` prefix.
- **Acceptance:** In Scenario E the verification round resolves and the COMMUNITY-basis warning appears; Tier 2 controls behave per RULES.

### T055 — Incident / Evidence screen `[P0 · 0.5 h]` ☐
- **Files:** `dashboard/evidence_view.py`
- **Depends:** T053
- **Notes:** DESIGN §7: incident selector, header, checklist, observed-vs-expected, deviation timeline, estimate card, actions and audit info.
- **Acceptance:** For Scenario C's attack incident the checklist shows ✓ message integrity and ✗ upstream consistency.

### T056 — Proof screen `[P0 · 0.5 h]` ☐
- **Files:** `dashboard/proof.py`
- **Depends:** T053, T011, T037
- **Notes:** DESIGN §8 A–F (G if available): packet inspector with stateless verification buttons, "valid signature ≠ true" card, sequence/replay plot, audit table with verify, **tamper a copy**, scenario classification table.
- **Acceptance:** Tamper demo shows `BROKEN at #k` for the copy and `VERIFIED` for the live log.

### T057 — Cached playback `[P1 · 0.15 h]` ☐
- **Files:** `dashboard/app.py`, `scripts/run_scenario.py`
- **Depends:** T056, T041
- **Notes:** Load exported `TickRecord`s; disable operator controls with an explanation chip.
- **Acceptance:** Dashboard plays a cached scenario with the engine disabled.

## Phase 12 — Documentation and polish

### T058 — README and documentation sync `[P0 · 0.35 h]` ☐
- **Files:** `README.md`, `docs/*` (only where reality differs)
- **Depends:** T056
- **Notes:** Quick start (`make setup data fit scenarios test demo`), architecture diagram, honest limitations, demo instructions. Update ARCHITECTURE §15/§13 if any interface changed.
- **Acceptance:** A new reader can run the demo from the README.

## Phase 13 — Final validation and demo rehearsal

### T059 — Offline validation `[P0 · 0.15 h]` ☐
- **Files:** `Makefile` (`demo`, `cache`), checklist in DEMO.md
- **Depends:** T058
- **Acceptance:** With networking disabled: `pytest` passes and the dashboard plays A, C, D and the audit proof. **Gate G5.**

### T060 — Rehearsals `[P0 · 0.75 h]` ☐
- **Depends:** T059
- **Notes:** Three full timed runs against DEMO.md; fix rough edges; record timing in MEMORY.
- **Acceptance:** Demo completes within the time budget in DEMO.md three times in a row.

### T061 — Backup recording and cache export `[P0 · 0.25 h]` ☐
- **Depends:** T060
- **Notes:** Export caches for A, C, D, E, F (if T057 done) and screen-record the full demo as a fallback.
- **Acceptance:** Backup video and caches exist.

### T062 — Freeze `[P0 · 0.1 h]` ☐
- **Depends:** T061
- **Notes:** Tag the repository, update MEMORY status, no further feature work.
- **Acceptance:** `make test` passes on the tagged commit.

---

## Hour-block plan (30 h)

| Block | Tasks | Focus / checkpoint |
|---|---|---|
| 0–5 h | T001–T011 (T005 before T004) | Scaffold, config, domain, data, transport, audit. **G1** |
| 5–10 h | T012–T016, T010, T034–T036 | Rating, features, Ridge, fit, bundle; simulation core. **G2** |
| 10–15 h | T018, T019, T021–T024, T017, T025, T031, T011, T032 | Checks, evidence, decision, tests, pipeline core |
| 15–20 h | T026–T030, T033, T037, T038, T041, T042 | Estimator, response, community, scenarios, CLI. **G3 (MVP-0)** |
| 20–25 h | T044–T049, T051–T054 | Tests, dashboard shell and Live screen. **G4 (MVP-1)** |
| 25–30 h | T055, T056, T058–T062 (+ P1 as time allows) | Evidence/Proof screens, docs, offline validation, rehearsals. **G5** |

The 6.2 h reserve is expected to be consumed mainly at G2 (model quality), G3 (scenario tuning) and G4 (dashboard glue). If a gate is late, apply the cut order.

## Cut order (if behind schedule)

1. T057 cached playback → 2. T050 estimator/response unit tests (keep the logic, drop extra tests) → 3. T040 random episodes → 4. T039 extended adversaries → 5. T020 replay match → 6. T043 `evaluate.py` and the Proof evaluation panel → 7. Dashboard polish (not functionality).
**Never cut:** T010, T011, T022–T024, T031–T033, T038, T042, T045–T049, T053, T056 — and do not remove community verification (T030, T054) before the items above, because it carries the inclusion element of the submission.

## Task traceability

| Requirement area | Tasks |
|---|---|
| Data (FR-01/02) | T006–T009 |
| Transport (FR-05/06) | T010 |
| Checks (FR-07–09) | T018–T020 |
| Model (FR-10/11) | T012–T016 |
| Evidence/decision (FR-12–14) | T021–T024 |
| Response/estimate (FR-15–19) | T025–T028, T033 |
| Community (FR-20/21) | T029, T030, T054 |
| Audit (FR-22/23) | T011, T032 |
| Simulation (FR-03/04) | T034–T040 |
| Dashboard (FR-24–26, 28) | T051–T057 |
| CLI (FR-27) | T041, T042 |
| Baselines (FR-29) | T017 |
| Evaluation (FR-30) | T043 |
| Tests (FR-31) | T024, T044–T050 |
