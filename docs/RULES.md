# StageProof — RULES.md

**Authority:** This file is the single source of truth for **how StageProof must behave**: verdict definitions, evidence semantics, decision order, threshold defaults, state machines, response rules, security/audit rules, failure behavior, and invariants.
**Defers to:** PRD.md (scope/priorities), ARCHITECTURE.md (where each rule is implemented, object fields, file formats), DESIGN.md (how behavior is displayed and message wording), TASKS.md (order of work).
**Conflict rule:** If any other document describes behavior differently from this file, this file wins and the other document must be corrected.

Keywords: **MUST**, **MUST NOT**, **SHOULD** are used in their RFC 2119 sense.

---

## 1. Core principles

| # | Principle |
|---|---|
| P1 | **Verify before alarm.** A reading is judged on two independent questions: *shape plausibility* (does the signal look like a physically possible sensor output?) and *context plausibility* (does the rest of the river system agree with it?). |
| P2 | **Evidence and judgment are separate.** Evidence is computed first (facts). The decision engine only reads evidence. Same evidence + same config → same verdict (deterministic). |
| P3 | **Alerts follow the best estimate of reality, not a single sensor.** Warnings are driven by the best available level (observed if trusted, estimated otherwise). |
| P4 | **Never certain about intent.** Cyber verdicts are always "POSSIBLE". Uncertainty is a first-class outcome, never hidden. |
| P5 | **Asymmetric caution.** Corroborated real floods are warned immediately. Fault/attack conclusions require persistence. When a notable level cannot be verified, prefer WATCH + verification over silence, and prefer informing officials over informing the public. |
| P6 | **Humans decide high-impact actions.** Evacuation recommendations and release of attack quarantines are human-only. |
| P7 | **Everything consequential is audited** in a tamper-evident log before it is executed. |
| P8 | **No future data, no ground truth.** The engine MUST NOT use data later than the current tick (with the explicit rain-availability rule in §3) or any scenario label. |
| P9 | **Explainable by construction.** Every verdict carries machine-readable reason codes that map to plain-language text. No black-box classifier and no LLM in the decision path. |

---

## 2. Verdict definitions

Verdicts are evaluated for the **target sensor B** (middle gauge). Sensors A (upstream), T (tributary), C (downstream) receive transport and basic health checks only (§3.2) and are never given a context verdict in the MVP.

| Label (enum) | Display | Definition | Subtypes |
|---|---|---|---|
| `REAL_FLOOD` | REAL FLOOD | B's reading is shape-plausible, consistent with the physics-based expectation derived from independent upstream/tributary inputs, not contradicted by rainfall/downstream evidence, and the water level is notable (≥ `watch_stage`). | – |
| `SENSOR_FAULT` | SENSOR FAULT | B's output shows a **non-physical signature** (dropout, stuck, spike-and-revert, noise burst, out-of-range) **or** a small, slow, sustained deviation consistent with calibration drift. | `DROPOUT`, `STUCK`, `SPIKE`, `NOISE`, `RANGE`, `DRIFT` |
| `POSSIBLE_CYBER_ATTACK` | POSSIBLE CYBER ATTACK | Either (a) the message failed a transport-integrity check, or (b) B's output is shape-plausible but **contradicted by independent context** in a way a fault would not explain (phantom flood, suppressed flood, exact replay, downstream mismatch). | `TRANSPORT`, `FABRICATED`, `SUPPRESSION`, `REPLAY`, `DOWNSTREAM_MISMATCH` |
| `UNCERTAIN` | UNCERTAIN | Evidence is missing, stale, or conflicting such that neither REAL, FAULT nor ATTACK can be concluded, and the level is notable (or the sensor is already SUSPECT). | reason codes (Appendix A) |
| `NORMAL` | NORMAL | No notable level and no persistent anomaly. Not an alert state. | – |

**Core heuristic (informative):** non-physical shape → fault; believable shape but contradicted context → possible attack; believable shape and supporting context → real.

---

## 3. Evidence rules

All evidence is computed **every tick for B, regardless of B's sensor state** (TRUSTED, SUSPECT, QUARANTINED, RECOVERING). Evidence from a sensor that is not TRUSTED MUST NOT be used as an *input* to other sensors' predictions or context (N-13).

### 3.1 Transport integrity (E1)
Computed by `security/transport.py` for every received packet (all channels).

| Flag | Class | Condition |
|---|---|---|
| `SIG_INVALID` | HARD | HMAC does not verify |
| `STATION_MISMATCH` | HARD | `reading.station_id` ≠ channel it arrived on |
| `UNKNOWN_STATION` | HARD | No key registered for `station_id` |
| `SEQ_REPLAY` | HARD | `seq` ≤ last accepted `seq` for the station |
| `TS_SKEW` | HARD | `|reading.ts − tick.ts|` > `transport.ts_skew_max_seconds` |
| `TS_NONMONOTONIC` | HARD | `reading.ts` ≤ last accepted `ts` for the station |
| `SEQ_GAP` | SOFT | `seq` > last + 1 (packets lost); gap size recorded |
| `MISSING` | SOFT | No packet on the channel this tick |

A packet with any HARD flag is **rejected**: its stage value MUST NOT be used as an observation (treated as missing for modeling) but the failure is recorded as evidence.

### 3.2 Sensor health / shape plausibility (E2)
Computed by `checks.py` on the received (accepted) stage series.

