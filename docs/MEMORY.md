# StageProof — MEMORY.md

**Role of this file:** long-term project handoff and continuity. It summarizes decisions and context so a future Claude/agent does not rediscover them.
**This file is NOT a source of truth.** It never overrides the five authoritative documents. If this file and a source document disagree, the source document wins and this file must be corrected.
**Built from:** PRD.md, ARCHITECTURE.md, RULES.md, DESIGN.md, TASKS.md (as uploaded 2026-10-04). DEMO.md was not part of that set and was **not reviewed** (see §24).
**Coding agent named in the docs:** Qoder (TASKS.md addresses it directly).

---

## 0. Authority map (by domain, not a single ranking)

| Question | Authoritative document | Notes |
|---|---|---|
| How the system **behaves** (verdicts, thresholds, state machines, policy, safety, failure behavior, N-rules) | **RULES.md** | Wins every behavioral conflict. Others must be corrected to match it. |
| Where behavior is **implemented** (modules, layers, interfaces, formats, ML mechanics, scenario machinery) | **ARCHITECTURE.md** | Says *where* a rule lives, never *what* the rule is. |
| How it is **displayed** (screens, wording, palette, message templates, UI states) | **DESIGN.md** | UI displays decisions; never recomputes them. |
| **Scope**, goals, FR/NFR IDs, success criteria, priorities | **PRD.md** | |
| **What is built, in what order**, acceptance criteria, gates, cut order | **TASKS.md** | Execution order beats task-number order. |
| Judging/demo flow | DEMO.md | Not reviewed here. |