| Check | Applies to | Definition (parameters in §5) | Class |
|---|---|---|---|
| `DROPOUT` | A,T,B,C | No accepted value for ≥ `health.dropout_ticks` consecutive ticks | non-physical |
| `RANGE` | A,T,B,C | Stage outside `[sensor_min, sensor_max]` from reach config, or non-finite | non-physical |
| `SPIKE` | A,T,B,C | One-tick change > `spike_delta_max[station]` **and** value returns within `spike_revert_tol_steps` quantization steps of the pre-spike level within `spike_revert_ticks` ticks (detected retroactively on revert) | non-physical |
| `STUCK` | **B only (MVP)** | Range of B stage over last `stuck_ticks` ≤ 1 quantization step **and** the model prediction (log Q) changed by ≥ `stuck_pred_change_min` over the same window | non-physical |
| `NOISE` | **B only (MVP)** | Std of first differences over `noise_window_ticks` ÷ station baseline std > `noise_ratio_max` | non-physical |
| `RATE_EXCEEDED` | B | One-tick change > `spike_delta_max` **without** revert. **Informational only — alone it never indicates a fault or attack (N-10).** A flash flood can exceed historical rates. | info |
| `NOISE_TOO_CLEAN` | B (P1) | Std of second differences over `clean_window_ticks` < `clean_ratio_min` × baseline **and** stage moved ≥ `clean_min_move_steps` steps. Attack booster only. | info |

`shape_ok` = none of {`DROPOUT`, `RANGE`, `SPIKE`, `STUCK`, `NOISE`} is active for B.

### 3.3 ML residual / context (E3)
Computed by `models.py` (prediction) and `evidence.py` (classification).

- Observed log-discharge: `logq_obs = ln(Q_from_stage(B_stage))` using B's rating curve.
- Expected log-discharge `logq_pred` from the **best available variant** (§10) using only TRUSTED upstream/tributary inputs and rainfall.
- `z = (logq_obs − logq_pred) / s`, where `s` is the calibrated robust scale (two regimes: normal / high-flow, stored in `model.json`, floored by `context.scale_floor`).
- `z_mean` = mean of `z` over the last `context.z_window_ticks` ticks (accepted observations only).
- **Context class:**

| Context | Condition |
|---|---|
| `CONSISTENT` | variant contains ≥ 1 upstream input (A or T) **and** `|z_mean| ≤ z_consistent` |
| `PHANTOM` | variant contains ≥ 1 upstream input **and** `z_mean ≥ z_implausible` (reading higher than physics expects) |
| `SUPPRESSED` | variant contains ≥ 1 upstream input **and** `z_mean ≤ −z_implausible` (reading lower than physics expects) |
| `AMBIGUOUS` | variant contains ≥ 1 upstream input and `z_consistent < |z_mean| < z_implausible` |
| `INSUFFICIENT` | no prediction possible, or variant is rain-only (`R`), or no accepted observation |

### 3.4 Upstream consistency / trend (E4)
`up_trend` computed from A (and T if A unavailable) **only if that sensor is TRUSTED**:
Let Δ = change in ln Q over the last `trend.up_window_ticks` ticks. Apply the checks in this order:
- `RISING`: Δ ≥ `trend.up_rising_min`
- `FALLING`: Δ ≤ −`trend.up_flat_max`
- `FLAT`: otherwise (including small positive changes below `up_rising_min`)
- `UNKNOWN`: neither A nor T is TRUSTED/available.