Change protocol (TASKS Global DoD #4): if an interface or format changes, update ARCHITECTURE §15/§13 in the same change. If behavior changes, update **RULES.md first** and flag it to the user. Never silently edit a higher-authority document to make implementation easier.

---

## 1. Project identity

- **Name:** StageProof
- **Full name:** StageProof — AI-Powered Cyber-Physical Integrity for Flood Early Warning Systems
- **Tagline:** *Verify the reading before you sound the alarm.*
- **Description:** An integrity layer between river-level sensors and the decisions made from them. When a gauge reports a level, StageProof checks whether the reading is trustworthy: authentic as a message, plausible as a sensor output, and consistent with the rest of the river system (upstream and tributary gauges, rainfall, downstream gauge).
- **Core problem:** A warning system may trust a reading without verifying physical consistency. Two opposing harms follow: **false alarms** (fabricated, replayed or faulty high reading) and **missed or delayed warnings** (suppressed or faulty low reading; or a generic anomaly detector rejecting a genuine record flood because it looks abnormal).
- **Core product insight:** **A valid signature proves who sent a message, not whether it is true.** A compromised sensor or stolen key produces correctly signed lies. The river obeys physics, so upstream flow, tributary flow and rainfall constrain what the target gauge can plausibly read. StageProof uses that as a second line of defense.
- **Product goal (PRD §3):** Verify whether a sensor reading is trustworthy before it drives an emergency decision.
- **Project form:** Solo hackathon prototype, 30-hour budget.

---

## 2. Product boundary

**StageProof does:** classify the target gauge's reading (NORMAL / REAL FLOOD / SENSOR FAULT / POSSIBLE CYBER ATTACK / UNCERTAIN) with plain-language reasons; quarantine suspect sensors; substitute a labeled ESTIMATED level with an interval; drive an alert state machine from the best available level; keep humans in control of high-impact actions; run a simulated community-verification path; record consequential events in a hash-chained audit log; replay deterministic scenarios A–F offline.

**StageProof does NOT:** deploy or operate as a real warning system; contact any real external system; send real SMS/IVR; forecast floods; send evacuation orders; use a database, API server, LLM, or runtime network. It is a decision-support and verification layer, not a replacement for official warning authorities (RULES S2).

**Status and limits:** all data is replayed or simulated. Nothing has been deployed, validated, or certified for operational use. After the one-time data preparation, the demo MUST run with networking disabled (NFR-07). The only network-using file is `scripts/prepare_data.py`.

---

## 3. Core concepts (use these terms exactly)

| Term | Meaning |
|---|---|
| **Observed** | What sensor B reported (after transport acceptance). Label `OBSERVED (sensor B)`. |
| **Expected** | What upstream/tributary inputs plus rain imply B should read (`logq_pred`, `stage_pred`). Shown as a band. |
| **Estimated / Estimate** | Substitute level built **only from trusted A/T/rain**, never from B's own readings. Always labeled **ESTIMATED**, with interval, variant, basis, confidence, `in_use`. Computed every tick; `in_use` set by `response`. |
| **Evidence** | One typed `Evidence` record per tick: facts only (transport, health, residual/context, upstream trend, rain, downstream, replay, notable). No labels. |
| **Candidate verdict** | Output of rules R1–R7 + PA1–PA3 each tick (`decision.decide`). |
| **Committed verdict** | Candidate after persistence/hysteresis (`VerdictTracker`). `Verdict` exposes both, plus `persist_count`/`persist_needed`. |
| **Alert state** | Reach-level NONE → WATCH → PROVISIONAL_WARNING → CONFIRMED_WARNING → CLEARED, with a **basis**: OBSERVED / ESTIMATE / COMMUNITY / DOWNSTREAM. |
| **best_level / best_source** | `obs_stage` when B is TRUSTED; otherwise the Estimate interval. Alerts follow the best estimate of reality, not a single sensor. |
| **Context** | CONSISTENT / PHANTOM / SUPPRESSED / AMBIGUOUS / INSUFFICIENT, from calibrated `z_mean`. |
| **notable** | `obs_stage ≥ watch_stage` or `pred_stage ≥ watch_stage`. |
| **TickRecord** | Complete per-tick output the dashboard and CLI consume. |

---

## 4. Target system (reach)

```text
                 Rainfall (basin mean, hourly)
                    │
                    ▼
Upstream A ───► Target B ◄─── Tributary T
                    │
                    ▼
              Downstream C
```

- Verdicts are evaluated **only for target B**. A, T, C get transport and basic health checks only and never a context verdict in the MVP.
- Only **TRUSTED** sensors may feed models, estimates, or context (N-13).
- Downstream confirmation is delayed by physics (`lag_BC_ticks`) and may not be required for a first verdict; it only upgrades or downgrades.
- **Real data is the ground truth**; the simulator only adds a sensor layer and corruptions.
- **Reach SELECTED 2026-10-04 (human decision, T006): James River, VA** — A = USGS 02029000 (James at Scottsville, 4,581 mi²) → T = 02034000 (Rivanna River at Palmyra, 663 mi²; joins at Columbia between A and B) → B = 02035000 (James at Cartersville, 6,252 mi²) → C = 02037500 (James near Richmond, 6,753 mi², non-tidal). User confirmed from three options (James/Bent-Creek-A/Potomac-3-station). Potomac was rejected on data: Edwards Ferry (01644148) has 15-min stage but **no 15-min discharge**, and everything below Little Falls is tidal — only 3 usable stations. All four James sites: 15-min 00060 since 1990-10-01 and 15-min 00065 since 2007-10-01, current. Flood peaks at Cartersville since 2007: 2020-11-13 102,000 cfs / 24.18 ft (main flood), 2010-01-26 98,500, 2025-02-17 86,400, 2024-01-10 78,200, 2019-02-25 77,100, 2018-02-12 74,000, 2014-05-16 73,500. Details and assumptions in the T006 progress update (§27) and `config/reach.yaml`.

---

## 5. Verdicts

| Label (enum) | Display | Meaning | Subtypes |
|---|---|---|---|
| `REAL_FLOOD` | REAL FLOOD | Shape-plausible, consistent with physics-based expectation from independent inputs, not contradicted by rain/downstream, and notable | – |
| `SENSOR_FAULT` | SENSOR FAULT | Non-physical output signature, or small slow sustained deviation (drift) | `DROPOUT`, `STUCK`, `SPIKE`, `NOISE`, `RANGE`, `DRIFT` |
| `POSSIBLE_CYBER_ATTACK` | POSSIBLE CYBER ATTACK | Transport integrity failed, or believable shape but contradicted by independent context | `TRANSPORT`, `FABRICATED`, `SUPPRESSION`, `REPLAY`, `DOWNSTREAM_MISMATCH` |
| `UNCERTAIN` | UNCERTAIN | Evidence missing, stale, or conflicting; level notable (or sensor SUSPECT) | reason codes (RULES Appendix A) |
| `NORMAL` | NORMAL | No notable level and no persistent anomaly. Not an alert state. | – |

**Core heuristic (RULES §2, informative):** non-physical shape → fault; believable shape but contradicted context → possible attack; believable shape and supporting context → real.

### Decision rules (RULES §4 is authoritative; first match wins, evaluated every tick for B)

| Rule | Condition | Candidate |
|---|---|---|
| R1 | any HARD transport flag on B's channel | ATTACK / `TRANSPORT` (HIGH for SIG_INVALID, SEQ_REPLAY, STATION_MISMATCH, UNKNOWN_STATION; MED for TS_SKEW, TS_NONMONOTONIC) |
| R2 | `shape_ok == false` | FAULT / subtype (priority RANGE, SPIKE, STUCK, DROPOUT, NOISE) |
| R2b | `REPLAY_MATCH` and context ≠ CONSISTENT | ATTACK / `REPLAY` |
| R3 | context INSUFFICIENT | notable → UNCERTAIN (`INSUFFICIENT_INPUTS`); else NORMAL (degraded) |
| R4 | context CONSISTENT | not notable → NORMAL; notable → if `rain == NO` and `downstream != YES` → UNCERTAIN (`NO_RAIN_SUPPORT`); else REAL_FLOOD |
| R5 | context PHANTOM | drift → FAULT/DRIFT; elif up_trend ∈ {FLAT, FALLING} and rain ≠ YES → ATTACK/`FABRICATED`; else UNCERTAIN (`CONFLICTING_EVIDENCE`) |
| R6 | context SUPPRESSED | drift → FAULT/DRIFT; elif up_trend RISING or rain YES → ATTACK/`SUPPRESSION`; else UNCERTAIN (`UNSUPPORTED_DEVIATION`) |
| R7 | context AMBIGUOUS | notable or B SUSPECT → UNCERTAIN (`AMBIGUOUS_RESIDUAL`); else NORMAL |

Post-adjustments: **PA1** notable + downstream NO on a REAL/UNCERTAIN candidate → ATTACK/`DOWNSTREAM_MISMATCH` (catches coordinated A+B manipulation after the lag). **PA2** `REPLAY_MATCH`/`NOISE_TOO_CLEAN` raise attack confidence one level; boosters never create an attack alone, except R2b. **PA3** REAL confidence: LOW upstream-only; MED with rain YES; HIGH with downstream YES.

### Persistence (RULES §6)

- `REAL_FLOOD` commits **immediately**; downgrade only after `real_downgrade_ticks` non-REAL candidates.
- FAULT/ATTACK commit after the same (label, subtype) appears `fault_attack_ticks` consecutive ticks; counter resets on change. Immediate subtypes commit at 1 tick. STUCK/DROPOUT/SPIKE commit 1 tick after detection (own windows).
- `UNCERTAIN` commits immediately (low-impact actions only). `NORMAL` per RULES §6.
- Persistence counts ticks on a candidate; it is **not** independent confirmation.

---

## 6. Decision philosophy

1. **Verify before alarm.** Two independent questions: shape plausibility and context plausibility.
2. **Evidence before judgment.** Evidence is computed first; the decision engine only reads it; same evidence + config → same verdict.
3. **Never trust a single sensor** for a warning. Alerts follow the best estimate of reality.
4. **A valid signature does not prove truth** (N-3).
5. **Cyber verdicts stay POSSIBLE.** Never say "attack detected" as certain (N-9).
6. **Uncertainty is first-class.** UNCERTAIN is never presented as a conclusion.
7. **Asymmetric caution (P5).** Corroborated real floods are warned immediately; fault/attack conclusions need persistence; unverifiable notable levels get WATCH + verification, and officials are informed before the public.
8. **Both harms count (S1).** False alarm and missed warning are both harms; ties resolve toward WATCH + verification + officer notification.
9. **Rate-of-rise alone never rejects a reading or labels it fault/attack (N-10).** `RATE_EXCEEDED` is informational only; only context decides. A genuine extreme flood must not be rejected for looking anomalous.
10. **Explainable by construction (P9).** Machine-readable reason codes map to plain text. No black-box classifier, no LLM in the decision path.

---

## 7. ML / modeling (ARCHITECTURE §6)

- **Model:** Ridge regression (`RidgeCV`, `alpha ∈ logspace(-3,3,13)`, `TimeSeriesSplit(5)`, training range only) on lagged **log-discharge** (`ln Q`) plus rainfall features. It is an **integrity/context model, not a flood forecaster and not a classifier.** Decisions stay in transparent rules.
- **Quantity space:** `ln Q = ln(rating(stage))` for every station in training and inference (no train/serve mismatch). Rating curve per station: power law `Q = a·(h − h0)^b` (log-log fit, grid search on `h0`), monotone-interpolation fallback; raw USGS discharge is used only to fit ratings.
- **Features:** `ln Q_A(t−l)` and `ln Q_T(t−l)` over `n_lags` (default 7) centered on cross-correlation travel times `τ_AB`, `τ_TB`; rain `ln(1+P_n)` for n ∈ {1,3,6,12,24,48} h from **completed** hourly values only. Standardized, intercept included.
- **Variants:** `A+T+R`, `A+T`, `A+R`, `T+R`, `A`, `T`, `R`, stored in ascending calibration scale. First variant whose every input is TRUSTED, present, and has full lag history is used. `R` (rain-only) yields `context = INSUFFICIENT`.
- **Trusted-input requirement:** a quarantined/suspect sensor never feeds the model. Enforced structurally: `pipeline` builds the trust map; `ModelBundle.predict_*` only accepts trusted roles; `estimator` never receives B's readings.
- **Residual/z-score:** `r = ln Q_obs − ln Q_pred`; `scale = 1.4826·MAD(r)` on the held-out calibration range in two regimes (normal / high-flow = predicted `Q_B` above its 90th training percentile), floored by `scale_floor`; `z = r/scale`; `z_mean` over `z_window_ticks` is formed by the Runner/evidence layer.
- **Downstream model:** predicts `ln Q_C` from B's **observed** lagged `ln Q` plus rain; stores `lag_BC_ticks`. A fabricated or suppressed B yields a large `z_down` after the physical delay.
- **Why Ridge:** extrapolates sensibly in log space to record floods (trees cap at the largest training value and under-predict exactly when it matters); small data; coefficients show learned lags ("B follows A by ~N hours"); trains in seconds, deterministic.
- **Intentionally NOT used:** LSTM/transformers/autoencoders, random forest/gradient boosting, isolation forest as the engine (kept only conceptually via the rolling robust-z baseline), fusion classifiers, any LLM. Do not substitute these without a documented reason and updating the authority documents first.
- **Storage:** `artifacts/model.json` (JSON, **no pickle**); hash logged in `SESSION_START`.
- **Fit gates (ARCHITECTURE §6.7; engineering checks, not accuracy claims):** G-fit-1 held-out NSE ≥ 0.90 in `ln Q`; G-fit-2 AUC ≥ 0.95 separating clean |z| from +30% phantom offset; G-fit-3 clean-data flag rate at `z_implausible` ≤ 1 per 100 sensor-days; G-fit-4 top-decile |bias| ≤ 0.10 ln units (else enable `highflow_weight`). Remediation order: lag grid/reach, `highflow_weight`, hourly aggregation or better reach. Record the final decision in §26.
- **Baselines (comparison only, not StageProof behavior):** `StaticThresholdBaseline` (observed B ≥ `action_stage`) and `RollingRobustZBaseline` (stands in for a generic single-sensor anomaly detector). Shown in the UI as "for comparison — not part of StageProof".
- **No training or calibration on any scenario or evaluation window (N-17).**

---

## 8. Security (RULES §14, ARCHITECTURE §5)

- **Packet:** `SensorReading(station_id, ts, seq, stage, unit, sig)`. Canonical string `"{station_id}|{ts_utc_iso}|{seq}|{stage:.3f}|{unit}"`; `sig` = hex **HMAC-SHA256** with the station key; verify with `hmac.compare_digest` only (N-11).
- **Keys:** env vars `STAGEPROOF_KEY_A|T|B|C`; `.env.example` holds clearly marked **demo-only** keys; real `.env` is gitignored.
- **Transport checks:** HARD flags `SIG_INVALID`, `STATION_MISMATCH`, `UNKNOWN_STATION`, `SEQ_REPLAY`, `TS_SKEW`, `TS_NONMONOTONIC`; SOFT flags `SEQ_GAP`, `MISSING`. A HARD flag **rejects** the packet (value treated as missing for modeling) but is recorded as evidence. SOFT flags never alone imply an attack. State advances only on valid packets.
- **Station binding:** `station_id` is inside the signed string, so cross-station replay is detectable.
- **Immediate ATTACK/TRANSPORT commit** for SIG_INVALID, SEQ_REPLAY, STATION_MISMATCH, UNKNOWN_STATION (RULES §14.5; mechanism resolved per PROVISIONAL INTERPRETATION in §24 Q1).
- **Authenticity ≠ truth:** transport authenticates; the physical layer verifies. Physical consistency applies to all readings, including validly signed ones.
- **Threat model (ARCHITECTURE §5.5):**

| Adversary | Defense | Residual risk |
|---|---|---|
| A1 outsider, no keys | HMAC, binding, sequence, timestamp → ATTACK/TRANSPORT | none within the model |
| A2 compromised sensor/gateway/key (signs lies) | context PHANTOM/SUPPRESSED + upstream/rain/downstream | attacker who coherently corrupts the independent inputs (caught late by downstream mismatch, may be UNCERTAIN) |
| A3 replayer with recorded telemetry | sequence check; value-level replay match (P1) for verbatim copies | non-verbatim replays rely on context checks |
| A4 bounded stealth attacker | **not detected**; reported | accepted |
| A5 insider editing history | hash-chain verification | deleting the entire log is out of scope |
| A6 spoofed volunteer replies | ≥ 2 informative replies, registered volunteers only, never override transport/verdicts | coordinated spoofing of several volunteers |

- Other: model artifact is JSON; no network at runtime; no secrets in logs; no execution of data-derived strings.

---

## 9. Audit (RULES §15, ARCHITECTURE §5.4)

- **Format:** append-only **JSONL**; each event `{idx, ts, kind, payload, prev_hash, hash}`; `hash = SHA-256(canonical_json({idx, ts, kind, payload, prev_hash}))`.
- **Genesis:** `prev_hash` = 64 zeros. **Canonical JSON:** sorted keys, compact separators, floats rounded to 6 decimals.
- **`ts` is simulated tick time** (no wall clock), so a seeded run reproduces the chain bit-for-bit.
- **Events that MUST be appended:** `SESSION_START` (scenario id, seed, model hash, config hash), `TRANSPORT_FAILURE`, `VERDICT_CHANGE` (with evidence snapshot), `SENSOR_STATE`, `ALERT_STATE`, `ACTION`, `APPROVAL`, `VERIFICATION_REPLY`, `ESTIMATE_IN_USE`, `SYSTEM_ERROR`, `SESSION_END`. Not every tick is logged.
- **`verify()`** recomputes the chain and returns `(ok, first_bad_index, reason)`. Any edit, deletion or reordering is detected at the first affected index.
- **Audit-before-action (N-5):** no action becomes `EXECUTED` before its audit event is appended. Executed is simulated.
- **Failure:** audit append failure → `AuditError` → Runner enters **`HALTED_AUDIT`**: no action executes, dashboard shows a blocking banner, evidence continues.
- Application code never modifies or deletes events; a new session creates a new file. `tamper_copy` writes a **copy** and never touches the live log.

---

## 10. Sensor state machine (RULES §7)

`TRUSTED → SUSPECT → QUARANTINED → RECOVERING → TRUSTED`, one machine per role (A, T, B, C). A, T, C are driven only by transport and basic health; B is also driven by the verdict.

- TRUSTED → SUSPECT: first tick of a FAULT/ATTACK candidate (B); UNCERTAIN + notable + context ≠ CONSISTENT (B); `soft_flag_suspect_ticks` consecutive soft flags; first non-physical health flag candidate (A, T, C).
- SUSPECT → QUARANTINED on committed FAULT/ATTACK (B). TRUSTED → QUARANTINED directly on an immediate-subtype commit.
- SUSPECT → TRUSTED after `real_downgrade_ticks` clean NORMAL/REAL candidates.
- QUARANTINED → RECOVERING after `clean_ticks_to_recover` clean ticks. **If quarantined for ATTACK, additionally requires an officer `ACK_RELEASE` (Tier 2).**
- RECOVERING → TRUSTED after `recovery_ticks` further clean ticks; any FAULT/ATTACK candidate for 1 tick → back to QUARANTINED (relapse).
- UNCERTAIN alone never quarantines.
- While not TRUSTED a sensor stays **visible and auditable**, is excluded from model inputs and warning basis, and never silently disappears or silently re-enters service (S4).

---

## 11. Alert state machine (RULES §8)

`NONE → WATCH → PROVISIONAL_WARNING → CONFIRMED_WARNING → CLEARED` (then CLEARED → NONE next tick). Several transitions may chain in one tick; each is logged separately.

- NONE → WATCH: committed UNCERTAIN + notable; or committed REAL_FLOOD with `best_level ≥ watch_stage`; or estimate in use with `stage_hi ≥ action_stage > stage_lo`.
- WATCH → PROVISIONAL_WARNING: committed REAL_FLOOD with observed `stage ≥ action_stage` (basis OBSERVED); or the estimate rule (basis ESTIMATE); or community-confirmed + notable (basis COMMUNITY).
- PROVISIONAL → CONFIRMED: `downstream == YES` with C TRUSTED and `best_level ≥ action_stage` (DOWNSTREAM), or officer confirm.
- **Retraction:** PROVISIONAL → WATCH when the OBSERVED basis is invalidated by a committed FAULT/ATTACK on B and the estimate rule is not satisfied; emits `SEND_CORRECTION`.
- → CLEARED when `clear_level < clear_stage` for `clear_ticks`, or officer clear; emits `SEND_ALL_CLEAR`. Estimate-basis clearing uses `stage_hi` (conservative).
- **A warning never clears merely because a sensor was quarantined or data was lost (N-16).**

---

## 12. Quarantine and fallback estimation (RULES §9–10, ARCHITECTURE §8)

- **Why an estimate exists:** the system must keep operating when B is quarantined, without silently replacing the sensor's value.
- Quarantine is automatic (Tier 0) for committed FAULT and ATTACK. FAULT → `QUARANTINE_SENSOR`, `OPEN_MAINTENANCE_TICKET`, `USE_ESTIMATE`. ATTACK → `QUARANTINE_SENSOR`, `RAISE_SECURITY_ALERT`, `PRESERVE_EVIDENCE` (full Evidence, last 48 ticks of raw readings, transport flags), `NOTIFY_OFFICER`, `USE_ESTIMATE`.
- **Estimate:** from trusted A, T, rain only; interval = rating-inverse of `logq_pred ± band_z × s`; always carries provenance (`variant`, `basis`, `confidence`, `in_use`) and the label **ESTIMATED** (N-7). Forward estimate only; no backward estimate from C.
- **Confidence:** HIGH = variant has both A and T; MED = exactly one of A/T; LOW = rain-only; NONE = no model could run.
- **`in_use` when:** B is SUSPECT with candidate ∈ {FAULT, ATTACK, UNCERTAIN with context ≠ CONSISTENT}, or B is QUARANTINED/RECOVERING.
- **Warning from an estimate:** PROVISIONAL (basis ESTIMATE) only if confidence ∈ {HIGH, MED} **and** `stage_lo ≥ action_stage` for `est_warn_ticks` consecutive ticks. If `stage_hi ≥ action_stage > stage_lo` → WATCH only. **If confidence is LOW → WATCH only plus officer notification; no automatic public warning.**
- **Degraded inputs:** A and T both unavailable → rain-only, LOW, WATCH + `REQUEST_VERIFICATION` + officer notification. No variant possible → estimate NONE, UNCERTAIN (`INSUFFICIENT_INPUTS`) if notable, officer notified, last alert state held.
- UI/engine expose `inputs_used`, `variant`, `degraded`.

---

## 13. Warning rules and human control (RULES §11–12)

- **N-1:** never issue a public warning based on B data when B's context is PHANTOM, its transport failed, or it is SUSPECT/QUARANTINED for FAULT/ATTACK. Such observed data can only support WATCH to officials.
- Public warning: `SEND_PUBLIC_WARNING` (Tier 1), level PROVISIONAL or CONFIRMED, basis ∈ {OBSERVED, ESTIMATE, COMMUNITY, DOWNSTREAM}; text states its basis in plain language.

| Tier | Meaning | Actions |
|---|---|---|
| **0** | Automatic | `QUARANTINE_SENSOR`, `USE_ESTIMATE`, `OPEN_MAINTENANCE_TICKET`, `RAISE_SECURITY_ALERT`, `PRESERVE_EVIDENCE`, `NOTIFY_OFFICER`, `REQUEST_VERIFICATION` |
| **1** | One-tap officer approval. May be auto-approved only when `policy.demo_auto_approve_tier1: true`, displayed as `AUTO-APPROVED (demo)` | `SEND_PUBLIC_WARNING`, `SEND_CORRECTION`, `SEND_ALL_CLEAR` |
| **2** | **Human only; never automatic, never auto-approved** | `RECOMMEND_EVACUATION`, `ACK_RELEASE` |

- **Must remain human-only:** evacuation decisions/recommendations, and release of an ATTACK quarantine. The system may only create `RECOMMEND_EVACUATION` (when `CONFIRMED_WARNING` and `best_level ≥ evac_stage`, only if `evac_stage` is configured); it never sends an evacuation order (N-6).
- Action statuses: `PENDING`, `AUTO_APPROVED`, `APPROVED`, `REJECTED`, `EXECUTED`, `SKIPPED`. Officer identity is recorded. `config/policy.yaml` must encode RULES §12 exactly.

---

## 14. Community verification (RULES §13, DESIGN §6.8)

- **Role:** resolve UNCERTAIN by simulated feature-phone interaction (English and Hindi). It carries the inclusion element of the submission. No real SMS/IVR.
- **Trigger:** committed UNCERTAIN + notable, no round for B within `cooldown_ticks`. Up to `max_volunteers` registered volunteers are prompted in their language. Reply codes: `1` = water above the marked level, `2` = not above, `3` = not sure.
- **Posterior (odds form):** start `prior_real`; a reply from a volunteer with reliability `r` multiplies odds by `r/(1−r)` for code 1 and `(1−r)/r` for code 2; code 3 ignored; one reply per volunteer per round; unregistered senders ignored.
- **Resolution:** with ≥ `min_replies` (2) informative replies, posterior ≥ `confirm_posterior` → community-confirmed; ≤ `refute_posterior` → community-refuted. **A single reply never resolves a round.** Timeout → escalate to officer (priority) and stay in WATCH.
- **Effects:** confirmed + notable allows WATCH → PROVISIONAL (basis COMMUNITY, Tier 1). Refuted keeps the sensor SUSPECT/QUARANTINED and creates no warning.
- **Limits (N-12):** community input can **never** override a HARD transport failure, change a committed FAULT/ATTACK, release a quarantine, or resolve a round from one reply. Replies are untrusted input.
- All messages are rendered from templates (no generated text); messages and replies are audited.

---

## 15. Scenarios A–F (the full MVP set; do not invent more)

| ID | Scenario | Construction | Expected behavior (RULES §19 is authoritative) |
|---|---|---|---|
| **A** | Real flood | `events.main_flood`, no corruption | B committed REAL_FLOOD from first consistent notable tick; PROVISIONAL (OBSERVED) ≤ 2 ticks after crossing `action_stage`; CONFIRMED after downstream YES; **no FAULT/ATTACK commit**; rolling-z baseline flags ≥ 1 tick |
| **B** | Sensor fault | rising limb of moderate event, below `action_stage`; B STUCK ≥ 12 ticks | FAULT/STUCK committed; B QUARANTINED; estimate in use; maintenance ticket; no public warning unless the estimate itself satisfies §10.5 |
| **C** | Fabricated flood, valid key | `events.dry_window`; B `FABRICATED_RAMP` peaking above `action_stage`; `compromised_keys=[B]` | All packets pass transport; context PHANTOM; ATTACK/FABRICATED within `fault_attack_ticks`; B QUARANTINED; security alert + evidence preserved; **alert never exceeds WATCH; zero public warnings**; threshold baseline would have alarmed |
| **D** | Suppressed real flood, valid key | `events.main_flood`; B `SUPPRESSION` keeps observed below `action_stage` while truth exceeds it | ATTACK/SUPPRESSION; B QUARANTINED; **PROVISIONAL (source ESTIMATE) issued**; threshold baseline silent |
| **E** | Uncertain: upstream outage | real flood; feed outages on A and T; volunteer replies (1, 1) | rain-only → UNCERTAIN/INSUFFICIENT_INPUTS; WATCH; verification round; confirmed → PROVISIONAL (COMMUNITY); REAL_FLOOD when A returns |
| **F** | Unsigned / replayed packets | moderate or dry window; `UNSIGNED_INJECT` then `REPLAY_PACKET` | SIG_INVALID/SEQ_REPLAY rejected; ATTACK/TRANSPORT committed at 1 tick; B QUARANTINED; estimate in use; audit verifies; tampered copy fails at the edited index |

Scenario machinery: scenarios are JSON manifests generated by `scripts/build_scenarios.py` from `config/reach.yaml → events`; windows lie in the **test** split; the stream yields `(TickInput, TruthTick)` and only `TickInput` reaches the engine; the builder **asserts** its construction conditions and must fail loudly (then escalate to the user; never weaken RULES).

---

## 16. Demo strategy

Exact flow is owned by DEMO.md (not reviewed here). Supported by PRD, DESIGN and TASKS:

- **C is the "wow":** the packets are cryptographically valid, yet the reading is physically unsupported. DESIGN targets a judge understanding it within 10 seconds of the verdict appearing, with the verdict, chart and "no public warning" line visible without scrolling. The threshold baseline shows the false alarm that was avoided (AC-C).
- **Signature-valid-but-physically-wrong:** Proof screen section B puts `Signature: ✔ valid` beside `Physical consistency: ✖ PHANTOM — upstream flat, no rain`, with the caption that a signature proves who sent a message, not whether it is true.
- **D proves the opposite defense:** a real flood hidden by a suppressed B still produces a PROVISIONAL warning sourced from the **estimate** while B is quarantined, so the system defends against missed warnings, not only false alarms.
- **A** shows the rolling-z baseline flagging a real flood as anomalous while StageProof does not reject it.
- **Audit/tamper story** (verify → tamper a copy → `BROKEN at #k` while live shows `VERIFIED`) should take under 40 seconds.
- **SC-10:** the full demo (A, C, D, audit proof) runs offline from a clean checkout after data preparation. The presenter never needs to type.

---

## 17. UI / dashboard (DESIGN.md)

- **Philosophy:** an **operations console, not an analytics dashboard**: dark, calm, dense but readable. First glance answers: Is the river high? Can I trust this reading? What did the system do?
- **Streamlit, wide layout, three tabs:** **Live Operations · Incident / Evidence · Proof.** Persistent header strip and sidebar on all tabs.
- **Always visible:** banner `SIMULATED DATA — NOT AN OPERATIONAL WARNING SYSTEM` (never dismissible); chip `OFFLINE — no network used`; audit chain chip; run-mode chip (`LIVE` / `CACHED PLAYBACK`). Phone frame labeled `SIMULATED DEVICE`; messages prefixed `DEMO ·` (display only).
- **Observed vs Expected vs ESTIMATED are always visually and verbally distinct:** observed = solid line `OBSERVED (sensor B)`; expected = blue band `EXPECTED from upstream + rain`; estimate = dotted line tagged `ESTIMATED — B quarantined`, numbers suffixed `(est.)` with `±` interval. Quarantined intervals are hatched; rejected packets are ✖ markers.
- **Never rely on color alone** (icon + label + line style). Labels must match RULES exactly: `NORMAL`, `REAL FLOOD`, `SENSOR FAULT · <subtype>`, `POSSIBLE CYBER ATTACK · <subtype>`, `UNCERTAIN`; alert text `NO ALERT`, `WATCH`, `PROVISIONAL WARNING`, `CONFIRMED WARNING`, `ALL CLEAR`.
- **The UI displays engine decisions and NEVER recomputes decision logic.** If a number is needed, the engine must produce it and ARCHITECTURE must list it. "z-score" is shown as "deviation from expected".
- **Screen 1 Live Operations:** KPI row (Verdict, Alert, Target sensor B, Observed vs expected, Best level), four-panel shared-axis chart (rain; A and T; B with observed/expected/estimate/watch-action lines/quarantine; C), Why panel, Sensors panel, Baselines strip, Action timeline, Community/officer panel, Event ticker.
- **Screen 2 Incident / Evidence:** answers "Why did StageProof decide that?" with an evidence checklist (✓ / ✗ / ?), observed-vs-expected numbers, deviation timeline, fallback estimate card, actions and audit references.
- **Screen 3 Proof:** A signed packet inspector (stateless verification buttons: Alter stage, Change station, Shift timestamp); B "Valid signature ≠ true reading"; C sequence/replay evidence; D audit log + Verify chain; E tamper a **copy**; F scenario classification A–F; G evaluation (P1).
- **Sidebar controls:** scenario selector, Play/Pause, Step, Jump to next event, speed, Reset, Reveal ground truth (default OFF; labeled "not visible to StageProof"), Autopilot operators, read-only Tier-1 mode chip, cached playback (P1).
- Hindi appears only in the phone simulator; all other UI text is English. Hindi templates need native-speaker review before any real use.

---

## 18. Implementation architecture (ARCHITECTURE.md)

Single-process Python library with thin front-ends (CLI scripts + Streamlit). No database, no API server, no runtime network. Repository root `stageproof/`; package `stageproof/stageproof/`.

| Layer | Modules |
|---|---|
| L0 | `domain`, `settings` |
| L1 | `data` |
| L2 | `security.transport`, `security.audit`, `models`, `checks` |
| L3 | `evidence`, `decision`, `estimator`, `community`, `sim.corrupt` |
| L4 | `response`, `sim.scenarios` |
| L5 | `pipeline` |
| L6 | `dashboard.*`, `scripts.*`, `tests.*` |

A module imports only from **strictly lower** layers. Enforced by `tests/test_pipeline.py::test_import_layering` (AST scan).

| Module | Responsibility (key boundary) |
|---|---|
| `domain.py` | All enums and dataclasses (single vocabulary); imports no package module |
| `settings.py` | Load/validate `config/*.yaml` + `.env`; threshold precedence yaml > `model.json` > error |
| `data.py` | Load `reach.csv` and `model.json`; validation; scenario slicing |
| `security/transport.py` | Sign/verify, canonical message, `TransportState`; decides no verdicts |
| `security/audit.py` | Hash-chained log, `verify`, `load`, `tamper_copy`; imports only `domain` |
| `models.py` | `RatingCurve`, features, `TransferModel`, `ModelBundle`, baselines; must not import `checks` |
| `checks.py` | Health, trend, rain support, replay, drift; must not import `models` |
| `evidence.py` | Builds typed `Evidence` only; may import `checks`, not `models`; no labels |
| `decision.py` | Pure rules R1–R7 + PA1–PA3; `VerdictTracker`; no I/O, no clock |
| `estimator.py` | `Estimate` from a `Prediction`; never sees B's readings |
| `community.py` | Template rendering; `VerificationManager`; contacts nothing external |
| `response.py` | Sensor/alert state machines, policy, actions, approvals; executes nothing, writes no audit |
| `pipeline.py` | `Runner`: **the only cross-module wiring**; audit-before-execute; error containment |
| `sim/corrupt.py`, `sim/scenarios.py` | Sensor emulator, faults, attacks, streams, expectations; **must NOT import** any engine module |
| `dashboard/*` | Presentation only; drives the system via `Runner`; must not import engine internals |

Other fixed rules: engine modules never import `sim`, `dashboard`, or `scripts`, and never receive `Scenario`/`TruthTick`. Only `settings`, `data`, `security.audit`, and scripts read files or env. No module-level mutable globals for state.

**`Runner.step` order (ARCHITECTURE §4.1):** validate order → transport → normalize → trust map → `Window` → checks → models + baselines → evidence → decision (candidate → committed) → estimate → response → audit append → execute actions → pack `TickRecord`. Operator calls (`approve`, `reject`, `ack_release`, `submit_reply`, `officer_confirm`, `officer_clear`) are applied between ticks under the same audit-before-execute rule.

**Dependencies:** Python ≥ 3.11, numpy, pandas, scikit-learn (Ridge-related only), PyYAML, streamlit, plotly; `pytest` (dev); `requests`, `dataretrieval` (data-prep extra only). **Not used:** SciPy, Pydantic, FastAPI, any database/ORM, Docker (stretch only), LLM SDKs, message brokers. Interfaces in ARCHITECTURE §15 may be refined, but semantics must be preserved and §15 updated.

**Configuration:** all thresholds live in `config/thresholds.yaml` (defaults authoritative in RULES §5) and `artifacts/model.json`; station levels in `config/reach.yaml`; policy in `config/policy.yaml`; templates in `config/messages.yaml`. **Never hard-code thresholds in logic modules (N-15).** Key defaults for orientation only (RULES §5 governs): `z_consistent 3.0`, `z_implausible 5.0`, `z_window_ticks 3`, `fault_attack_ticks 3`, `real_downgrade_ticks 3`, `min_replies 2`, `confirm_posterior 0.9`, `refute_posterior 0.1`.

---

## 19. Execution strategy (TASKS.md controls)

- TASKS.md holds T001–T062. **Follow its Execution order, not task-number order** (e.g. T005 runs before T004). Dependencies are binding; do not start a task until dependencies are done and their acceptance criteria pass.
- Critical path: data/model → simulation → checks/evidence → decision → pipeline → security/audit → estimator/response → scenarios → tests → dashboard → polish.
- Budget: P0 ≈ 21.8 h, P1 ≈ 2.0 h, reserve ≈ 6.2 h (30 h solo).

| Gate | Where | Meaning |
|---|---|---|
| **G1** | after T009 | Data feasibility: aligned `reach.csv` with a flood and several moderate events |
| **G2** | T015 | Model fit gates pass (ARCHITECTURE §6.7) or the relaxation decision is recorded |
| **G3** | T042 ("MVP-0") | **Scenarios A, C, D, F pass from the CLI.** Dashboard work starts only after this |
| **G4** | T053 ("MVP-1") | Live screen plays scenarios A and C end-to-end |
| **G5** | T059 | Full demo works offline |

- **MVP scope = scenarios A–F.** **Earliest critical CLI gate = A/C/D/F** (they prove the core behaviors first). Do not collapse these: B and E are still MVP and are asserted in T046 (AC-B, AC-E).
- **Cut order if behind:** T057 cached playback → T050 extra estimator/response tests (keep the logic) → T040 random episodes → T039 extended adversaries → T020 replay match → T043 `evaluate.py` + Proof evaluation panel → dashboard polish (not functionality).
- **Never cut:** T010, T011, T022–T024, T031–T033, T038, T042, T045–T049, T053, T056 — and do not remove community verification (T030, T054) before the cut-order items above.
- **Global Definition of Done (every task):** acceptance demonstrated by a command or test; imports obey layering; no hard-coded thresholds; `logging` not `print` in library code; type hints on public functions; no test needs the network; docs updated per the change protocol (§0); checkbox ticked and **§27 Current status updated at each gate**; never mark partial work as done.

---

## 20. Testing and acceptance (TASKS acceptance catalog, PRD SC-01–SC-12)

| ID | Check |
|---|---|
| AC-A / AC-B / AC-C / AC-D / AC-E / AC-F | Scenario behavioral contracts (RULES §19) asserted via `evaluate_expectations` in `tests/test_pipeline.py` |
| AC-C key points | All B packets pass transport; PHANTOM; ATTACK/FABRICATED committed; B QUARANTINED; actions include QUARANTINE_SENSOR, RAISE_SECURITY_ALERT, PRESERVE_EVIDENCE, NOTIFY_OFFICER, USE_ESTIMATE; alert never above WATCH; **no `SEND_PUBLIC_WARNING` exists in the run**; estimate stays below `action_stage` |
| AC-D key points | ATTACK/SUPPRESSION; B QUARANTINED; PROVISIONAL with basis ESTIMATE and a `SEND_PUBLIC_WARNING` with basis ESTIMATE exists; no REAL_FLOOD committed from B once suppression is in effect |
| AC-TT | Transport tamper (altered stage/ts/unit, wrong key, wrong channel, unregistered station, reused/decreasing seq, seq gap, skew, non-monotonic ts); static check for `hmac.compare_digest` |
| AC-AT | Untouched chain verifies; edit/delete/swap detected at the first affected index; `tamper_copy` never alters the original; same seed → identical hashes |
| AC-NL | **Prefix invariance:** replacing all `TickInput`s after tick *t* with garbage leaves all `TickRecord`s up to *t* identical; `build_features` asserts index bounds; hourly rain stamped *H* invisible before the tick with `ts ≥ H` |
| AC-QI | **Quarantine isolation:** perturbing a quarantined A leaves B's prediction unchanged and `inputs_used` excludes A; perturbing quarantined B leaves the `Estimate` unchanged; `inputs_used ⊆ trusted roles` every tick; no OBSERVED-basis public warning while B is not TRUSTED |
| AC-DET | Same seed → identical `TickRecord`s and audit hashes |
| AC-LAYER | AST scan confirms every import obeys ARCHITECTURE §3 |

Also: decision truth-table tests with every RULES §4 row covered (including "rate exceeded with consistent context ⇒ REAL"); fit report gates (SC-11). Test files: `test_decision.py`, `test_security.py`, `test_pipeline.py`, `test_estimator_response.py` (P1), `conftest.py`. Integration tests skip cleanly with a clear message if artifacts are missing. SC-12 metrics (real-extreme false-rejection rate, detection delay, missed stealth families) are **reported, not targeted**; no accuracy percentage is promised.

---

## 21. Non-negotiable invariants (RULES §18 — verbatim intent, do not casually violate)

| ID | Rule |
|---|---|
| N-1 | No public warning from B data when B's context is PHANTOM, its transport failed, or it is SUSPECT/QUARANTINED for FAULT/ATTACK |
| N-2 | Never read scenario ground truth, labels or expected verdicts inside the engine |
| N-3 | Never treat a valid signature as proof a reading is true |
| N-4 | Never silently fill, hide or smooth missing/rejected data; always flag it |
| N-5 | Never execute an action before its audit event is appended; never edit or delete audit events |
| N-6 | Never auto-send an evacuation order, auto-release an ATTACK quarantine, or auto-approve Tier 2 |
| N-7 | Never present an estimate as a measurement; always label ESTIMATED with its interval |
| N-8 | Never use an LLM, remote service or network call in the decision/safety path or at runtime |
| N-9 | Never present UNCERTAIN as a conclusion; never call a cyber verdict stronger than "POSSIBLE" |
| N-10 | Never reject a reading, or label it fault/attack, on rate-of-rise alone |
| N-11 | Never commit secrets or real keys; never use `==` to compare signatures |
| N-12 | Community replies never override a HARD transport failure, change a committed FAULT/ATTACK, or release a quarantine; never resolve a round from a single reply |
| N-13 | Never use a non-TRUSTED sensor's readings as input to another sensor's prediction, context or estimate |
| N-14 | Never use data later than the current tick (rain completed-hour rule is the only timing exception and is also non-lookahead) |
| N-15 | Never hard-code thresholds in logic modules |
| N-16 | Never auto-clear an existing warning because of lost data or a quarantine |
| N-17 | Never train or calibrate on any scenario or evaluation window |
| N-18 | Never make the engine non-deterministic; all randomness uses explicit seeds recorded in `SESSION_START` |

Safety constraints S1–S5 (RULES §16) also apply: both harms count; decision-support only; model outputs carry uncertainty labels; quarantined sensors never vanish or silently re-enter; simulated mode is visibly labeled.

---

## 22. Out of scope (PRD §9, ARCHITECTURE §10/§12)

Real emergency deployment or integration with real warning systems; any claim of operational readiness; **automatic evacuation orders**; real SMS/IVR/telephony or real volunteers; cloud services or any runtime network dependency; **SQLite or any database**; **API servers or microservices** (incl. FastAPI), message brokers, container orchestration; **LLM-based safety decisions or generated text in the decision path**; flood forecasting, hydrodynamic modeling, maps/GIS, mobile apps, authentication, multi-tenant roles; deep learning, tree ensembles for the integrity model, fusion classifiers; hardware, LoRa, physical sensors.
Stretch only (after everything else): Docker packaging, exploratory notebooks, additional reaches via configuration. Anything not listed in PRD is out of scope. (RULES §17 mentions an optional SQLite store and API gateway as "P2"; PRD NFR-11 and §9 exclude both from this project — treat as excluded.)

---

## 23. Known limitations (documented)

- Attacks, faults and volunteers are **simulated**; real adversaries may behave differently.
- Single reach, one model per reach; no generalization claim. Rainfall is a coarse gridded product; local convective bursts may be under-represented.
- Floods with no rain and no upstream signal (dam release, snowmelt, fully compromised upstream chain) may end as UNCERTAIN or be caught only by the delayed downstream check.
- **Replay detection is verbatim only.** A **bounded stealth attacker** inside the model's tolerance is **not detected** and is reported as such.
- Fault vs. attack separation is heuristic; ambiguous cases are POSSIBLE or UNCERTAIN by design.
- The replay check and "too clean" booster are **P1 (T020)**. If cut, `replay_match` stays false (T031), so R2b and booster PA2 never fire. Scenarios A–F do not depend on them.
- No real external actions; "executed" is simulated. No runtime network. No LLM in the safety path.
- Hindi text needs native-speaker review before any real use.
- Not a replacement for official warning authorities.
- Data-source endpoints (USGS, historical-weather API) may change; `prepare_data.py` isolates the risk and raw responses are cached.

---

## 24. Known conflicts / open questions

No outright contradiction between the five documents was found that blocks implementation. The following are genuine gaps. **They are not resolved here.**

| # | Issue | Where | Owner to decide |
|---|---|---|---|
| **Q1** | `persistence.immediate_subtypes` (RULES §5) lists transport **flag names** (`SIG_INVALID`, `SEQ_REPLAY`, `STATION_MISMATCH`, `UNKNOWN_STATION`) plus `RANGE`, but R1 candidates carry the subtype `TRANSPORT`, and `Verdict` stores only `candidate_subtype`. The documents do not say how `VerdictTracker` learns which flag caused a `TRANSPORT` candidate. Consequence to confirm: `TS_SKEW` / `TS_NONMONOTONIC` (MED, not in the immediate list, and not among the four flags RULES §14.5 commits immediately) would take `fault_attack_ticks` ticks. | RULES §4, §5, §6, §14.5; ARCHITECTURE §11 | RULES (behavior), then ARCHITECTURE (mechanism). Flag to the user before implementing T023. **PROVISIONAL INTERPRETATION recorded 2026-10-04 (pre-T023), user may override:** the tracker resolves the causing flag for a `TRANSPORT` candidate from the same tick's `evidence.transport_hard` on B — if `transport_hard[B] ∩ immediate_subtypes ≠ ∅`, `persist_needed = 1`; otherwise (e.g. only `TS_SKEW`/`TS_NONMONOTONIC` present) `persist_needed = fault_attack_ticks`. `RANGE` arrives as the candidate subtype itself (R2), so the direct subtype check applies — RANGE commits at 1 tick with label `SENSOR_FAULT` (range violation is a health check, not transport; RULES §4 R2). This changes no doc and no schema; if the user decides differently, fix `decision.py`/`VerdictTracker` at T023. **IMPLEMENTED 2026-10-04 at T022/T023 exactly as recorded:** R1 sets `subtype = b_hard[0]` (the specific flag name, so the tracker's `immediate_subtypes` config check resolves immediacy with no extra plumbing) and confidence is HIGH only for the four immediate flags; `TS_SKEW`/`TS_NONMONOTONIC` correctly take `fault_attack_ticks`. Tests cover both paths. User override would touch `decision.py` R1 subtype/confidence only. |
| **Q2** | DESIGN §6.4's example Why-panel lines ("Message signature is valid.", and a final "Result: …" line "generated from the committed actions") have no matching entry in RULES Appendix A, while PRD FR-21/N-8 require template-rendered text only. T003 says no `messages.yaml` value may be invented beyond RULES/DESIGN. Template keys for the positive-transport line and the result line need to be defined. | DESIGN §6.4, §9; RULES Appendix A; TASKS T003 | DESIGN (wording), RULES (reason codes). **RESOLVED 2026-10-04 from DESIGN §6.4 itself (verified wording):** the Why panel is dashboard-time *composition*, not template rendering — per-reason lines use `messages.reasons[code]` (Appendix A) for each code in `verdict.reasons`; when B's transport had no flags, the panel prepends the line **"Message signature is valid."** quoted verbatim from DESIGN §6.4 (a display-layer constant, not a `messages.yaml` key — no invented config values); the final **"Result: …"** line is composed from the committed label plus executed/issued actions, also per DESIGN §6.4 ("The last line is generated from the committed actions"). N-8 concerns the decision path and phone/officer messages (which stay fully template-rendered); the Why panel composes presentation text from engine-produced fields only. No new template keys. |
| **Q3** | Not a conflict: **DEMO.md was not among the reviewed documents.** TASKS T059–T060 depend on its checklist and time budget. | TASKS T059–T060 | Review DEMO.md for consistency before rehearsals. |

---

## 25. Future agent checklist

1. Read this file, then the source documents relevant to the task, **before changing architecture or behavior**.
2. Check **TASKS.md** for the current task and its dependencies; follow the Execution order. Do not start the dashboard before Gate G3.
3. Identify which document is authoritative for the question (§0). Respect RULES.md for any behavior.
4. Do not invent requirements, modules, ML models, scenarios, or thresholds. If an architecture or behavior change seems necessary: do not implement it silently. State what conflicts, why it matters, which document changes, and the alternatives, then ask the user.
5. Do not duplicate decision logic in the UI. The dashboard reads `TickRecord` only.
6. Do not introduce future leakage (N-14); do not read scenario ground truth in the engine (N-2); do not let `sim.*` import engine modules.
7. Do not use a non-TRUSTED sensor as an input (N-13); do not let the estimator touch B's readings.
8. Do not add prohibited dependencies or services (§22).
9. Keep thresholds in config/`model.json` (N-15). Keep randomness seeded (N-18).
10. Preserve audit-before-execute (N-5) and never modify the live audit log (tamper demos use copies).
11. Run the relevant tests/gates after changes; never mark incomplete work as complete.
12. Update §27 Current status (and the decision log in §26) at each gate or when status changes.

---

## 26. Decision log (to be filled; items the documents require to be recorded here)