### 3.5 Downstream consistency (E5)
Computed from the **downstream model** (predicts log Q at C from B's *observed* lagged readings and rain):
- `z_down = (logq_C_obs − logq_C_pred)/s_C`; `z_down_mean` over last `context.z_window_ticks` ticks.
- `downstream` ∈ {`YES` (|z_down_mean| ≤ z_consistent), `NO` (|z_down_mean| ≥ z_implausible), `PENDING`, `UNKNOWN`}.
- `PENDING` while fewer than `lag_BC_ticks` (from `model.json`) have elapsed since B first became notable in the current episode.
- `UNKNOWN` if C is not TRUSTED/available or the residual is in the ambiguous band.
- `NO` covers both "C failed to rise as B claims" (phantom at B) and "C rose more than B implies" (suppression at B).
- Downstream evidence is **delayed by physics**; it MUST NOT be required for a first verdict, only used to upgrade/downgrade (§4).

### 3.6 Rainfall support (E6)
Basin-mean precipitation accumulation over `rain.window_hours` (48 h), from **completed hourly values only**.
- `YES`: accumulation ≥ `rain.yes_mm`
- `NO`: accumulation < `rain.no_mm`
- `UNKNOWN`: between thresholds, or rain feed stale (no new hourly value for > `rain.stale_hours`), or missing.
- **Rain availability rule (no-lookahead):** the hourly value stamped `H` represents the hour ending at `H` and becomes available at the tick with `ts ≥ H`.

### 3.7 Temporal consistency (E7)
Temporal consistency is evidenced by: (a) `RATE_EXCEEDED`/`SPIKE`/`STUCK`/`NOISE` (shape, §3.2); (b) agreement between B's trend and A/T trend (embedded in the residual); (c) **persistence** of a candidate across ticks (§6). There is no separate classifier.

### 3.8 Replay evidence (E8, P1)
`REPLAY_MATCH` is true if the last `replay.window_ticks` accepted B stage values match, within `replay.tol_steps` quantization steps at every tick, some window of B's own archive that (a) is at least `replay.min_age_days` away in time and (b) has stage range ≥ `replay.min_range_steps` steps. This detects **verbatim** replays only (documented limitation).

### 3.9 Drift condition
`drift` is true if context is `PHANTOM` or `SUPPRESSED`, **and** |mean residual over the persistence window| < `drift.max_dev`, **and** the residual's rate of change < `drift.max_rate_per_hour`.

### 3.10 Notable level
`notable` = `obs_stage ≥ levels.B.watch_stage` **or** `pred_stage ≥ levels.B.watch_stage` (using the stage equivalent of `logq_pred`).

---

## 4. Decision procedure

Evaluated **every tick for B**, first match wins. Output is the **candidate** verdict (committed verdict follows §6).

| Rule | Condition | Candidate | Confidence |
|---|---|---|---|
| **R1** | any HARD transport flag on B's channel this tick | `POSSIBLE_CYBER_ATTACK` / `TRANSPORT` | HIGH for `SIG_INVALID`, `SEQ_REPLAY`, `STATION_MISMATCH`, `UNKNOWN_STATION`; MED for `TS_SKEW`, `TS_NONMONOTONIC` |
| **R2** | `shape_ok == false` | `SENSOR_FAULT` / subtype of the active check (priority: `RANGE`, `SPIKE`, `STUCK`, `DROPOUT`, `NOISE`) | HIGH |
| **R2b** | `REPLAY_MATCH` and `context != CONSISTENT` | `POSSIBLE_CYBER_ATTACK` / `REPLAY` | HIGH |
| **R3** | `context == INSUFFICIENT` | if `notable`: `UNCERTAIN` (`INSUFFICIENT_INPUTS`); else `NORMAL` (degraded flag set) | LOW |
| **R4** | `context == CONSISTENT` | if not `notable`: `NORMAL`. If `notable`: if `rain == NO` and `downstream != YES` → `UNCERTAIN` (`NO_RAIN_SUPPORT`); else `REAL_FLOOD` | see below |
| **R5** | `context == PHANTOM` | if `drift`: `SENSOR_FAULT`/`DRIFT`; elif `up_trend ∈ {FLAT, FALLING}` and `rain != YES`: `POSSIBLE_CYBER_ATTACK`/`FABRICATED`; else `UNCERTAIN` (`CONFLICTING_EVIDENCE`) | MED (HIGH with booster) |
| **R6** | `context == SUPPRESSED` | if `drift`: `SENSOR_FAULT`/`DRIFT`; elif `up_trend == RISING` or `rain == YES`: `POSSIBLE_CYBER_ATTACK`/`SUPPRESSION`; else `UNCERTAIN` (`UNSUPPORTED_DEVIATION`) | MED (HIGH with booster) |
| **R7** | `context == AMBIGUOUS` | if `notable` or B state is `SUSPECT`: `UNCERTAIN` (`AMBIGUOUS_RESIDUAL`); else `NORMAL` | LOW |

**Post-adjustments (applied after the table):**
- **PA1 (downstream mismatch):** if candidate ∈ {`REAL_FLOOD`, `UNCERTAIN`}, B is `notable`, and `downstream == NO`, then candidate = `POSSIBLE_CYBER_ATTACK`/`DOWNSTREAM_MISMATCH`, MED. (Catches coordinated A+B manipulation after the lag.)
- **PA2 (boosters):** `REPLAY_MATCH` or `NOISE_TOO_CLEAN` raises a `POSSIBLE_CYBER_ATTACK` candidate's confidence by one level (LOW→MED→HIGH). Boosters never create an attack candidate by themselves, except R2b.
- **PA3 (REAL confidence):** `REAL_FLOOD` confidence = LOW if only upstream-based context; MED if additionally `rain == YES`; HIGH if `downstream == YES`.

**Rate-of-rise rule (N-10):** a sudden rise (`RATE_EXCEEDED`) is never sufficient for R2, R5 or R6. Only context decides.

Reason codes accompany every candidate (Appendix A).

---

## 5. Threshold rules

All numeric thresholds live in **`config/thresholds.yaml`** and (for station-specific/derived values) **`artifacts/model.json`**. Code MUST read them via `settings.py`/`models.py`; hard-coding numbers in logic modules is forbidden (N-15). Station levels live in **`config/reach.yaml`**.

**Precedence:** explicit non-null value in `thresholds.yaml` > value derived by `scripts/fit_models.py` in `model.json` > startup error.

```yaml
# config/thresholds.yaml — authoritative defaults (RULES.md §5)
transport:
  ts_skew_max_seconds: 300
  soft_flag_suspect_ticks: 2        # consecutive soft flags that move a sensor to SUSPECT
context:
  z_consistent: 3.0
  z_implausible: 5.0
  z_window_ticks: 3
  scale_floor: 0.02                 # ln Q units
  band_z: 3.0                       # estimate interval half-width in scale units
trend:
  up_window_ticks: 8
  up_rising_min: 0.10               # ln Q rise over window
  up_flat_max: 0.03
rain:
  window_hours: 48
  yes_mm: null                      # derived by fit_models.py if null
  no_fraction: 0.25                 # no_mm = no_fraction * yes_mm
  no_mm: null
  stale_hours: 3
health:
  dropout_ticks: 2
  stuck_ticks: 8
  stuck_pred_change_min: 0.05
  spike_revert_ticks: 3
  spike_revert_tol_steps: 3
  noise_window_ticks: 8
  noise_ratio_max: 5.0
  clean_window_ticks: 16
  clean_ratio_min: 0.2
  clean_min_move_steps: 3
drift:
  max_dev: 0.25                     # |mean residual| in ln Q
  max_rate_per_hour: 0.02
replay:
  window_ticks: 24
  tol_steps: 2
  min_range_steps: 5
  min_age_days: 7
persistence:
  fault_attack_ticks: 3
  real_downgrade_ticks: 3
  immediate_subtypes: [SIG_INVALID, SEQ_REPLAY, STATION_MISMATCH, UNKNOWN_STATION, RANGE]
sensor:
  clean_ticks_to_recover: 4         # QUARANTINED -> RECOVERING
  recovery_ticks: 8                 # RECOVERING -> TRUSTED
alert:
  watch_idle_ticks: 8
  clear_ticks: 8
  est_warn_ticks: 2
verification:
  prior_real: 0.5
  default_reliability: 0.8
  confirm_posterior: 0.9
  refute_posterior: 0.1
  min_replies: 2
  timeout_ticks: 4
  cooldown_ticks: 8
  max_volunteers: 4
```

Station levels (`config/reach.yaml`, per target sensor B): `watch_stage`, `action_stage`, `clear_stage` (< `action_stage`), `evac_stage` (nullable; null disables evacuation recommendations). Units are recorded in `reach.yaml`.

**Calibration obligation:** after fitting, `z_implausible` MUST be checked so that false SUSPECT/QUARANTINE on clean calibration data is ≤ 1 per 100 sensor-days; if not, increase `z_implausible` in `thresholds.yaml` (tuning is done on tuning-family scenarios only — see ARCHITECTURE §9).

---

## 6. Persistence / hysteresis

The decision engine produces a **candidate** every tick. A `VerdictTracker` converts candidates into **committed** verdicts:

| Candidate class | Commit rule |
|---|---|
| `REAL_FLOOD` | Commits **immediately** (1 tick) — corroborated real floods MUST NOT be delayed. Committed REAL is downgraded only after `persistence.real_downgrade_ticks` consecutive non-REAL candidates. |
| `SENSOR_FAULT`, `POSSIBLE_CYBER_ATTACK` | Commit after the **same (label, subtype)** candidate appears `persistence.fault_attack_ticks` consecutive ticks. Counter resets if the candidate changes. **Exception:** subtypes in `persistence.immediate_subtypes` commit at 1 tick. `STUCK`, `DROPOUT`, `SPIKE` are already persistence-defined by their own windows and commit at 1 tick after detection. |
| `UNCERTAIN` | Commits immediately (it triggers only low-impact actions). |
| `NORMAL` | Commits when the previous committed label is NORMAL, or after `persistence.real_downgrade_ticks` consecutive NORMAL candidates when leaving another label. |

The `Verdict` record always exposes both `candidate` and committed `label`, plus `persist_count`/`persist_needed`, so the UI can show "candidate: ATTACK (2/3)".

`z_mean` is already a smoothed statistic; persistence is counted in ticks on the candidate and MUST NOT be interpreted as independent confirmations.

---

## 7. Sensor state machine

One state machine per sensor role (A, T, B, C). For A, T, C it is driven only by transport and basic health evidence (§3.1–3.2); for B it is also driven by the verdict.

States: `TRUSTED`, `SUSPECT`, `QUARANTINED`, `RECOVERING`.

| From | To | Trigger |
|---|---|---|
| TRUSTED | SUSPECT | First tick of a FAULT/ATTACK candidate (B); UNCERTAIN candidate with `notable` and `context != CONSISTENT` (B); `transport.soft_flag_suspect_ticks` consecutive soft flags (any sensor); first non-physical health flag candidate (A,T,C) |
| SUSPECT | TRUSTED | `persistence.real_downgrade_ticks` consecutive candidates ∈ {NORMAL, REAL_FLOOD} with no flags |
| SUSPECT | QUARANTINED | Verdict commit of FAULT or ATTACK (B); for A,T,C: non-physical health flag or HARD transport flag persisting per §6 |
| TRUSTED | QUARANTINED | Immediate-subtype commit (§6) |
| QUARANTINED | RECOVERING | Underlying condition absent for `sensor.clean_ticks_to_recover` consecutive ticks (candidate ∈ {NORMAL, REAL_FLOOD}, no HARD flags, `shape_ok`). **If quarantined for ATTACK, additionally requires an officer `ACK_RELEASE` (Tier 2).** |
| RECOVERING | TRUSTED | `sensor.recovery_ticks` further consecutive clean ticks |
| RECOVERING | QUARANTINED | Any FAULT/ATTACK candidate for 1 tick (relapse) |
| any | (no change) | UNCERTAIN alone never quarantines |

While `SUSPECT`, `QUARANTINED`, or `RECOVERING`, the sensor's readings MUST NOT be used as model inputs for other sensors or as the basis of a warning (N-1, N-13). Evidence keeps being computed for monitoring.

---

## 8. Alert state machine

One alert state for the reach (centered on target B). States: `NONE`, `WATCH`, `PROVISIONAL_WARNING`, `CONFIRMED_WARNING`, `CLEARED`. Several transitions may chain within one tick (each is logged separately).

`best_level` and `best_source` are defined in §11.

| From | To | Trigger | Source tag |
|---|---|---|---|
| NONE | WATCH | (a) committed `UNCERTAIN` ∧ `notable`; or (b) committed `REAL_FLOOD` ∧ `best_level ≥ watch_stage`; or (c) estimate in use ∧ `stage_hi ≥ action_stage` ∧ `stage_lo < action_stage` | OBSERVED / ESTIMATE |
| WATCH | NONE | No trigger for `alert.watch_idle_ticks` | – |
| WATCH | PROVISIONAL_WARNING | (a) committed `REAL_FLOOD` ∧ observed `stage ≥ action_stage`; or (b) estimate rule (§10.5) satisfied; or (c) community-confirmed (§13) ∧ `notable` | OBSERVED / ESTIMATE / COMMUNITY |
| PROVISIONAL_WARNING | CONFIRMED_WARNING | `downstream == YES` (C TRUSTED) ∧ `best_level ≥ action_stage`; or officer confirm | DOWNSTREAM / OFFICER |
| PROVISIONAL_WARNING | WATCH | **Retraction:** the basis (`best_source == OBSERVED`) is invalidated by a committed FAULT/ATTACK on B and the estimate rule is not satisfied → emits `SEND_CORRECTION` | – |
| PROVISIONAL_WARNING / CONFIRMED_WARNING | CLEARED | `clear_level < clear_stage` for `alert.clear_ticks` consecutive ticks, or officer clear | – |
| CLEARED | NONE | Next tick (emits `SEND_ALL_CLEAR` on entering CLEARED) | – |

`clear_level` = observed stage if `best_source == OBSERVED`; if `best_source == ESTIMATE`, use `stage_hi` (conservative).
A warning MUST NOT clear merely because a sensor was quarantined.

---

## 9. Quarantine rules

1. Quarantine is **automatic** for FAULT and ATTACK commits (Tier 0).
2. Quarantine means: the sensor's readings are excluded from decisions and from other sensors' model inputs; evidence still computed; estimate substituted (§10).
3. Entering QUARANTINED triggers actions (policy §12): FAULT → `QUARANTINE_SENSOR`, `OPEN_MAINTENANCE_TICKET`, `USE_ESTIMATE`; ATTACK → `QUARANTINE_SENSOR`, `RAISE_SECURITY_ALERT`, `PRESERVE_EVIDENCE`, `NOTIFY_OFFICER`, `USE_ESTIMATE`.
4. `PRESERVE_EVIDENCE` stores the full `Evidence`, last 48 ticks of raw readings, and transport flags for the sensor in the audit payload.
5. Release: FAULT → automatic via RECOVERING (§7). ATTACK → requires officer `ACK_RELEASE` plus the clean-tick condition. Volunteers and community replies can never release a quarantine (N-12).
6. A quarantined sensor MUST NOT be silently dropped from the UI; it is shown with its state and reason.

---

## 10. Fallback (substitute estimate) rules

1. **Estimate generation.** The estimate is derived from the same transfer model as the prediction, using **only** TRUSTED upstream (A), tributary (T) and rain inputs — never B's own readings. Output: `q_hat`, converted to `stage_hat`, with interval `[stage_lo, stage_hi]` = rating-inverse of `logq_pred ± context.band_z × s` (s = variant scale).
2. **Variant selection.** Variants are tried in the order stored in `model.json` (ascending calibration scale). The first variant whose every input is TRUSTED, present and has full lag history is used. Variants: `A+T+R`, `A+T`, `A+R`, `T+R`, `A`, `T`, `R`.
3. **Confidence.** `HIGH`: variant contains both A and T. `MED`: contains exactly one of A/T. `LOW`: rain-only (`R`). `NONE`: no model could run.
4. **Estimate always carries provenance**: `variant`, `basis` (input roles), `confidence`, `in_use`. In every UI and message, an estimate MUST be labeled "ESTIMATED" (N-7).
5. **Warning from estimates.** While `in_use`, a `PROVISIONAL_WARNING` (source ESTIMATE) is issued only if `confidence ∈ {HIGH, MED}` ∧ `stage_lo ≥ action_stage` for `alert.est_warn_ticks` consecutive ticks. If `stage_hi ≥ action_stage > stage_lo` → WATCH only. If `confidence == LOW` → WATCH only, plus officer notification; no automatic public warning.
6. **When the estimate is `in_use`:** B is SUSPECT with candidate ∈ {FAULT, ATTACK, UNCERTAIN-with-`context != CONSISTENT`}, or B is QUARANTINED/RECOVERING.
7. **If upstream inputs are unavailable** (both A and T not TRUSTED): rain-only variant; confidence LOW; WATCH + `REQUEST_VERIFICATION` + officer notification; no automatic public warning.
8. **If no variant can run** (rain also unavailable/stale): estimate `NONE`; verdict `UNCERTAIN` (`INSUFFICIENT_INPUTS`) if B's reading is notable; officer notified; the last known alert state is held (never auto-cleared, N-16).
9. **Return to TRUSTED:** per §7. During RECOVERING, B readings are monitored but not used for decisions or warnings.

---

## 11. Warning rules

- `best_level` = `obs_stage` when B is TRUSTED; the `Estimate` interval when B is SUSPECT (with estimate in use), QUARANTINED or RECOVERING.
- Public warnings are issued via `SEND_PUBLIC_WARNING` (Tier 1) with payload `level` ∈ {`PROVISIONAL`, `CONFIRMED`}, `basis` ∈ {`OBSERVED`, `ESTIMATE`, `COMMUNITY`, `DOWNSTREAM`}.
- **No public warning may be based on data from a sensor whose context is PHANTOM, whose transport failed (HARD flag), or which is SUSPECT-with-attack/fault-candidate or QUARANTINED** (N-1). Observed data from such sensors can only support WATCH to officials.
- Warning text states its basis in plain language (DESIGN.md §8).
- Evacuation: the system MAY create a `RECOMMEND_EVACUATION` (Tier 2) addressed to the officer when `CONFIRMED_WARNING` ∧ `best_level ≥ evac_stage` (only if `evac_stage` is configured). It MUST NOT send any evacuation order.
- `SEND_CORRECTION` is issued on retraction (§8); `SEND_ALL_CLEAR` on entering CLEARED.

---

## 12. Human approval rules

| Tier | Meaning | Actions |
|---|---|---|
| 0 | Automatic | `QUARANTINE_SENSOR`, `USE_ESTIMATE`, `OPEN_MAINTENANCE_TICKET`, `RAISE_SECURITY_ALERT`, `PRESERVE_EVIDENCE`, `NOTIFY_OFFICER`, `REQUEST_VERIFICATION` |
| 1 | One-tap officer approval; may be auto-approved only when `policy.demo_auto_approve_tier1: true` (displayed as AUTO-APPROVED (demo)) | `SEND_PUBLIC_WARNING`, `SEND_CORRECTION`, `SEND_ALL_CLEAR` |
| 2 | Human only; never automatic, never auto-approved | `RECOMMEND_EVACUATION`, `ACK_RELEASE` |

Action statuses: `PENDING`, `AUTO_APPROVED`, `APPROVED`, `REJECTED`, `EXECUTED`, `SKIPPED`. "Executed" is simulated (no external system is contacted). An action becomes `EXECUTED` only **after** its audit event is appended (N-5). Officer identity is recorded for every approval.

**Policy mapping (`config/policy.yaml` MUST encode exactly this):**

| Trigger (committed event) | Actions |
|---|---|
| Verdict commit `SENSOR_FAULT` | `QUARANTINE_SENSOR`, `OPEN_MAINTENANCE_TICKET`, `USE_ESTIMATE` |
| Verdict commit `POSSIBLE_CYBER_ATTACK` | `QUARANTINE_SENSOR`, `RAISE_SECURITY_ALERT`, `PRESERVE_EVIDENCE`, `NOTIFY_OFFICER`, `USE_ESTIMATE` |
| Verdict commit `UNCERTAIN` ∧ `notable` | `NOTIFY_OFFICER`, `REQUEST_VERIFICATION` (subject to cooldown) |
| Alert → WATCH | `NOTIFY_OFFICER` |
| Alert → PROVISIONAL_WARNING | `SEND_PUBLIC_WARNING` (level PROVISIONAL), `NOTIFY_OFFICER` |
| Alert → CONFIRMED_WARNING | `SEND_PUBLIC_WARNING` (level CONFIRMED), `RECOMMEND_EVACUATION` (if configured) |
| Alert retraction | `SEND_CORRECTION`, `NOTIFY_OFFICER` |
| Alert → CLEARED | `SEND_ALL_CLEAR` |
| Officer `ACK_RELEASE` | releases ATTACK quarantine when clean condition holds |

---

## 13. Community verification rules

1. **Trigger:** committed `UNCERTAIN` ∧ `notable` and no verification round for B within `verification.cooldown_ticks`.
2. **Round:** up to `max_volunteers` registered volunteers assigned to B are prompted (their language; prompt text in DESIGN.md §8). Reply codes: `1` = water above the marked level (flooding), `2` = not above, `3` = not sure.
3. **Posterior (odds form):** start from `prior_real`; each reply from a volunteer with reliability `r` (default `default_reliability`) multiplies odds by `r/(1−r)` for code 1, by `(1−r)/r` for code 2; code 3 is ignored. One reply per volunteer per round (later replies from the same volunteer are ignored). Replies from unregistered senders are ignored.
4. **Resolution:** with ≥ `min_replies` informative replies: posterior ≥ `confirm_posterior` → **community-confirmed**; ≤ `refute_posterior` → **community-refuted**. A single reply never resolves a round.
5. **Timeout:** after `timeout_ticks` without resolution → escalate: `NOTIFY_OFFICER` (priority) and remain in WATCH.
6. **Effects:** community-confirmed ∧ `notable` allows WATCH → PROVISIONAL_WARNING (source COMMUNITY; Tier 1). Community-refuted keeps the sensor SUSPECT/QUARANTINED and creates no warning. Community replies **never** override a HARD transport failure, never change a committed ATTACK/FAULT verdict, and never release a quarantine (N-12).
7. Verification messages and replies are audited. Replies are treated as untrusted input (a spoofed reply must not be sufficient to cause a public warning on its own — hence `min_replies`).

---

## 14. Security rules

1. Every sensor message carries `(station_id, ts, seq, stage, unit, sig)`; `sig` = hex HMAC-SHA256 over the canonical string `"{station_id}|{ts_utc_iso}|{seq}|{stage:.3f}|{unit}"` using the station's key. Verification MUST use constant-time comparison (`hmac.compare_digest`).
2. Keys come from environment variables (`STAGEPROOF_KEY_<STATION>`), never from source control. `.env.example` contains **demo-only** keys, clearly marked.
3. Station binding is enforced (channel ≠ `station_id` → `STATION_MISMATCH`). Sequence and timestamp checks follow §3.1.
4. A valid signature proves origin, **not truth**. The system MUST NOT treat a validly signed reading as trustworthy by that fact alone (N-3). Physical consistency (E3–E6) applies to all readings.
5. HARD transport failures on B yield an immediate ATTACK/`TRANSPORT` commit for `SIG_INVALID`, `SEQ_REPLAY`, `STATION_MISMATCH`, `UNKNOWN_STATION`.
6. The model artifact is stored as JSON (no pickle) and its content hash is logged at session start.
7. No network access at runtime; no secrets in logs; no execution of data-derived strings.
8. Threat model (what is and is not defended) is documented in ARCHITECTURE.md §10.

---

## 15. Audit rules

1. The audit log is an append-only JSONL file; each event contains `idx`, `ts`, `kind`, `payload`, `prev_hash`, `hash`, with `hash = SHA-256(canonical_json({idx, ts, kind, payload, prev_hash}))`. Genesis `prev_hash` is 64 zeros. Canonical JSON: sorted keys, compact separators, floats rounded to 6 decimals.
2. **Events that MUST be appended:** `SESSION_START` (scenario id, seed, model hash, config hash), `TRANSPORT_FAILURE` (each HARD flag), `VERDICT_CHANGE` (committed label/subtype change, with evidence snapshot), `SENSOR_STATE` (each transition), `ALERT_STATE` (each transition), `ACTION` (each creation and status change), `APPROVAL` (officer decisions), `VERIFICATION_REPLY`, `ESTIMATE_IN_USE` (start/stop), `SYSTEM_ERROR`, `SESSION_END`.
3. Every tick is NOT logged; only the events above (plus the periodic evidence is available from `TickRecord`s in memory/cache).
4. `verify()` recomputes the chain and returns `(ok, first_bad_index, reason)`. Any edit, deletion or reordering MUST be detected at the first affected index.
5. Audit events MUST NOT be modified or deleted by application code. Starting a new session creates a new file; old files are never rewritten.
6. If an audit append fails, the Runner MUST halt action execution (§17).

---

## 16. Safety constraints

- **S1:** A false public alarm and a missed warning are both harms. The design resolves ties toward WATCH + verification + officer notification.
- **S2:** The system is a decision-support and verification layer. It does not replace official warning authorities and never contacts real external systems in this project.
- **S3:** All outputs derived from models are labeled with uncertainty (interval/confidence).
- **S4:** A quarantined or suspect sensor never silently disappears and never silently re-enters service.
- **S5:** Demo/simulated mode is visibly labeled in the UI and in message text where applicable.

---

## 17. Failure behavior

| Failure | Required behavior |
|---|---|
| A single B tick missing | Not filled silently. Counts toward `dropout_ticks`. Evidence marks `MISSING`. |
| B dropout (≥ `dropout_ticks`) | `SENSOR_FAULT`/`DROPOUT` → QUARANTINED → estimate in use. |
| A missing or not TRUSTED | Next best variant (T-based or rain-only); `up_trend = UNKNOWN` unless T available; `degraded = true`. |
| A and T both unavailable | Rain-only → `context = INSUFFICIENT` → R3 → `UNCERTAIN` (if notable) → WATCH + verification + officer notification. |
| Rain feed stale/missing | `rain = UNKNOWN`; variants without `R` are used; `degraded = true`. |
| Model exception or NaN | Catch at the Runner; log `SYSTEM_ERROR`; `prediction = None`; `context = INSUFFICIENT`; continue the run. |
| `model.json` missing/corrupt | Refuse to start; message: run `make fit`. |
| Config invalid | Refuse to start with a precise error. |
| Out-of-order or duplicate tick input | Reject the tick with an error; state unchanged. |
| Audit append failure | Enter `HALTED_AUDIT`: no action is executed; dashboard shows a blocking banner; evidence continues to be computed. |
| Dashboard component exception | Show an error card for that component only; the app and Runner continue. |
| Optional SQLite store (P2) failure | Best-effort write; failure logged; no effect on decisions. |
| Optional API gateway (P2) failure | No effect on the in-process dashboard in the MVP. |
| Quarantined B with no estimate possible | Hold the last alert state; verdict UNCERTAIN; escalate to officer. |

---

## 18. Rules that must NEVER be violated

| ID | Rule |
|---|---|
| N-1 | Never issue a public warning based on B data when B's context is PHANTOM, its transport failed, or it is SUSPECT/QUARANTINED for FAULT/ATTACK. |
| N-2 | Never read scenario ground truth, scenario labels or expected verdicts inside the engine (`pipeline`, `evidence`, `decision`, `estimator`, `response`, `community`, `models`, `checks`). |
| N-3 | Never treat a valid signature as proof that a reading is true. |
| N-4 | Never silently fill, hide or smooth missing/rejected data; always flag it. |
| N-5 | Never execute an action before its audit event is appended; never edit or delete audit events. |
| N-6 | Never automatically send an evacuation order, and never automatically release an ATTACK quarantine or auto-approve Tier 2. |
| N-7 | Never present an estimate as a measurement; always label ESTIMATED with its interval. |
| N-8 | Never use an LLM, remote service or network call in the decision/safety path or at runtime. |
| N-9 | Never present UNCERTAIN as a conclusion, and never call a cyber verdict anything stronger than "POSSIBLE". |
| N-10 | Never reject a reading, or label it fault/attack, on rate-of-rise alone. |
| N-11 | Never commit secrets or real keys; never use `==` to compare signatures. |
| N-12 | Never let community replies override a HARD transport failure, change a committed FAULT/ATTACK, or release a quarantine; never resolve a verification round from a single reply. |
| N-13 | Never use a non-TRUSTED sensor's readings as input to another sensor's prediction, context or estimate. |
| N-14 | Never use data later than the current tick (rain availability rule in §3.6 is the only timing exception, and it is also non-lookahead). |
| N-15 | Never hard-code thresholds in logic modules; read them from config/`model.json`. |
| N-16 | Never auto-clear an existing warning because of lost data or a quarantine. |
| N-17 | Never train or calibrate on any scenario or evaluation window. |
| N-18 | Never make the engine non-deterministic: all randomness uses explicit seeds recorded in the audit `SESSION_START`. |

---

## 19. Scenario behavioral contract

`tests/test_pipeline.py` MUST assert these behaviors (tick tolerances are in the scenario manifests).

| ID | Scenario | Expected behavior |
|---|---|---|
| A | Real flood replay | B committed `REAL_FLOOD` from the first tick with consistent context and `notable`; alert reaches PROVISIONAL_WARNING (source OBSERVED) no later than 2 ticks after observed stage crosses `action_stage`; reaches CONFIRMED_WARNING after `downstream == YES`; **no FAULT/ATTACK commit**; baseline rolling-z flags ≥ 1 tick as "anomaly/discard". |
| B | Sensor fault (B stuck while river changes) | `SENSOR_FAULT`/`STUCK` committed; B QUARANTINED; estimate in use; maintenance ticket created; **no public warning** unless the estimate itself satisfies §10.5. |
| C | Fabricated flood with compromised key (dry period) | All packets pass transport; context PHANTOM; `POSSIBLE_CYBER_ATTACK`/`FABRICATED` committed within `persistence.fault_attack_ticks` ticks of context becoming implausible; B QUARANTINED; security alert + evidence preserved; **alert state never exceeds WATCH; zero public warnings.** Estimate stays near the true level. |
| D | Suppressed real flood (compromised key) | `POSSIBLE_CYBER_ATTACK`/`SUPPRESSION` committed; B QUARANTINED; **PROVISIONAL_WARNING (source ESTIMATE) issued** via §10.5. |
| E | Upstream telemetry outage during real flood | A and T unavailable → rain-only → `UNCERTAIN`/`INSUFFICIENT_INPUTS`; WATCH; verification round; scripted replies (1,1) → posterior ≥ `confirm_posterior` → PROVISIONAL_WARNING (source COMMUNITY); when A returns, committed `REAL_FLOOD`. |
| F | Unsigned/replayed packets (no key) | `SIG_INVALID` / `SEQ_REPLAY` packets rejected; `POSSIBLE_CYBER_ATTACK`/`TRANSPORT` committed at 1 tick; B QUARANTINED; estimate in use; audit verifies OK; tampering with the saved log makes `verify()` fail at the edited index. |

---

## Appendix A — Reason codes

| Code | Plain-language meaning (English default) |
|---|---|
| `TRANSPORT_SIG_INVALID` | The message signature is invalid. |
| `TRANSPORT_SEQ_REPLAY` | A message sequence number was reused or went backwards. |
| `TRANSPORT_STATION_MISMATCH` | The message claims a different station than the channel it arrived on. |
| `TRANSPORT_UNKNOWN_STATION` | No key is registered for this station. |
| `TRANSPORT_TS_SKEW` | The message timestamp is too far from the expected time. |
| `TRANSPORT_TS_NONMONOTONIC` | The message timestamp is not newer than the last accepted one. |
| `SHAPE_DROPOUT` / `SHAPE_STUCK` / `SHAPE_SPIKE` / `SHAPE_NOISE` / `SHAPE_RANGE` | The sensor output has a non-physical signature (dropout / stuck value / spike that reverts / noise burst / out of range). |
| `CONTEXT_CONSISTENT` | The reading matches what upstream gauges and rainfall predict. |
| `CONTEXT_PHANTOM` | The reading is much higher than upstream gauges and rainfall can explain. |
| `CONTEXT_SUPPRESSED` | The reading is much lower than upstream gauges and rainfall imply. |
| `UPSTREAM_RISING` / `UPSTREAM_FLAT` / `UPSTREAM_FALLING` / `UPSTREAM_UNKNOWN` | State of the upstream trend. |
| `RAIN_YES` / `RAIN_NO` / `RAIN_UNKNOWN` | Whether recent rainfall supports a rise. |
| `DOWNSTREAM_YES` / `DOWNSTREAM_NO` / `DOWNSTREAM_PENDING` / `DOWNSTREAM_UNKNOWN` | Whether the downstream gauge responded as expected. |
| `REPLAY_MATCH` | The recent readings are an exact copy of an earlier period. |
| `NOISE_TOO_CLEAN` | The signal is unnaturally smooth for a real sensor. |
| `DRIFT_PATTERN` | A small, slow, sustained deviation consistent with calibration drift. |
| `INSUFFICIENT_INPUTS` | Not enough trustworthy inputs to verify this reading. |
| `NO_RAIN_SUPPORT` | Upstream agrees, but there is no rainfall to explain a rise and downstream has not confirmed. |
| `AMBIGUOUS_RESIDUAL` | The reading is somewhat unusual but not clearly wrong. |
| `CONFLICTING_EVIDENCE` | Evidence sources disagree. |
| `UNSUPPORTED_DEVIATION` | The reading is lower than expected but nothing explains why. |
| `DEGRADED_INPUTS` | A lower-quality model variant was used because some inputs were unavailable. |