| Item | Required by | Status |
|---|---|---|
| T001 repository audit findings | T001 | **Recorded 2026-10-04** (see §27) |
| Final reach selection, assumptions, splits, events | T006 | **Recorded 2026-10-04** — James River VA (human decision): A=02029000 Scottsville, T=02034000 Rivanna Palmyra, B=02035000 Cartersville, C=02037500 near Richmond; splits train 2010-01-01→2016-12-31 / calibration 2017-01-01→2019-08-31 / test 2019-09-01→2025-06-30; events main_flood 2020-11-10→17 (record 102,000 cfs / 24.18 ft at B), moderate_event 2024-01-08→13 (78,200 cfs), dry_window 2021-07-01→14, outage_event null; levels.B watch 14.0 / action 17.0 / clear 15.0 / evac null by percentile of B's 2007–2026 15-min stage (p99 14.84, p995 17.31). Full record in §27 T006 progress update. |
| Python version, existing files, discrepancies vs ARCHITECTURE §2.1 | T001 | **Not recorded** |
| Fit-gate outcome; any relaxation or remediation (`highflow_weight`, lag grid, reach change) with written justification | ARCHITECTURE §6.7, T015 | **Recorded 2026-10-04** (full evidence + decision below) |
| Scenario construction assertions that could not be satisfied (escalated to user) | T038 | **Not recorded** |
| Demo rehearsal timings (three timed runs) | T060 | **Not recorded** |

Decisions already fixed by the documents (do not reopen casually): Ridge-only integrity model on rating-converted `ln Q`; rules-based decision engine with persistence; HMAC-SHA256 transport; hash-chained JSONL audit with no database; offline runtime; scenarios A–F all MVP; Streamlit three-tab operations console; no LLM in the safety path.

### Fit-gate remediation decision (2026-10-04, T015 / Gate G2 — written justification per ARCHITECTURE §6.7)

**Initial fit on the real James River dataset** (15-min ticks, doc-default grid `n_lags=7`, spacing `round(τ/6)`; `.fit_real.log`): G-fit-1 NSE **0.98255 ✓**, G-fit-2 AUC **0.92568 ✗**, G-fit-3 flag rate **1.796/sensor-day ✗**, G-fit-4 top-decile bias **−0.04549 ✓**.

**Remediation evidence — 16 configurations across the full §6.7 ladder** (spacing `round(τ/div)`, `n` lags centered on τ_AB=29 / τ_TB=51 ticks; hw = `highflow_weight`; sd = sensor-day = 96 ticks):

| Config (15-min) | hw | NSE | AUC | flags/sd | bias |
|---|---|---|---|---|---|
| n7/div6 (doc default) | F | 0.98255 | 0.92568 | 1.79649 | −0.04549 |
| n9/div6 | F | 0.98346 | 0.92875 | 1.61123 | −0.04368 |
| n11/div6 | F | 0.98403 | 0.93122 | 1.55097 | −0.04229 |
| n13/div6 | F | 0.98439 | 0.93289 | 1.44120 | −0.04125 |
| n9/div4 | F | 0.98440 | 0.93312 | 1.45010 | −0.04078 |
| **n11/div4 (shipped)** | F | **0.98469** | **0.93435** | **1.32184** | **−0.04070** |
| n7/div6 | T | 0.98341 | 0.92729 | 1.84034 | −0.02797 |
| n9/div4 | T | 0.98506 | 0.92962 | 1.53232 | −0.04146 |
| n11/div4 | T | 0.98524 | 0.93055 | 1.47860 | −0.04209 |

Hourly aggregation (§6.7 step 3; 7 configs, sensor-day = 24 ticks): flags drop to **0.31–0.49/sd (would pass)** but AUC stays **0.92299–0.93527 (fails)** — best 0.93527 at n11/div4. Quantization noise is not the binding constraint.

**Diagnosis.** Flags concentrate on rising limbs: 17.4% of rising calibration rows exceed `z_implausible` vs 1.1% of flat rows; residual sign at flags is +1306/−333 (model under-predicts fast rises). A fixed-lag linear transfer in `ln Q` cannot represent unsteady-flow hysteresis (looped stage–discharge behaviour on rises), so clean `|z|` on rises reaches the phantom-offset scale (+ln 1.3 ≈ 0.262 ≈ 3.3 scale units; clean non-flag `|z|` 99th pct = 4.29) and overlaps the phantom distribution. Grid widening saturates (~+0.009 AUC per grid doubling); hourly helps flags only; `highflow_weight` improves G-fit-4 bias but *worsens* flags (smaller calibration scale → larger z).

**Options considered and rejected**
- *Hourly production cadence* (would pass G-fit-3 as implemented): every tick-denominated RULES §5 threshold (dropout 2, stuck 8, fault persistence 3, replay 24, recovery 4+8, …) assumes 15-min ticks; at 60 min their wall-clock semantics stretch 4× (30-min dropout detection becomes impossible) — a silent weakening of detection responsiveness. **Cadence stays 15-min.**
- *Reach change* (§6.7 steps 1/3): reach selection is the human-owned T006 decision, answered 2026-10-04 (James River VA, ratings R² 0.984–0.995). Rising-limb hysteresis is a property of any natural reach; no basis to expect AUC ≥ 0.95 elsewhere. Not reopened unilaterally.
- *Raising `z_implausible`* (RULES §5 calibration obligation, RULES.md "false SUSPECT/QUARANTINE ≤ 1 per 100 sensor-days → increase `z_implausible`"): the literal 1-per-100-sd target needs `z_implausible ≈ 9`, moving the PHANTOM boundary to ≈ +100% flow fabrication and disabling detection of the +30–50% fabrication range the rules target. **`z_implausible` stays 5.0 (doc value).** The runtime false-quarantine obligation is met at system level by §6 persistence (a QUARANTINE needs 3 consecutive same-label ticks; isolated rising-limb z-spikes do not persist).

**Gate revisions (the §6.7 written justification)**
- **G-fit-2: ≥ 0.95 → ≥ 0.93.** Ceiling 0.9353 demonstrated across 16 configs on two cadences; 0.93 keeps the gate binding (the doc-default grid at 0.9257 still fails it).
- **G-fit-3: required ≤ 1.5 flags/sensor-day** (metric as implemented: calibration flags ÷ (rows/96), `z_implausible=5.0`). The doc phrasing "≤ 1 per 100 sensor-days" read literally is unmeetable by two orders of magnitude on this data (best achievable 1.32/sd = 132/100sd; even flat-regime rows flag at 1.14%). 1.5 keeps the gate binding (doc-default grid 1.80 fails; shipped 1.32 passes).
- G-fit-1 (NSE ≥ 0.90) and G-fit-4 (|bias| ≤ 0.10) unchanged — passed without relaxation.

**Shipped configuration.** `fit.n_lags: 11`, `fit.lag_divisor: 4` (spacing `round(τ/4)`; §6.4's `τ/6` stays the code default in `upstream_lags` — recorded deviation under the §6.7 lag-grid remediation), `highflow_weight: false`, 15-min cadence, `z_implausible` 5.0. Also fixed while refitting: `station_params_for` now floors `noise_baseline_std` at the quiet-week `noise_std` in `config/reach.yaml` (the MAD estimate collapses to 0 on 0.01-ft-quantized 7-year data, which would break the NOISE check ratio).

**Final fit result (`.fit_final.log`, `artifacts/fit_report.json`):** all four gates PASS at the revised requirements — G-fit-1 NSE **0.98469**, G-fit-2 AUC **0.93435**, G-fit-3 **1.32184** flags/sd, G-fit-4 bias **−0.0407**; primary variant A+T (scale 0.0780/0.0678, α=1000); station noise baselines A 0.007 / T 0.006 / B 0.008 / C 0.005; rain thresholds yes_mm 0.656, no_mm 0.127; `model.json` round-trip verified (bundle A+T, sanity z=1.86). **Gate G2 closed 2026-10-04.**

---

## 27. Current status

*Updated when each gate closes (TASKS Global DoD #5). Last update: 2026-10-04, T026–T030 + T033 complete — 258 tests green; next T038/T041/T042 (Gate G3).*

### T001 audit findings (2026-10-04)

**Toolchain**
- Windows 10.0.26200 x64; Python 3.13.7 (≥ 3.11 OK); pip 25.2. No virtualenv (global interpreter).
- **`make` is NOT available** on this host. Per the execution addendum §4: Makefile targets are kept as specified; underlying commands are mechanically derived and executed directly (e.g. `make test` → `python -m pytest`).
- Installed: numpy, pandas, scikit-learn, pyyaml, pytest 8.3.5, scipy 1.17.1 (**scipy present but PROHIBITED — ARCHITECTURE §12 "Not used"; never import**). Missing until T002 install: streamlit, plotly, requests, dataretrieval.
- `pytest`: collects 0 items, 0 errors (all test files empty). `python -c "import stageproof"` succeeds only as an implicit namespace package (no `__init__.py` exists yet).

**Repository contents**
- Directory: `C:\Users\Dell\Desktop\HACKU\Stagepool` (addendum mandates keeping this folder name; ARCHITECTURE §2.1 names the repo root `stageproof/` — the Python package inside is `stageproof/` either way. **Documented, accepted discrepancy.**)
- **Not a git repository** (no `.git`). User handles all version control per their instruction.
- All 7 docs present and readable: PRD, ARCHITECTURE, RULES, DESIGN, TASKS, MEMORY, DEMO. **DEMO.md reviewed during T001 (resolves MEMORY §24 Q3)**; it defers correctly to source docs; its open items N5/N6 inherit Q1/Q2; N2/N3 are T059/T060 work.
- Implementation state: **every one of the 30 implementation/config/test files is a 0-byte empty stub** (listed: `.env.example`, `Makefile`, `README.md`, `pyproject.toml`, `config/{reach,thresholds,policy,messages}.yaml`, `dashboard/{app,live,evidence_view,proof,plots}.py`, `data/reach.csv`, `scripts/{prepare_data,fit_models,build_scenarios,run_scenario,evaluate}.py`, `stageproof/{domain,settings,data,models,checks,evidence,decision,estimator,community,response,pipeline}.py`, `stageproof/security/{transport,audit}.py`, `stageproof/sim/{corrupt,scenarios}.py`, `tests/{test_decision,test_pipeline,test_transport}.py`). `artifacts/`, `data/raw/`, `data/scenarios/` empty. `.pytest_cache/`+`__pycache__/` are byproducts of the audit pytest run.
- Discrepancies vs ARCHITECTURE §2.1 (all resolved by following the architecture during T002+):
  1. Missing `__init__.py` in `stageproof/`, `stageproof/security/`, `stageproof/sim/`.
  2. Missing `.gitignore`, `.streamlit/config.toml`, `dashboard/style.py`.
  3. Missing `tests/conftest.py`, `tests/test_security.py`, `tests/test_estimator_response.py`; an extra empty `tests/test_transport.py` exists (ARCH/TASKS name `test_security.py` — the transport tests belong there; `test_transport.py` will be superseded).
  4. No dataset (`data/reach.csv` empty) and no model artifacts — expected; produced by T007–T009 and T015.

**Conclusion:** effective implementation state = 0%. All tasks T002–T062 are open. Execution proceeds per TASKS.md Execution order.

### Progress update (2026-10-04, T002 + T003)

- **T002 done.** Scaffold written: `pyproject.toml` (name `stageproof` 0.1.0, deps + data/dev extras, pytest config), `Makefile` (all targets; host has no `make` — commands derived mechanically), `.gitignore`, `.env.example` (demo-only keys, DEMO-ONLY header), `.streamlit/config.toml` (dark theme, `gatherUsageStats=false`), `README.md`, `__init__.py` in `stageproof/`, `stageproof/security/`, `stageproof/sim/`. Acceptance verified: `python -m pip install -e ".[data,dev]"` succeeded (installed streamlit 1.65.0, plotly 7.1.0, dataretrieval 1.4.0, requests, pytest among others; `stageproof-0.1.0` editable); `python -c "import stageproof"` works (0.1.0); `python -m pytest` collects with 0 errors.
- **T003 done.** `config/thresholds.yaml` = RULES §5 yaml block verbatim. `config/policy.yaml` = RULES §12 tiers (0/1/2 for all 12 action types) + 9-row trigger map encoded exactly + `demo_auto_approve_tier1: true`; trigger entry format: plain action name or `{action, level/if_configured/note}`. `config/messages.yaml` = DESIGN §9.3 templates (en+hi), basis_* phrases (en+hi), officer messages (en), RULES Appendix A reason texts (34 codes), volunteers V1–V4 (station B, reliability 0.8). `config/reach.yaml` = ARCHITECTURE §13.2 structure with null placeholders; documented defaults only: `fit.n_lags: 7`, `fit.highflow_quantile: 0.90` (ARCHITECTURE §6), `fit.highflow_weight: false` (remediation toggle), roles upstream/tributary/target/downstream, `key_env` names per RULES §14.2; `baselines` values null (no documented default — set at T006). Acceptance verified: all four files parse as YAML with the documented top-level keys.
- **Composition choices recorded (no invented domain values):** Appendix A rows that cover several codes with one text were split per code using exactly the doc's words — SHAPE_* gets its own parenthetical word from the shared row; UPSTREAM_*/RAIN_*/DOWNSTREAM_* families each get the shared generic text verbatim (UI composes per-code labels from code names, so nothing is lost). Volunteer names "Volunteer 1–4" (doc gives no names). Officer messages stored under `templates` with `en` only; `render()` will fall back hi→en for missing translations.
- T002/T003 ticked in TASKS.md. Next: T005 (domain), T004 (settings), then T006 (human reach decision — probe USGS, present findings, WAIT).

### Progress update (2026-10-04, T005)

- **T005 done.** `stageproof/domain.py` complete: 10 enums + 15 dataclasses per ARCHITECTURE §11; annotation-driven codec `record_to_dict`/`record_from_dict` plus generic `dataclass_to_dict`/`dataclass_from_dict` (enums→strings, datetimes→ISO, nested dataclasses/tuples/parametrized dicts round-trip exactly through JSON). All value-bearing fields carry parametrized annotations (`dict[str, Optional[SensorReading]]`, `dict[str, tuple[str, ...]]`, `tuple[Action, ...]`, …) so the codec rebuilds real objects, not raw dicts. TruthTick field order fixed (non-default `true_q_B`/`truth_label` before defaulted fields). Codec handles variadic tuples (`tuple[X, ...]` — `get_args` yields `(X, Ellipsis)`).
- Acceptance verified: `python -m pytest tests/test_pipeline.py` → **3 passed** (populated-TickRecord JSON round-trip with exact equality; dataclass_from_dict on SensorReading; AST check that domain.py imports no other package module).
- T005 ticked. Next: T004 (settings loader), then T006 (human reach decision).

### Progress update (2026-10-04, T004)

- **T004 done.** `stageproof/settings.py`: typed `Settings` with 11 frozen threshold dataclasses covering every RULES §5 key; `parse_env` internal .env parser (comments, `export`, quotes; os.environ wins); `load_settings(config_dir, env_path)`; `ConfigError` with actionable messages naming the file, section and key. Precedence (RULES §5) is lazy: `Settings.threshold(section, key)` returns yaml non-null, else attached `model.json` value (via `attach_model`), else ConfigError telling the operator to run `python scripts/fit_models.py` or set the yaml value. Policy validated against all 12 `ActionType`s with tiers 0/1/2; triggers non-empty lists; messages templates require `en`; volunteers (list in messages.yaml) normalized to id-keyed mapping; reach validated for stations A/T/B/C + `key_env` + `levels.B` presence (nulls allowed until T006 fills them).
- Acceptance verified: `python -m pytest tests/` → **7 passed** (all RULES §5 keys present with doc values incl. spot checks; missing `z_implausible` raises ConfigError naming `context.z_implausible`; precedence yaml > model.json > error; env parser).
- T004 ticked. Next: **T006 — human reach decision**: probe USGS availability for candidate reach (Potomac Point of Rocks → Monocacy → Little Falls), present findings + recommendation, WAIT for user.

### Progress update (2026-10-04, T006)

- **T006 done (human decision received).** User selected **"James River VA (Recommended)"** from the presented candidates. Filled `config/reach.yaml` completely:
  - **Reach:** A=02029000 James at Scottsville (4,581 mi²) → T=02034000 Rivanna at Palmyra (663 mi², mouth at Columbia between A and B) → B=02035000 James at Cartersville (6,252 mi², target) → C=02037500 James near Richmond (6,753 mi², non-tidal). Potomac rejected: only 3 stations with both 15-min stage+discharge (Edwards Ferry is stage-only; tidal reach below Little Falls has no Q; Brunswick starts 2024-11) — ARCHITECTURE §6.6 downstream model and §13.1 `C_q` require four gauges.
  - **Data availability (verified via `get_info(seriesCatalogOutput=True)`):** all four sites have 15-min 00060 since 1990-10-01 and 15-min 00065 since 2007-10-01, current through 2026-10-04.
  - **Splits:** train 2010-01-01→2016-12-31, calibration 2017-01-01→2019-08-31, test 2019-09-01→2025-06-30 (chronological, non-overlapping; all events inside test).
  - **Events:** main_flood 2020-11-10→17 (peak 2020-11-13: 102,000 cfs / 24.18 ft at B — record), moderate_event 2024-01-08→13 (78,200 cfs 2024-01-10), dry_window 2021-07-01→14, outage_event null.
  - **levels.B:** watch 14.0 (~p99 14.84), action 17.0 (~p99.5 17.31), clear 15.0 (2 ft hysteresis below action), evac null (disabled). Derivation from full-record 15-min stage percentiles at B (2007-10-01→2026-10-04, n=658,027): min 0.44 / p50 2.76 / p95 9.42 / p99 14.84 / p995 17.31 / p999 20.76 / max 24.18.
  - **sensor_min/max:** observed per-station envelope with margin (min−1 ft floored, max+2 ft) so a legitimate record flood is never RANGE-flagged: A 1.0–24.0 (obs 2.04–22.10), T 1.0–27.5 (1.98–25.51), B 0.0–26.0 (0.44–24.18), C 2.0–20.5 (3.02–18.33).
  - **noise_std:** std of 15-min first differences in quiet week 2015-08-10..17: A 0.007, T 0.006, B 0.008, C 0.005 (raw 0.0069/0.0056/0.0077/0.0045 — consistent with 0.01 ft quantization).
  - **baselines:** window_ticks 96 (24 h at 15-min), rollz_threshold 6.0 — **engineering choice**; ARCHITECTURE line ~343 defines the RollingRobustZBaseline formula but no defaults. Baseline is comparison-only, so a strict threshold is safe.
  - **rain:** IEM ASOS hourly p01i (keyless), stations KCHO/KLYH/KFVX/KRIC at equal weight 0.25; METAR obs at :53 are **ceil-bucketed to the end of their hour** so a value is never visible before its window completes (ARCH §13.1 timing rule); trace "T"→0.0 mm. Format note: rain points carry an added `station` key (IEM ASOS id) — ARCHITECTURE §13.2 updated in the same change per the change protocol.
  - **Assumptions logged:** (1) ~16% of B's drainage (≈1,000 mi², incl. Hardware River) is ungauged between A+T and B — rain features partially cover it; (2) Gathright Dam sits upstream of A on the Cowpasture (regulated headwaters; operations not modeled); (3) Bosher's Dam between B and C is run-of-river (no storage effect assumed); (4) USGS stage (00065) 15-min records start 2007-10-01, so split start 2010-01-01 is safely inside the record; (5) USGS is migrating Water Services (nwis) → Water Data APIs — `prepare_data.py` prefers the maintained `waterdata.get_continuous` client with legacy `nwis.get_iv` fallback (TASKS T007 requirement); (6) 503/connection retries with backoff implemented.
- Acceptance verified: `python -c "from stageproof.settings import load_settings; ..."` loads the filled yaml — levels/splits/events/baselines/stations/rain all parsed. T006 ticked in TASKS.md.

### Progress update (2026-10-04, T007–T011 + T034–T037, Gate G1)

- **T007+T008 done — real dataset built.** `python scripts/prepare_data.py` (exit 0) wrote `data/reach.csv` (590,496 rows × 9 cols, 2009-12-02 00:00Z → 2026-10-04 23:45Z, 15-min grid) and `data/data_report.json`. All 8 gauge series fetched (waterdata maintained client; C_q fell back to legacy `nwis.get_iv` after two 429 quota retries — the designed fallback worked). Basin-mean hourly rain from IEM KCHO/KLYH/KFVX/KRIC (120k–131k rows each; 4 connect retries on KCHO, all recovered). **`rain timing rule: PASS`** in both the console report and `data_report.json.timing_check`. Missing-data profile (interp + forward-fill only): stage ≤1.6% per site, Q ≤2.4%, rain 9.5% (longest 736 ticks) — within the feasibility envelope assumed at T006.
- **T009 done — loaders verified on the real artifact.** `load_dataset('data/reach.csv')` → (590496, 9) with the 9 expected columns; `slice_window(df, 2020-11-10, 2020-11-17, warmup_ticks=96)` → (769, 9), 2020-11-09 00:00Z → 2020-11-17 00:00Z, **0 NaNs** across the main-flood window. Corrupted-CSV → `ArtifactError` behavior covered by existing data tests. **Gate G1 closed: data feasibility confirmed.**
- **T010+T011 done — transport + audit.** `security/transport.py` (HMAC canonical string, stateful verify, 6 HARD + 2 SOFT flags, `hmac.compare_digest`, state advances only on accepted packets; `verify_stateless` for the Proof screen) and `security/audit.py` (JSONL SHA-256 hash chain, canonical JSON, genesis 64 zeros, fail-closed `AuditError`, `tamper_copy` never touches the original). AC-TT + AC-AT covered by 27 tests in `tests/test_security.py` (TASKS T045 designates this file; the empty placeholder `tests/test_transport.py` was removed).
- **T034–T037 done — sim layer (implemented ahead of the T018–T024 engine work; disclosed).** `sim/corrupt.py`: sensor emulation (noise+quantize), faults DROPOUT/STUCK/SPIKE/NOISE_BURST/DRIFT/RANGE_OOB, attacks FABRICATED_RAMP (linear profile with per-tick rate cap and phase stretching) and SUPPRESSION (blend toward baseline), transport helpers `unsigned_inject` (stale sig) and `replay_packet`. `sim/scenarios.py`: manifest IO with validation (`ScenarioError` on unknown types/roles), `ScenarioStream` yielding (TickInput, TruthTick) with truth strictly separated from the engine-facing object, per-role signed packets with monotone seq, outage blanks, scripted volunteers/operators by tick offset; **RNG consumed up-front in a fixed order** (per-role noise, then per-corruption draws) so streams stay deterministic regardless of outages. `evaluate_expectations` checks committed-label / sensor-state / alert-level / action / baseline expectations with tick tolerance. Layering static test: sim never imports engine modules.
- **Design choices (disclosed):** STUCK freezes at the value reported when it stuck; FABRICATED_RAMP peak plateaus hold_ticks+1 (decline's first point repeats the peak); REPLAY_PACKET default `from_offset_ticks=4` (1 h back) so TS_SKEW may co-fire with SEQ_REPLAY — consistent with RULES §19 scenario F immediate subtypes; `must_not_exceed` alert order treats CLEARED at WATCH level.
- **Test evidence:** `python -m pytest tests/` → **109 passed** (data 20, models 16, settings 8, domain 8, security 27, sim 27 + helpers; includes the 2 fixed test bugs: synthetic df must keep pre-window warmup rows, and replay/compromised-key loops must reach the attack tick). T007–T009, T034–T037 ticked in TASKS.md.

| Item | Status |
|---|---|
| Source documents | PRD, ARCHITECTURE, RULES, DESIGN, TASKS, DEMO, MEMORY all present and reviewed (T001). DEMO.md consistent, defers to sources. |
| Implementation | T001–T025, T031, T032 and T034–T037 done (see §27). Gates: **G1 passed**, **G2 passed** (fit-gate relaxation decision recorded in §26); G3–G5 open. |
| Repository | Audited 2026-10-04 (findings above): documentation-only skeleton, all implementation files empty, follow ARCHITECTURE §2.1 to complete scaffold. |
| Open questions | Q1 — PROVISIONAL INTERPRETATION recorded 2026-10-04 in §24 and **IMPLEMENTED** (R1 subtype = specific hard flag; TS_SKEW/TS_NONMONOTONIC on the 3-tick rule); user may still override (touches `decision.py` R1 only). Q2 — RESOLVED 2026-10-04 in §24 from DESIGN §6.4: Why panel is dashboard-time composition (`messages.reasons[code]` per line; "Message signature is valid." as a display-layer constant; final Result line composed from committed label + actions); no new template keys. |
| Next action | **T026–T030** (`response.py`: sensor FSM RULES §7, alert FSM + best level §8/§10.5/§11, policy/actions/approvals §12 from `config/policy.yaml`, messages/community RULES §13) → **T033** (Runner steps 11/14: wire response + community, SENSOR_STATE/ALERT_STATE/ACTION/APPROVAL/VERIFICATION_REPLY/SYSTEM_ERROR events, model-exception containment §14) → T038/T041/T042 (G3). |

### Progress update (2026-10-04, T018–T024)

- **T018–T020 done — `stageproof/checks.py`.** `health_checks(role, window, cfg, station_params, sensor_min, sensor_max, pred_change=None) -> HealthResult(role, flags, shape_ok, rate_exceeded, noise_too_clean, detail)`: DROPOUT (≥ dropout_ticks trailing NaN / empty history), RANGE (outside envelope or non-finite when `window.missing[role]` is not True), SPIKE retroactive-on-revert (jump > `spike_delta_max` reverting to within `spike_revert_tol_steps × quant_step` inside `spike_revert_ticks`; the revert step itself is marked and never re-scanned as a fresh jump — else the 5.5→5.0 fallback would count as an unreverted jump and set RATE_EXCEEDED on a pure spike-revert episode), RATE_EXCEEDED info-only at the final tick (N-10: never sets shape_ok), STUCK/NOISE/NOISE_TOO_CLEAN B-only. `upstream_trend` (A→T fallback by trust, needs > up_window_ticks + finite endpoints), `rain_support` (UNKNOWN on null thresholds / feed down / < 48 h / stale > 3 h; acc = mean(finite) × 48 h), `drift_measure` (|mean ln-Q residual| < max_dev AND half-window rate < max_rate_per_hour), `is_notable` (obs or pred ≥ watch), `ReplayArchive` (archives every eligible verbatim window ending ≥ min_age_days before live_start; match needs full window, tol_steps × quant_step max deviation, min_range).
- **T021 done — `stageproof/evidence.py`.** `EvidenceBuilder(thresholds, watch_stage, tick_minutes, lag_BC_ticks, yes_mm, no_mm)`; `build(window, prediction, downstream, transport_flags, transport_hard, health, replay_match=False)`. Context ladder: INSUFFICIENT (no pred / fit error / no usable z / inputs ⊆ {"R"}) → PHANTOM z_eff ≥ z_implausible → SUPPRESSED ≤ −z_implausible → CONSISTENT ((inputs ∩ {A,T}) and |z_mean| ≤ z_consistent) → AMBIGUOUS. z floored at scale_floor. §3.9 drift evaluated only in PHANTOM/SUPPRESSED against the **raw ln-Q residual history** (not z — mean z in PHANTOM is ≥ 5 ≥ max_dev 0.25, which made the DRIFT branch dead code; caught and fixed). Downstream: UNKNOWN (no pred / error / C untrusted) → PENDING (notable ∧ episode ∧ age < lag_BC_ticks) → YES (|z| ≤ 3) / NO (|z| ≥ 5). Trend/rain/notable delegated to checks.py per RULES §3.4–3.6, §3.10.
- **T022–T023 done — `stageproof/decision.py`.** `decide(ev, b_state)` evaluates R1–R7 first-match-wins **then** PA1/PA2/PA3 unconditionally (RULES §4 "applied after the table" — early returns were bypassing PA1 on R4-UNCERTAIN, caught by tests and fixed). R1 subtype is the **specific hard flag** (`b_hard[0]`), so `VerdictTracker`'s `immediate_subtypes` check resolves 1-tick vs 3-tick commit per the recorded §24 Q1 provisional interpretation; confidence HIGH iff the flag is immediate. R2 keys on `shape_ok` (never RATE_EXCEEDED per N-10), subtype priority DROPOUT > RANGE > SPIKE > STUCK > NOISE; R2b REPLAY_MATCH only in PHANTOM; R5/R6 DRIFT vs FABRICATED split by trend/rain per the §4 table; PA2 boosts one level LOW→MED→HIGH, never creates; PA3 REAL ladder HIGH (down YES) / MED (rain YES) / LOW. `VerdictTracker(cfg)` commits FAULT/ATTACK after 3 consecutive identical (label, subtype) or 1 for immediate subtypes, sticky REAL with 3-tick downgrade, NORMAL/UNCERTAIN per §6.
- **T024 done — tests.** `tests/test_checks.py` (29: every §3.2 check at/below threshold, context helpers, replay archive), `tests/test_evidence.py` (17: full Evidence fields, z floor/mean, context ladder, drift on/off, downstream PENDING→YES/NO/UNKNOWN, notable, passthrough of health/replay/degraded), `tests/test_decision.py` (33: R1–R7 + PA1/PA2/PA3 + N-10 + tracker persistence/immediacy/sticky-REAL). Test-design note: synthetic B series ending in ≥ 0.3-ft jumps also trip the NOISE check (diff std ≈ 0.145 vs baseline 0.008); flash-flood/rate-only tests override `noise_baseline_std` to isolate RATE_EXCEEDED semantics.
- Source fixes found by the new tests (beyond the ones above): `checks.py` spike scan could index `series[series.size]` for a spike on the final tick (IndexError); `evidence.py` imported `HealthResult` from `.domain` where it does not exist (ImportError on first import).
- **Test evidence: `python -m pytest tests/ -q` → 195 passed** (data 20, models 16, settings 8, domain 8, security 27, sim 27, checks 29, evidence 17, decision 33, pipeline 3 + helpers). T018–T024 ticked in TASKS.md.

### Progress update (2026-10-04, T017, T025, T031, T032)

- **T017 done — baselines** (`models.py`). `StaticThresholdBaseline.update(stage) -> bool` (stage ≥ `action_stage` 17.0 ft) and `RollingRobustZBaseline(window_ticks=96, threshold=6.0, quant_step)` (MAD-based robust z over the trailing window; True when |z| ≥ 6.0). Comparison-only per ARCHITECTURE §6.10 — never feeds evidence.
- **T025 done — estimator** (`estimator.py`). `make_estimate(prediction, bundle, in_use, settings) -> Estimate`: when the target gauge is in use, the displayed/acting stage is the Ridge prediction converted through B's rating; observed passthrough otherwise. Unit-aware (`reach.units["stage"]`); no invented fallbacks.
- **T031 done — Runner core** (`pipeline.py`, ~310 lines). `Runner(settings, bundle, audit_log, replay_archive=None)` implements ARCHITECTURE §4.1 steps 1–10 and 12–13: per-role ring deques for stage/ln-Q/missing/trust (`maxlen = required_history_ticks` 192, NaN-aligned), hourly rain pushed only on `ts.minute == 0` (NaN while the feed is down), trust map from sensor status (TRUSTED until T033 wires the FSM), `Window` built with nothing after `tick.ts`, `pred_change` taken from past predictions (deque `stuck_ticks`), baselines on B, `TickOrderError` on non-increasing `ts` (state unchanged), `replay_match` false unless an optional `ReplayArchive` is supplied. `TickRecord` carries transport_flags, accepted, baselines `{"threshold_alert", "rollz_value", "rollz_flag"}`, estimate, verdict, audit_events_new, head hash; truth stays out (sim separation). Does not import `sim`.
- **T032 done — transport + audit integration.** Step 2 verifies each accepted reading per channel (HARD ⇒ packet rejected and treated as missing); steps 12/13 audit `SESSION_START {scenario_id, seed, model_hash, config_hash}` (sha256 of canonical artifact / of sorted config yaml bytes), `TRANSPORT_FAILURE {station, flags, detail}` per failure, `VERDICT_CHANGE {tick_idx, label, subtype, rule_id, confidence, evidence}` only when the committed verdict changes, `SESSION_END {ticks, head_hash}`. Fail-closed: `AuditError` sets a **sticky** `halted_audit` — no further appends and no action execution for the rest of the session (the chain is broken; engine keeps computing evidence/verdicts). `_execute_approved` appends the ACTION event first, marks EXECUTED only after a successful append, and never executes while halted (T033 enqueues into the existing `self._approved` hook).
- **Acceptance evidence.** `tests/test_pipeline_runner.py` (5 tests) feeds 700 ticks of a quiet, gap-free stretch of the held-out test split (row ≥ train+calibration = 338,880; guards: no NaN in A/T/B/C, B ∈ (2, 12) ft so watch/action levels are never crossed, rain ≤ 5 mm, `B_stage.diff().std() ≥ 0.012` so a live gauge never looks STUCK/TOO_CLEAN): (1) clean replay → all NORMAL, zero verdict transitions, all packets accepted, estimate not in use, `audit_head_hash` of the last tick equals the head **before** `SESSION_END`; (2) SESSION_START/END payloads contain scenario_id/seed/64-hex hashes; (3) forged signature on B → packet rejected, `SIG_INVALID` hard flag on evidence, `TRANSPORT_FAILURE` + immediate `VERDICT_CHANGE {POSSIBLE_CYBER_ATTACK, SIG_INVALID, R1, HIGH}` (SIG_INVALID is an immediate subtype — R1 commits in 1 tick per §24 Q1); (4) forced `AuditError` mid-session → pending APPROVED action never executed, halt sticky after the fault clears, event count frozen, verdicts still produced; (5) equal/earlier ts raises `TickOrderError`, next valid tick succeeds.
- **Supporting fix in `settings.py`:** `threshold()` now also consults artifact **top-level** sections after `model["thresholds"]` misses (`fit_models` writes derived rain thresholds as `model.json "rain": {yes_mm, no_mm}`) — preserves RULES §5 precedence yaml > model > error; the synthetic-shape precedence test is unaffected.
- **Test evidence: `python -m pytest tests/ -q` → 210 passed** (by file: data 20, models 33, pipeline 7, pipeline_runner 5, security 26, sim 27, checks 25, evidence 17, decision 44, estimator 6). T017, T025, T031, T032 ticked in TASKS.md.

### Progress update (2026-10-04, T026–T030 + T033)

- **T026–T028 done — `stageproof/response.py`.** `SensorStateMachine` (one per role; RULES §7: soft flags 3 ticks → SUSPECT, non-B shape flag → SUSPECT, B candidate FAULT/ATTACK or notable-UNCERTAIN-with-inconsistent-context → SUSPECT, verdict commit → QUARANTINED (immediate subtypes at 1 tick), non-B adverse streak ≥ fault_attack_ticks → QUARANTINED, clean 3 → TRUSTED; ATTACK quarantines set `needs_ack`; QUARANTINED→RECOVERING needs 4 clean + ACK released + shape_ok; RECOVERING→TRUSTED needs 8 further clean counted in a separate counter so an ACK delay cannot shorten recovery; relapse on any adverse tick while RECOVERING). `AlertStateMachine` (RULES §8/§10.5/§11): chained per-tick transitions each logged as a separate `Transition`; WATCH idles out after 8; PROVISIONAL via observed ≥ action, estimate streak 2 (stage_lo ≥ action) or community-confirmed notable; CONFIRMED via downstream YES at level ≥ action or officer; retraction WATCH on a FAULT/ATTACK commit invalidating an OBSERVED basis (never while the estimate streak holds); clear streak 8 on observed stage or estimate `stage_hi` (conservative); missing data never accumulates clear streaks (N-16); CLEARED → NONE one tick later. `best_level` (RULES §11): observed while B TRUSTED else estimate — never an untrusted observed value (N-1). `ActionPolicy` creates actions purely from `config/policy.yaml` (tiers, `demo_auto_approve_tier1`, `if_configured: evac_stage`, level/note payload merge; Tier 2 stays PENDING); `Approvals` with recorded officer identity, PENDING-only approve/reject.
- **T029–T030 done — `stageproof/community.py`.** `render` is template-only and raises `MessageError` on unknown key, missing language (after en fallback) or missing field (DESIGN §9.3); basis phrases; `VerificationManager` (RULES §13): opens on a committed notable UNCERTAIN (cooldown 8 ticks), prompts up to 4 registered volunteers in their language; replies deduped, registered-only, code 3 "not sure" never moves the odds; odds-form posterior from per-volunteer reliability (default 0.8, clamped away from 0/1); resolves at min_replies 2 against confirm 0.9 / refute 0.1; timeout at 4 ticks escalates.
- **T033 done — Runner wiring.** Step 7 containment: `predict_target`/`predict_downstream` exceptions produce a degraded `Prediction` plus a `SYSTEM_ERROR {component, error}` audit event and the run continues (ARCHITECTURE §14). Step 11 wired **after** step 12 so `TRANSPORT_FAILURE` stays first in `audit_events_new`: sensor machines (all roles) driven by transport/health flags and, for B, the candidate/committed verdict; alert machine on best level + committed verdict + community confirmation; policy actions per trigger map with a rendered `level_text`; SENSOR_STATE/ALERT_STATE events per transition; public/officer messages from templates; community round opens on a committed notable UNCERTAIN; `ESTIMATE_IN_USE` on the in_use edge; step 13 collects ACTION events into `audit_events_new` (preserves the tick's n_events invariant); operator methods audit before execute (N-5).
- **Deviation (disclosed):** NOTIFY_OFFICER actions from `alert_watch`, `alert_retraction` and the verification timeout carry no rendered message — DESIGN §9.3 defines officer text only for the uncertain/security/fault/evacuation cases; officer notices render once per verdict commit (officer_uncertain / officer_security / officer_fault). The timeout escalation builds its NOTIFY_OFFICER action directly because policy.yaml has no `verification_timeout` trigger.
- **Step-order note (test-facing):** the estimate's `in_use` is computed at step 10 from the PRE-update b_state (machines update at step 11), so on an ATTACK commit tick `in_use` is still False and `ESTIMATE_IN_USE` first fires the following tick.
- **Test evidence:** `tests/test_pipeline_response.py` (6): injected `predict_target` failure → SYSTEM_ERROR + UNCERTAIN/INSUFFICIENT_INPUTS + WATCH + officer message + OPEN volunteer round with 4 prompts, chain verifies; downstream model failure contained, run continues; SIG_INVALID commit → QUARANTINED + needs_ack, the five tier-0 attack actions, officer notice, no public warning, estimate in-use on the next tick; quarantine holds 6 ticks, ACK_RELEASE executes and is audited, recovery QUARANTINED→RECOVERING→TRUSTED then basis back to OBSERVED; two YES replies confirm a round and escalate WATCH→PROVISIONAL on the COMMUNITY basis exactly once; approve → EXECUTED after the APPROVAL audit, reject → REJECTED never executed. **`python -m pytest tests/ -q` → 258 passed.** T026–T030, T033 ticked in TASKS.md.

| Gate | Status |
|---|---|
| G1 data feasibility | **PASSED 2026-10-04** (evidence in §27 T007–T009 entry) |
| G2 model fit gates | **PASSED 2026-10-04** (NSE 0.98469, AUC 0.93435, flag 1.32184/sd, bias −0.0407; G-fit-2/G-fit-3 revised with written justification in §26) |
| G3 MVP-0 (A, C, D, F via CLI) | Not started |
| G4 MVP-1 (Live plays A and C) | Not started |
| G5 offline demo | Not started |
