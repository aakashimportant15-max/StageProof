# StageProof — ARCHITECTURE.md

**Authority:** This document owns **HOW the system is structured**: repository layout, module responsibilities, import rules, domain objects, file/data formats, interfaces, ML pipeline mechanics, scenario machinery and failure mechanisms.
**Defers to:** RULES.md for *behavior* (verdict logic, thresholds, state machines, policy). This document says **where** each rule is implemented, never what the rule is. If a conflict appears, RULES.md wins for behavior and this file must be fixed.
**Companion docs:** PRD.md (scope, FR IDs), DESIGN.md (UI), TASKS.md (order), DEMO.md, MEMORY.md.

---

## 1. Architecture overview

StageProof is a **single-process Python library** with thin front-ends (CLI scripts and a Streamlit dashboard). There is no database, no API server and no runtime network access.

```text
DATA            data/reach.csv + artifacts/model.json + config/*.yaml   (prepared offline)
 ↓
INGESTION       signed SensorReading packets per channel (A, T, B, C) + rain feed     [security/transport.py]
 ↓
NORMALIZATION   UTC tick grid, stage → ln Q via rating curve, buffers, rain hourly history   [pipeline.py, models.py]
 ↓
FEATURE ENG.    Window: lagged ln Q of TRUSTED sensors, rain accumulations            [models.py, pipeline.py]
 ↓
PHYSICS/HEALTH  dropout, range, spike, stuck, noise, trend, rain support, replay      [checks.py]
 ↓
ML              Ridge lagged ln-discharge prediction, residual, z-score              [models.py]
 ↓
EVIDENCE        one typed Evidence record per tick (facts only)                        [evidence.py]
 ↓
DECISION        rules R1–R7 + PA1–PA3 → candidate; persistence → committed Verdict     [decision.py]
 ↓
RESPONSE        sensor/alert state machines, estimate, actions, messages, verification  [estimator.py, response.py, community.py]
 ↓
AUDIT           hash-chained JSONL; actions execute only after append                   [security/audit.py, pipeline.py]
 ↓
DASHBOARD       reads TickRecord history; operator controls call the Runner             [dashboard/*]
```

Simulation is a **separate side**: `sim/` produces signed, corrupted packet streams plus ground truth. It never touches the engine.

```text
 sim/scenarios.py ──(TickInput stream, signed)──►  Runner.step(...)  ──► TickRecord ──► CLI / Dashboard
        │                                              ▲
        └──(TruthTick, kept outside the engine)────────┘ (attached to records by the driver only, for display/evaluation)
```

Key design properties:
- **Causal and deterministic:** one `Runner.step()` per tick; state only moves forward; all randomness is seeded.
- **Pure decision core:** `evidence`, `decision`, `estimator` are pure functions of their inputs plus explicit state objects.
- **One wiring point:** only `pipeline.py` imports across engine modules.
- **Every tick produces a complete `TickRecord`** so any moment can be explained afterwards.

---

## 2. Component responsibilities

### 2.1 Repository layout

Repository root is `stageproof/`; the Python package is `stageproof/stageproof/` (import name `stageproof`).

```text
stageproof/                          # repository root
├── README.md                        # quick start, how to run, limitations
├── pyproject.toml                   # package + dependencies (extras: data, dev)
├── Makefile                         # setup / data / fit / scenarios / test / demo / eval / cache
├── .env.example                     # DEMO-ONLY signing keys (never real keys)
├── .gitignore
├── .streamlit/
│   └── config.toml                  # dark theme, wide layout
│
├── config/
│   ├── reach.yaml                   # stations, roles, units, levels, sensor limits, rain points, splits, events, baselines
│   ├── thresholds.yaml              # all thresholds (defaults authoritative in RULES §5)
│   ├── policy.yaml                  # action tiers + trigger→action map (RULES §12) + demo_auto_approve_tier1
│   └── messages.yaml                # message templates (en, hi), reason texts, volunteer registry
│
├── data/
│   ├── raw/                         # cached downloads (gitignored)
│   ├── reach.csv                    # aligned 15-min dataset used by everything at runtime
│   └── scenarios/                   # scenario manifests A–F (JSON)
│
├── artifacts/                       # generated; mostly gitignored
│   ├── model.json                   # fitted models + rating curves + derived parameters
│   ├── fit_report.json              # held-out metrics + gate results
│   ├── audit/                       # per-session audit logs (JSONL)
│   ├── replay_cache/                # exported TickRecords per scenario (JSON) [P1]
│   └── eval/                        # evaluation outputs (JSON/PNG) [P1]
│
├── scripts/
│   ├── prepare_data.py              # ONLY network-using file: fetch + align → data/reach.csv
│   ├── fit_models.py                # rating curves, lags, Ridge variants, calibration → artifacts/model.json
│   ├── build_scenarios.py           # write manifests A–F from config/events
│   ├── run_scenario.py              # headless run, timeline, --explain, --export, expectation check
│   └── evaluate.py                  # batch episodes, metrics, figures [P1]
│
├── stageproof/                      # Python package
│   ├── __init__.py
│   ├── domain.py                    # enums + dataclasses (single vocabulary)
│   ├── settings.py                  # YAML/.env → typed Settings
│   ├── data.py                      # dataset + model artifact loading
│   ├── models.py                    # RatingCurve, features, TransferModel, ModelBundle, baselines
│   ├── checks.py                    # deterministic health/trend/rain/replay checks
│   ├── evidence.py                  # builds Evidence
│   ├── decision.py                  # rules → candidate; VerdictTracker → committed Verdict
│   ├── estimator.py                 # substitute Estimate
│   ├── response.py                  # sensor + alert state machines, policy, actions, approvals
│   ├── community.py                 # message rendering + volunteer verification
│   ├── pipeline.py                  # Runner: the only cross-module wiring
│   ├── security/
│   │   ├── __init__.py
│   │   ├── transport.py             # sign / verify / canonical / stateless verify
│   │   └── audit.py                 # hash-chained log, verify, load, tamper helper
│   └── sim/
│       ├── __init__.py
│       ├── corrupt.py               # sensor emulator, faults, attacks, transport attacks, outages
│       └── scenarios.py             # Scenario model, manifest IO, signed stream, truth, expectations, episodes
│
├── dashboard/
│   ├── app.py                       # entry: session state, controls, playback loop, tabs
│   ├── live.py                      # Screen 1: Live Operations (+ phone/officer panel)
│   ├── evidence_view.py             # Screen 2: Incident / Evidence
│   ├── proof.py                     # Screen 3: Proof (packets, audit, tamper, expectations, evaluation)
│   ├── plots.py                     # Plotly figure builders
│   └── style.py                     # palette, CSS, icons, label maps (values owned by DESIGN.md)
│
├── tests/
│   ├── conftest.py                  # fixtures (settings, bundle, scenario runs)
│   ├── test_decision.py             # decision truth table, persistence
│   ├── test_security.py             # transport tamper, audit tamper
│   ├── test_pipeline.py             # scenario golden A–F, no-leakage, quarantine isolation, determinism, import layering
│   └── test_estimator_response.py   # estimator + state machine unit tests [P1]
│
└── docs/
    ├── PRD.md  ARCHITECTURE.md  RULES.md  DESIGN.md  TASKS.md  MEMORY.md  DEMO.md
```

### 2.2 Core package

| Module | Responsibility | Inputs | Outputs | May import | Must NOT |
|---|---|---|---|---|---|
| `domain.py` | All enums and dataclasses (§11); record (de)serialization helpers `record_to_dict`, `record_from_dict`. | – | Types | stdlib, numpy | contain logic beyond trivial helpers; import any other package module |
| `settings.py` | Load and validate `config/*.yaml` and `.env`; expose typed `Settings`; resolve threshold precedence (yaml > model.json > error). | file paths | `Settings` | `domain`, stdlib, `yaml` | read the dataset; hold mutable global state |
| `data.py` | Load `data/reach.csv` and `artifacts/model.json`; schema/UTC/gap validation; scenario slicing helpers. | paths, `Settings` | DataFrames, raw model dict | `domain`, `settings`, pandas, json | fit models; access network |
| `security/transport.py` | Canonical message, `sign`, stateful `verify`, `verify_stateless`, key loading, `TransportState`. | `SensorReading`, channel, tick ts, keys, config | transport flags + new state | `domain`, `settings`, stdlib (`hmac`, `hashlib`) | decide verdicts; read engine state; compare signatures with `==` |
| `security/audit.py` | Append-only JSONL hash chain; `verify`; `load`; `tamper_copy` (demo helper). | event kind + payload + ts | `AuditEvent`, `VerifyResult` | `domain`, stdlib | modify or delete past events; import anything else |
| `models.py` | `RatingCurve`; feature construction; `TransferModel` (Ridge) fit/predict/serialize; `ModelBundle` (variants, downstream model, scales, derived params); baselines (`StaticThresholdBaseline`, `RollingRobustZBaseline`). | `Window`, trust map, artifacts | `Prediction`, stage↔Q conversions, baseline flags | `domain`, `settings`, numpy, pandas, scikit-learn | import `checks`; read ground truth; use data later than `Window.ts` |
| `checks.py` | Deterministic checks: `health_checks`, `upstream_trend`, `rain_support`, `replay_match`, noise/clean ratios, drift measure. | `Window`, per-station parameters, config | `HealthResult`, trend, support values | `domain`, `settings`, numpy | import `models`; make verdicts |
| `evidence.py` | Assemble one `Evidence`: classify context from z-history, merge transport/health/trend/rain/downstream/replay, compute `notable`, `degraded`. | outputs of checks/models/transport, z-history | `Evidence` | `domain`, `settings`, `checks` | decide labels; import `models` or `response` |
| `decision.py` | Pure rules R1–R7 + PA1–PA3 → candidate (label, subtype, confidence, reasons); `VerdictTracker` persistence → `Verdict`. | `Evidence`, tracker state, config | `Verdict` | `domain`, `settings` | perform I/O; read time; know about scenarios |
| `estimator.py` | Build `Estimate` from a `Prediction`: stage conversion, interval, confidence, `in_use`. | `Prediction`, `ModelBundle`, config | `Estimate` | `domain`, `settings`, `models` | use the target sensor's own readings |
| `community.py` | `render(template_key, lang, **fields)`; `VerificationManager` (rounds, posterior, timeout, cooldown). | templates, replies, ticks | `Message`s, round state, resolution | `domain`, `settings` | contact anything external; override verdicts |
| `response.py` | Sensor state machines; alert state machine; `best_level`/`best_source`; policy mapping; action creation, approval tiers, officer controls. | `Verdict`, `Estimate`, `Evidence`, states, policy | new states, `Action`s, `Message`s | `domain`, `settings`, `community` | execute actions; write audit; read ground truth |
| `pipeline.py` | `Runner`: buffers, per-tick orchestration, audit-before-execute, error containment, operator inputs, history. **The only module that imports across engine modules.** | `TickInput`, operator calls | `TickRecord` | everything in the package except `sim/*` and `dashboard` | read `Scenario`/`TruthTick`; import `sim` |

### 2.3 Simulation

| Module | Responsibility | Inputs | Outputs | May import | Must NOT |
|---|---|---|---|---|---|
| `sim/corrupt.py` | Sensor emulator (noise, quantization); faults (DROPOUT, STUCK, SPIKE, NOISE_BURST, DRIFT, RANGE_OOB); attacks (FABRICATED_RAMP, SUPPRESSION; P1: REPLAY_COPY, SLOW_RAMP, STEALTH_BOUNDED, COORDINATED); feed outages; transport attacks (UNSIGNED_INJECT, REPLAY_PACKET). Pure functions over numpy/pandas. | true series, spec, seeded RNG | corrupted stage series, packet mutation directives | `domain`, `settings`, numpy | import `pipeline`, `evidence`, `decision`, `checks`, `models`, `response`, `estimator`, `community` |
| `sim/scenarios.py` | `Scenario` dataclass + manifest IO; `ScenarioStream` (yields signed `TickInput` and separate `TruthTick`, including warmup ticks); operator/volunteer scripts; `evaluate_expectations(scenario, records)`; random labeled episode generator (P1). | manifest, `data`, keys | streams, expectation results | `domain`, `settings`, `data`, `security/transport`, `sim/corrupt` | import `pipeline` or any engine module; leak truth into `TickInput` |

### 2.4 Front-ends and tooling

| Component | Responsibility | May import | Must NOT |
|---|---|---|---|
| `dashboard/app.py` | Session state (`Runner`, stream, history), scenario controls, playback via a timed fragment, tab layout. | `domain`, `settings`, `data`, `pipeline`, `sim.scenarios`, `security.audit`, dashboard modules | import engine internals (`models`, `checks`, `evidence`, `decision`, `estimator`, `response`, `community`) |
| `dashboard/live.py` | Screen 1 (+ simulated phone / officer approval panel). Reads `TickRecord`s; calls `Runner` operator methods. | `domain`, `plots`, `style` | compute verdicts, thresholds or statistics itself |
| `dashboard/evidence_view.py` | Screen 2. Renders the stored `Evidence`/`Verdict`/`Prediction` for a chosen incident. | `domain`, `plots`, `style` | recompute evidence |
| `dashboard/proof.py` | Screen 3. Packet inspector, audit table, chain verify, tamper demo, expectation results, evaluation figures. | `domain`, `security.audit`, `security.transport` (`verify_stateless` only), `sim.scenarios` (`evaluate_expectations`), `plots`, `style` | mutate the live audit log (tamper demo works on a copy) |
| `dashboard/plots.py`, `style.py` | Figure builders; palette/CSS/icons (values from DESIGN.md). | `domain`, plotly | import the package engine |
| `scripts/prepare_data.py` | Fetch + align (network allowed here only). | `settings`, `data` helpers, `requests`, `dataretrieval` | be imported by runtime code |
| `scripts/fit_models.py` | Train/calibrate, write `model.json` + `fit_report.json`, run gates. | `settings`, `data`, `models` | touch scenario windows (N-17) |
| `scripts/build_scenarios.py` | Write manifests from `config/reach.yaml` events and model-derived values. | `settings`, `data`, `sim.scenarios` (types) | run the engine |
| `scripts/run_scenario.py` | Drive `Runner` with a stream; print timeline; `--explain TICK`; `--export`; non-zero exit if expectations fail. | `pipeline`, `sim.scenarios`, `settings`, `data` | contain decision logic |
| `scripts/evaluate.py` | Batch episodes, baselines comparison, metrics/figures. | `pipeline`, `sim.scenarios`, `models` (baselines), `settings`, `data` | tune thresholds on held-out families |
| `tests/*` | See TASKS.md; pure/unit tests plus integration tests over real artifacts (skipped with a clear message if artifacts are absent). | any | depend on network |

---

## 3. Dependency rules

Lower layers MUST NOT import upper layers. A module may import only from **strictly lower** layers (plus stdlib/third-party).

| Layer | Modules |
|---|---|
| L0 | `domain`, `settings` |
| L1 | `data` |
| L2 | `security.transport`, `security.audit`, `models`, `checks` |
| L3 | `evidence`, `decision`, `estimator`, `community`, `sim.corrupt` |
| L4 | `response`, `sim.scenarios` |
| L5 | `pipeline` |
| L6 | `dashboard.*`, `scripts.*`, `tests.*` |

Specific rules (enforced by `tests/test_pipeline.py::test_import_layering` via AST scan):

1. `domain` imports no other package module; `settings` imports only `domain`.
2. `security.audit` imports only `domain`. `security.transport` imports only `domain` and `settings`.
3. `models` and `checks` MUST NOT import each other.
4. `evidence` may import `checks` but not `models`; model outputs arrive as `Prediction` objects.
5. `decision`, `estimator`, `community`, `response` MUST NOT import `pipeline` or each other, except `response → community` and `estimator → models`.
6. **`sim.*` MUST NOT import** `pipeline`, `evidence`, `decision`, `checks`, `models`, `estimator`, `response`, `community`.
7. Engine modules (`models` … `pipeline`) MUST NOT import `sim`, `dashboard` or `scripts`; they must not receive `Scenario` or `TruthTick` objects.
8. `dashboard` and `scripts` drive the system through `pipeline.Runner`. Dashboard exceptions are listed in §2.4 (read-only audit/transport helpers for the Proof screen).
9. Same-layer imports are forbidden unless listed above. No circular imports.
10. Configuration is passed as `Settings`; no module reads files or environment variables except `settings` (config, `.env`), `data` (dataset/artifacts), `security.audit` (its own log file), and scripts.

---

## 4. Data flow

One reading's path through a single `Runner.step(tick_input)`:

```text
SensorReading (per channel A,T,B,C)           ← from stream, signed
→ transport.verify          → transport flags (+ updated TransportState); HARD flag ⇒ packet rejected
→ normalize                 → stage → ln Q (RatingCurve); append to buffers; update hourly rain history
→ Window                    → arrays of recent stage/ln Q, missing masks, trust map, rain accumulations, ts, tick_idx
→ checks                    → HealthResult per sensor, up_trend, rain support, replay_match, noise ratios, drift measure
→ models                    → Prediction (target B; variant chosen from TRUSTED inputs) + downstream Prediction (C | B)
→ Evidence                  → context class (z_mean history), downstream, notable, degraded, all flags
→ Verdict                   → candidate (rules) → committed (VerdictTracker)
→ Estimate + Actions + States → estimator, response (sensor SM, alert SM, policy, community)
→ AuditEvent(s)             → appended BEFORE any action is marked EXECUTED
→ TickRecord                → appended to Runner.history
→ Dashboard / CLI           → read-only views and operator calls
```

### 4.1 `Runner.step` algorithm (authoritative sequence)

1. **Validate order.** `tick.ts` must be strictly greater than the previous tick; otherwise raise `TickOrderError` (state unchanged).
2. **Transport.** For each channel call `transport.verify`. Collect flags; for each HARD flag append `TRANSPORT_FAILURE` audit events (deferred to step 12). Rejected packets are treated as missing observations.
3. **Normalize.** For accepted readings compute `ln Q`; push into per-role ring buffers (size `required_history_ticks`). On hour boundaries (`tick.ts.minute == 0`) push the rain value into hourly history. Mark rain feed stale per RULES §3.6.
4. **Trust map.** From current sensor states: only `TRUSTED` sensors are usable as model inputs (N-13).
5. **Window.** Build the immutable `Window` (no element after `tick.ts`).
6. **Checks.** Run health checks for A,T,B,C; trend, rain support, replay match (if enabled), noise/clean ratios, drift measure.
7. **Models.** Predict B with the best available variant; predict C from B (downstream). Compute baselines (static threshold, rolling robust-z). Exceptions are caught here (§14).
8. **Evidence.** `evidence.build(...)` using the Runner-held z-history buffers.
9. **Decision.** `decision.decide(evidence)` → candidate; `VerdictTracker.update` → `Verdict`.
10. **Estimate.** `estimator.make_estimate(prediction, in_use)` where `in_use` follows RULES §10.6.
11. **Response.** `response.step(...)`: update sensor states (RULES §7), alert state (§8) from `best_level`/`best_source`, create policy actions (§12), apply community logic (`community.VerificationManager.tick`), render messages. Actions are `PENDING` or `AUTO_APPROVED`; Tier 1 follows `policy.demo_auto_approve_tier1`.
12. **Audit.** Append all events produced this tick (transport failures, verdict change, sensor/alert transitions, action creations/status changes, estimate start/stop, errors). On failure enter `HALTED_AUDIT` (RULES §17).
13. **Execute.** Only now mark approved/auto-approved actions `EXECUTED` (simulated) and append their `ACTION` status events.
14. **Pack.** Build `TickRecord` and append to history; return it.

Operator calls (`approve`, `reject`, `ack_release`, `submit_reply`, `officer_confirm`, `officer_clear`) are applied **between** ticks at the last tick's timestamp, follow the same audit-before-execute rule, and are reflected in the next `TickRecord` (`operator_events`).

---

## 5. Security architecture

### 5.1 Message format and signing
- Packet: `SensorReading(station_id, ts, seq, stage, unit, sig)`.
- Canonical string: `"{station_id}|{ts_utc_iso}|{seq}|{stage:.3f}|{unit}"`; `sig` = hex HMAC-SHA256 with the station's key; verification uses `hmac.compare_digest`.
- Keys: environment variables `STAGEPROOF_KEY_A|T|B|C` (read by `settings`); `.env.example` has **demo-only** values.

### 5.2 Checks and where they happen (`security/transport.py`)
| Check | Flag | Notes |
|---|---|---|
| Signature | `SIG_INVALID` | any alteration after signing, wrong key |
| Station binding | `STATION_MISMATCH` | `reading.station_id` ≠ channel; the id is inside the signed string, so cross-station replay is detectable |
| Unknown station | `UNKNOWN_STATION` | no key registered |
| Sequence | `SEQ_REPLAY` (HARD), `SEQ_GAP` (SOFT) | `TransportState.last_seq` advances only on valid packets |
| Timestamp | `TS_SKEW`, `TS_NONMONOTONIC` | skew vs. tick time; monotonic per station |
| Missing | `MISSING` (SOFT) | no packet on channel |

`verify_stateless` performs signature, binding and skew checks without touching state; it exists for the Proof screen's packet-tamper demonstration.

### 5.3 Quarantine
Implemented in `response.py` (sensor state machine, RULES §7, §9). Enforcement of *isolation* is structural: `pipeline` builds the trust map from sensor states, `models.ModelBundle.predict_*` only accepts inputs for roles marked trusted, and `estimator` never receives the target's own readings.

### 5.4 Audit hash chain (`security/audit.py`)
JSONL; each line `{"idx","ts","kind","payload","prev_hash","hash"}`; `hash = SHA-256(canonical_json({idx, ts, kind, payload, prev_hash}))`; canonical JSON = sorted keys, compact separators, floats rounded to 6 decimals; genesis `prev_hash` = 64 zeros. `ts` is the **simulated** tick time, so a seeded run reproduces the chain bit-for-bit. `verify(path)` returns `VerifyResult(ok, n_events, first_bad_index, reason)`. `tamper_copy(path, idx, field)` writes a modified copy (never the original) for demonstrations.

### 5.5 Threat model

| Adversary | Capability | Defense | Residual risk |
|---|---|---|---|
| **A1 Outsider, no keys** | forge, modify or replay packets | HMAC, station binding, sequence, timestamp → immediate ATTACK/TRANSPORT | none within the model |
| **A2 Compromised sensor/gateway/key (signs lies)** | send fabricated or suppressed readings that pass transport | physical-consistency layer (context PHANTOM/SUPPRESSED + upstream/rain/downstream) → ATTACK/FABRICATED or SUPPRESSION | attacker who also corrupts the independent inputs coherently (caught late by downstream mismatch, may be UNCERTAIN) |
| **A3 Replayer with recorded telemetry** | resend archived values | sequence check for packet replay; value-level replay match (P1) for verbatim copies | non-verbatim replays rely on context checks |
| **A4 Bounded stealth attacker** | keep deviation inside model tolerance | **not detected**; reported in limitations/evaluation | accepted |
| **A5 Insider editing history** | alter audit records | hash chain verification | deleting the entire log is out of scope |
| **A6 Spoofed volunteer replies** | fake community confirmation | min 2 informative replies, registered volunteers only, replies never override transport/verdicts | coordinated spoofing of several volunteers |

**A valid signature proves origin, not truth.** This is the central design principle: the transport layer authenticates, the physical layer verifies.

---

## 6. ML architecture

### 6.1 What the model is
A **Ridge regression** that learns a linear *routing filter* for log-discharge: the expected `ln Q` at the target gauge B is a weighted sum of recent upstream (A) and tributary (T) `ln Q` at several lags, plus log-transformed rainfall accumulations. The residual between observed and expected `ln Q`, divided by a calibrated robust scale, is the **z-score** used by the evidence layer.

### 6.2 Why Ridge (and why not more)
| Reason | Detail |
|---|---|
| **Extrapolation** | A linear model in log space extrapolates sensibly to record floods; tree models cap at the largest training value and would under-predict exactly when it matters. |
| **Small data** | A few years of 15-minute data and few floods; low-variance, regularized models are appropriate. |
| **Explainability** | Coefficients show the learned lags; judges can see "B follows A by ~N hours". |
| **Speed/reliability** | Trains in seconds; deterministic; no tuning rabbit hole. |
| **Role fit** | The model supplies a *calibrated expectation*, not a classifier. Decisions stay in transparent rules. |

Intentionally **not used:** LSTM/transformers/autoencoders (data-hungry, unexplainable, extrapolation risk), random forest/gradient boosting (cannot extrapolate), isolation forest as the engine (treats real floods as outliers; kept only conceptually as a baseline family via the rolling robust-z), fusion classifiers (violate the explainability principle).

### 6.3 Quantity space
- All models operate on **rating-converted discharge**: `ln Q = ln(rating(stage))` for every station, in training and in inference, so there is no train/serve mismatch. The raw USGS discharge series is used only to fit rating curves.
- Rating curve: per station power law `Q = a·(h − h0)^b` fitted by log-log regression with a grid search on `h0`; fallback to monotone interpolation if fit quality is poor. Both directions (`q_from_stage`, `stage_from_q`) exist; extrapolation beyond the observed range follows the power law.

### 6.4 Features and variants
- Target: `ln Q_B(t)`.
- Upstream features (for variants containing A): `ln Q_A(t − l)` for `n_lags` (default 7) lags centered on the cross-correlation travel time `τ_AB`, spaced by `max(1, round(τ_AB/6))` ticks. Same for T with `τ_TB`.
- Rain features: `ln(1 + P_n)` for `n ∈ {1, 3, 6, 12, 24, 48}` hours, where `P_n` sums the last `n` **completed** hourly values.
- Standardize features; intercept included; `RidgeCV` over `alpha ∈ logspace(-3, 3, 13)` with `TimeSeriesSplit(5)`, on the training range only.
- **Variants:** `A+T+R`, `A+T`, `A+R`, `T+R`, `A`, `T`, `R`. Each is fitted and calibrated separately and stored in ascending order of calibration scale. At inference the first variant whose every input is TRUSTED, present, and has full lag history is used. `R` (rain-only) is stored but yields `context = INSUFFICIENT` (RULES §3.3).
- Optional `highflow_weight` (config) up-weights training rows above the 90th percentile of `Q_B` if the high-flow bias gate fails.

### 6.5 Calibration and z-score
- On the held-out **calibration range** (clean data), residuals `r = ln Q_obs − ln Q_pred` give `scale = 1.4826 · MAD(r)`, computed in two regimes (normal; high-flow = predicted `Q_B` above its 90th training percentile). `scale_floor` from `thresholds.yaml` applies.
- `z = r / scale_regime`; `z_mean` over `context.z_window_ticks` is formed by the Runner/evidence layer.
- The estimate interval uses `± band_z × scale_regime` in log space, converted to stage via the rating inverse.

### 6.6 Downstream model
`ln Q_C(t)` from `ln Q_B` at lags centered on `τ_BC` plus rain accumulations; calibrated like the upstream models. `lag_BC_ticks = round(τ_BC)` is stored in `model.json`. It consumes B's **observed** readings, so a fabricated or suppressed B yields a large `z_down` after the physical delay.

### 6.7 Fit gates (initial engineering acceptance checks, not accuracy claims)
`scripts/fit_models.py` evaluates and records in `fit_report.json`; the script exits non-zero if a required gate fails:

| Gate | Check | Required |
|---|---|---|
| G-fit-1 | Held-out NSE of best variant in `ln Q` space on the calibration range | ≥ 0.90 |
| G-fit-2 | Separability: AUC between `|z|` of clean calibration samples and `|z|` after injecting a +30% (`ln 1.3`) phantom offset | ≥ 0.95 |
| G-fit-3 | Clean-data flag rate at `z_implausible` (RULES §5) | ≤ 1 per 100 sensor-days |
| G-fit-4 | Mean residual bias on the top flow decile | \|bias\| ≤ 0.10 ln units (else enable `highflow_weight` and refit) |

If a gate fails, remediate in this order: (1) revisit lag grid / reach choice, (2) enable `highflow_weight`, (3) shorten the aggregation (hourly) or choose a better reach. Record the final decision in MEMORY.md. These values may be revised only with a written justification.

### 6.8 Storage and loading
`artifacts/model.json` (no pickle): `{model_version, created_utc, data_hash, config_hash, tick_minutes, required_history_ticks, ratings{role:{type,params,range}}, lags{AB,TB,BC}, variants[{name, inputs, lags, feature_names, mean, std, coef, intercept, alpha, scale_normal, scale_high, high_flow_q_threshold}], downstream{...}, station_params{role:{spike_delta_max, noise_baseline_std, quant_step}}, rain{yes_mm, no_mm}, baseline_stats, lag_BC_ticks}`. `data.load_model_artifact` reads JSON; `models.ModelBundle.from_dict` builds the bundle; the bundle hash is logged in `SESSION_START`.

### 6.9 No future leakage
Feature construction takes only a `Window`, whose arrays end at `Window.ts`; `build_features` asserts that its maximum index ≤ the current index. Rain follows the completed-hour rule. Training uses chronological splits; scenario windows and evaluation windows are excluded from train/calibration (N-17). The prefix-invariance test (TASKS T047) verifies the whole system.

### 6.10 Baselines (comparison only, not StageProof behavior)
- `StaticThresholdBaseline`: alert when observed B stage ≥ `action_stage`.
- `RollingRobustZBaseline`: `(stage − median(W)) / (1.4826 · MAD(W))` over the last `baselines.window_ticks` accepted B readings (MAD floored at one quantization step); flag if `|value| > baselines.rollz_threshold`. This stands in for a generic single-sensor anomaly detector. Parameters live in `config/reach.yaml → baselines`.

---

## 7. State machines

Behavior is defined in RULES §7 (sensor) and §8 (alert). Implementation:

| Machine | Location | Notes |
|---|---|---|
| Sensor SM (per role A,T,B,C) | `response.py: SensorStateMachine` | holds `SensorStatus` (state, since_tick, reason, clean_streak, candidate_streak, needs_ack). A,T,C driven by transport + basic health only. |
| Alert SM (reach) | `response.py: AlertStateMachine` | consumes committed verdict, `best_level/best_source`, downstream result, estimate-warning streak, community resolution; may chain transitions within a tick; each transition logged. |
| Verdict persistence | `decision.py: VerdictTracker` | candidate vs committed; counters; REAL immediate; FAULT/ATTACK persistence; immediate subtypes. |
| Verification rounds | `community.py: VerificationManager` | round lifecycle: IDLE → OPEN → RESOLVED(confirmed/refuted) or TIMED_OUT; cooldown. |

All state lives in explicit objects owned by the `Runner`; none are module-level globals.

---

## 8. Estimation / fallback

Implementation of RULES §10:

1. `pipeline` builds the trust map; `models.predict_target` selects the first allowed variant → `Prediction`.
2. `estimator.make_estimate(prediction, bundle, in_use)` converts `logq_pred` and `logq_pred ± band_z·scale` into `stage_hat`, `stage_lo`, `stage_hi` with the B rating inverse; sets `confidence` from the variant's input set (A+T → HIGH, one of A/T → MED, rain-only → LOW, none → NONE); records `variant` and `basis`.
3. The estimate is computed **every tick** (it doubles as the "expected band" on the chart); `in_use` is set by `response` (RULES §10.6).
4. The estimator receives only `Prediction`/bundle data — never B's observed readings (N-13). Test: perturbing B readings during quarantine leaves `Estimate` unchanged.
5. Warning from estimates follows RULES §10.5 (`alert.est_warn_ticks`, interval lower bound).
6. No upstream available → rain-only variant → LOW confidence; no variant → confidence NONE; both handled per RULES §10.7–10.8.
7. A backward estimate from the downstream gauge is **not** part of this project (RULES specifies a forward estimate only).

---

## 9. Scenario architecture

### 9.1 Principles
- Scenarios are **data**: JSON manifests in `data/scenarios/` generated by `scripts/build_scenarios.py` from `config/reach.yaml → events` and model-derived values. Real data is the ground truth; the simulator only adds a sensor layer and corruptions.
- The stream yields `(TickInput, TruthTick)`; only `TickInput` goes into `Runner.step`. The driver may attach `TruthTick` to the resulting `TickRecord` for display/evaluation (never the engine).
- The stream begins `warmup_ticks` before the window of interest (≥ `required_history_ticks`); the dashboard fast-forwards warmup.
- Scenario windows lie in the **test** range; the builder asserts non-overlap with train/calibration ranges.

### 9.2 Manifest schema (JSON)

```text
id, title, description, seed
window {start, end}                 # UTC ISO; stream starts at start − warmup
warmup_ticks
event_start_tick                    # reference point for expectations (tick offset in window)
corruptions[]                       # {role, type, start_tick, end_tick, params}
compromised_keys[]                  # roles whose corrupted packets are signed with a VALID key
transport_attacks[]                 # {role, type: UNSIGNED_INJECT|REPLAY_PACKET|..., tick, params}
feed_outages[]                      # {feed: A|T|C|RAIN, start_tick, end_tick}
volunteer_script[]                  # {tick_offset, volunteer_id, code}
operator_script[]                   # {tick_offset, action: approve|ack_release|officer_confirm|officer_clear, ...}
ground_truth {label, subtype, truth_notable}
expected {                          # consumed by evaluate_expectations()
  committed[]  : {label, subtype?, from_tick, to_tick, tolerance_ticks}
  sensor[]     : {role, state, by_tick}
  alert        : {must_not_exceed? | must_reach?, source?, within_ticks?}
  actions      : {must_include[], must_not_include[]}
  baselines    : {rollz_flags_min?, threshold_alarm: expected_true|expected_false}
}
```

### 9.3 Scenarios

| ID | Scenario | Data window / construction | Corruption | Expected behavior (RULES §19) |
|---|---|---|---|---|
| **A** | Real flood | `events.main_flood`; no corruption | sensor-noise emulation only | REAL FLOOD from the first consistent notable tick; PROVISIONAL (OBSERVED) ≤ 2 ticks after crossing `action_stage`; CONFIRMED after downstream YES; no FAULT/ATTACK; rolling-z baseline flags ≥ 1 tick |
| **B** | Sensor fault | rising limb of `events.moderate_event`, level below `action_stage`; builder asserts the true flow changes enough to satisfy the STUCK condition | B `STUCK` for ≥ 12 ticks | SENSOR FAULT/STUCK committed; B QUARANTINED; maintenance ticket; no public warning; recovery path after the fault ends |
| **C** | Fabricated flood, valid key | `events.dry_window` (no rain, flat upstream); builder sets `peak_stage` ≥ `action_stage` + margin | B `FABRICATED_RAMP` (rise ~6 h, hold, decline); `compromised_keys=[B]` | all packets pass transport; POSSIBLE CYBER ATTACK/FABRICATED; B QUARANTINED; security alert + evidence; alert never above WATCH; zero public warnings; threshold baseline would alarm |
| **D** | Suppressed real flood, valid key | `events.main_flood` | B `SUPPRESSION` from the rising limb across the crest; strength chosen so observed stays below `action_stage` while truth exceeds it (builder asserts) | POSSIBLE CYBER ATTACK/SUPPRESSION; B QUARANTINED; PROVISIONAL (ESTIMATE); threshold baseline stays silent |
| **E** | Uncertain: upstream telemetry outage | a real flood window | `feed_outages` for A and T during the rise; `volunteer_script` replies (1, 1) | rain-only → UNCERTAIN/INSUFFICIENT_INPUTS; WATCH; verification round; community-confirmed → PROVISIONAL (COMMUNITY); REAL FLOOD after A returns |
| **F** | Unsigned / replayed packets | moderate or dry window | `transport_attacks`: `UNSIGNED_INJECT` (altered value, bad signature) then `REPLAY_PACKET` (reused seq); `operator_script` may `ack_release` later | SIG_INVALID/SEQ_REPLAY → ATTACK/TRANSPORT committed at 1 tick; B QUARANTINED; estimate in use; audit verifies OK; tampering the saved log is detected |

### 9.4 Evaluation episodes (P1)
`make_episodes(...)` creates labeled episodes (clean, real flood on test floods, faults, attacks) by sampling start ticks in the test range. **Tuning families:** FABRICATED_RAMP, SUPPRESSION, STUCK/DROPOUT/SPIKE/NOISE/DRIFT. **Held-out families:** REPLAY_COPY, SLOW_RAMP, COORDINATED, STEALTH_BOUNDED, plus an adaptive attacker that adds realistic noise. Thresholds are tuned on tuning families only. `evaluate.py` reports the real-extreme false-rejection rate, detection delay, false-quarantine rate, estimate error/coverage, confusion matrix and the families that fail.

---

## 10. Runtime constraints

| Constraint | Implementation |
|---|---|
| **Offline** | Only `scripts/prepare_data.py` touches the network. Runtime reads `data/`, `artifacts/`, `config/`. A test or the demo checklist runs with networking disabled. |
| **Deterministic** | Seeded `numpy.random.Generator`; no wall-clock in decisions or audit (`ts` = simulated time); stable dict ordering in canonical JSON. |
| **No LLM in the safety path** | No model APIs, no generated text; messages come from templates. |
| **No ground truth at runtime** | `Runner`, engine modules and `models` never receive `Scenario`/`TruthTick`; `sim` cannot be imported by them (layer rules). |
| **No database** | State in memory; audit in JSONL; artifacts in JSON; replay cache in JSON. |
| **No unnecessary services** | No API server, broker, container orchestration or background workers; the dashboard runs the `Runner` in-process. |
| **No future data** | `Window` ends at the current tick; rain completed-hour rule; prefix-invariance test. |
| **Secrets** | Demo keys only in `.env.example`; real `.env` gitignored. |

---

## 11. Domain objects

All defined in `domain.py` as plain dataclasses (frozen where practical) and `Enum`s.

### Enums
`Label` {REAL_FLOOD, SENSOR_FAULT, POSSIBLE_CYBER_ATTACK, UNCERTAIN, NORMAL} · `SensorState` {TRUSTED, SUSPECT, QUARANTINED, RECOVERING} · `AlertState` {NONE, WATCH, PROVISIONAL_WARNING, CONFIRMED_WARNING, CLEARED} · `Context` {CONSISTENT, PHANTOM, SUPPRESSED, AMBIGUOUS, INSUFFICIENT} · `Trend` {RISING, FLAT, FALLING, UNKNOWN} · `Support` {YES, NO, UNKNOWN} · `Downstream` {YES, NO, PENDING, UNKNOWN} · `Conf` {NONE, LOW, MED, HIGH} · `ActionType` {QUARANTINE_SENSOR, USE_ESTIMATE, OPEN_MAINTENANCE_TICKET, RAISE_SECURITY_ALERT, PRESERVE_EVIDENCE, NOTIFY_OFFICER, REQUEST_VERIFICATION, SEND_PUBLIC_WARNING, SEND_CORRECTION, SEND_ALL_CLEAR, RECOMMEND_EVACUATION, ACK_RELEASE} · `ActionStatus` {PENDING, AUTO_APPROVED, APPROVED, REJECTED, EXECUTED, SKIPPED}.

### Dataclasses

| Object | Key fields | Created by / lifecycle |
|---|---|---|
| `SensorReading` | `station_id, ts, seq, stage, unit, sig` | `sim.scenarios` (signed) → `Runner.step`; immutable |
| `TickInput` | `ts, tick_idx, readings{channel: SensorReading|None}, rain_prev_hr_mm, rain_feed_ok` | stream → Runner; immutable; contains no truth |
| `TruthTick` | `ts, true_stage{role}, true_q_B, truth_label, corrupted_roles, truth_notable` | stream only; never enters the engine |
| `Window` | `ts, tick_idx, stage{role:array}, logq{role:array}, missing{role:array}, trust{role:bool}, rain_hourly, rain_acc{n:mm}, rain_available` | built each tick in `pipeline` |
| `Prediction` | `role, ts, variant, inputs_used, logq_obs, logq_pred, scale, regime, z, q_pred, stage_pred, degraded, error` | `models` → evidence/estimator |
| `Evidence` | `ts, tick_idx, transport_flags, transport_hard, health_flags, shape_ok, rate_exceeded, variant, z, z_mean, context, up_trend, rain, downstream, z_down_mean, replay_match, noise_too_clean, drift, obs_stage, pred_stage, notable, degraded, trusted_inputs` | `evidence.build`, one per tick |
| `Verdict` | `ts, tick_idx, candidate, candidate_subtype, label, subtype, confidence, persist_count, persist_needed, rule_id, reasons[], changed` | `decision` |
| `SensorStatus` | `role, state, since_tick, reason, clean_streak, candidate_streak, needs_ack` | `response` |
| `Estimate` | `ts, stage_hat, stage_lo, stage_hi, q_hat, variant, basis[], confidence, in_use` | `estimator` |
| `Action` | `action_id, ts, type, tier, status, target, payload, approver, executed_ts, source` | `response` → executed by `pipeline` after audit |
| `Message` | `msg_id, ts, channel (SMS|IVR|OFFICER|TICKET), audience, language, text, action_id, recipient_id` | `community.render` via `response` |
| `VolunteerReply` | `ts, volunteer_id, code, round_id` | operator/driver → `community` |
| `AuditEvent` | `idx, ts, kind, payload, prev_hash, hash` | `audit`; immutable |
| `Scenario` | manifest fields (§9.2) | `sim.scenarios` |
| `TickRecord` | `tick_idx, ts, readings, accepted, transport_flags, prediction, downstream_prediction, evidence, verdict, estimate, sensor_status{role}, alert_state, alert_source, best_level, best_source, new_actions, new_messages, verification, baselines, audit_events_new, audit_head_hash, operator_events, halted_audit, truth (optional, attached by driver)` | `Runner.step`; the unit the dashboard and CLI consume |

---

## 12. Technology and dependencies

| Package | Use | Notes |
|---|---|---|
| Python ≥ 3.11 | language | |
| numpy, pandas | arrays, dataset | |
| scikit-learn | `RidgeCV`, `TimeSeriesSplit`, metrics | only Ridge-related use |
| PyYAML | config | |
| streamlit, plotly | dashboard | |
| pytest | tests (dev extra) | |
| requests, dataretrieval | data prep only (`data` extra) | Not needed at runtime. Prefer the maintained USGS API client path; verify the endpoint at prep time because USGS is migrating its web services. Always cache raw responses. |

Not used: SciPy, Pydantic, FastAPI, any database/ORM, Docker (stretch only), LLM SDKs, message brokers.

Standard library: `hmac`, `hashlib`, `json`, `dataclasses`, `enum`, `datetime`, `logging`, `collections.deque`, `pathlib`, `argparse`, `ast` (layering test).

---

## 13. File and data formats

### 13.1 `data/reach.csv`
Columns (UTC, 15-minute grid, ISO-8601 `ts_utc`): `A_stage, A_q, T_stage, T_q, B_stage, B_q, C_stage, C_q, rain_mm_prev_hr`. `*_q` is the source discharge (used only to fit ratings). `rain_mm_prev_hr` is the basin-mean precipitation of the **most recent completed hour as of that row** (forward-filled from the :00 rows; the hourly value stamped H is the sum over the hour ending at H, so it is available from H). Blank = missing; units recorded in `reach.yaml`.

### 13.2 `config/reach.yaml` (structure)
`tick_minutes: 15` · `units{stage, discharge}` · `stations{A,T,B,C: {usgs_site, name, role, quant_step, sensor_min, sensor_max, noise_std, key_env}}` · `levels{B: {watch_stage, action_stage, clear_stage, evac_stage|null}}` · `rain{points[{lat, lon, weight, station}], source}` (`station` = the precipitation station identifier used by `rain.source` — e.g. an IEM ASOS id) · `splits{train, calibration, test}` · `events{main_flood, moderate_event, dry_window, outage_event?}` · `baselines{window_ticks, rollz_threshold}` · `fit{n_lags, highflow_weight, highflow_quantile}`.

### 13.3 Other configs
`thresholds.yaml` — structure and defaults exactly as RULES §5. `policy.yaml` — `demo_auto_approve_tier1`, `tiers{ActionType: 0|1|2}`, `triggers{...}` encoding RULES §12. `messages.yaml` — `templates{key:{en, hi}}`, `reasons{code: text}`, `volunteers[{id, name, language, station, reliability}]` (wording owned by DESIGN.md).

### 13.4 Audit JSONL, replay cache
Audit: see §5.4. Replay cache: one JSON file per scenario containing `[TickRecord dict]` produced by `domain.record_to_dict` (enums as strings, timestamps ISO) plus the audit file path and the scenario manifest hash.

---

## 14. Failure handling (mechanisms; behavior in RULES §17)

| Situation | Mechanism |
|---|---|
| Missing/stale inputs | `Window.missing` masks and `trust` map; variant fallback in `models`; `degraded` flag in `Evidence`/`Prediction`. |
| Model/predict exception | `try/except` around step 7 in `Runner`; `Prediction.error` set; `context = INSUFFICIENT`; `SYSTEM_ERROR` audit event. |
| Artifact/config problems | `settings`/`data` raise `ConfigError`/`ArtifactError` with an actionable message (`make fit`, `make data`); scripts exit 2. |
| Audit failure | `AuditError` caught in the Runner → `halted_audit = True`; actions not executed; dashboard banner. |
| Tick order violation | `TickOrderError`; no state mutation. |
| Dashboard component failure | each screen function wrapped with an error card; Runner unaffected. |
| Cache load failure | CLI/dashboard fall back to live run; message shown. |

---

## 15. Principal interfaces (names and semantics; Qoder may refine signatures but MUST preserve semantics and update this section)

| Module | Interface |
|---|---|
| `settings` | `load_settings(config_dir, env_path) -> Settings` |
| `data` | `load_dataset(path) -> DataFrame`; `load_model_artifact(path) -> dict` |
| `transport` | `canonical_message(r)`, `sign(r_unsigned, key) -> SensorReading`, `verify(channel, r, tick_ts, state, keys, cfg) -> (flags, state)`, `verify_stateless(...) -> flags`, `TransportState` |
| `audit` | `AuditLog(path)`, `.append(kind, payload, ts) -> AuditEvent`, `verify(path) -> VerifyResult`, `load(path)`, `tamper_copy(path, idx, field) -> Path` |
| `models` | `RatingCurve.fit/q_from_stage/stage_from_q`, `build_features`, `TransferModel.fit/predict`, `ModelBundle.from_dict`, `.predict_target(window, trust) -> Prediction`, `.predict_downstream(window) -> Prediction|None`, baselines `.update(...) -> flags` |
| `checks` | `health_checks`, `upstream_trend`, `rain_support`, `replay_match`, `drift_measure` |
| `evidence` | `build_evidence(...) -> Evidence` |
| `decision` | `decide(evidence, cfg) -> Candidate`; `VerdictTracker.update(candidate, evidence) -> Verdict` |
| `estimator` | `make_estimate(prediction, bundle, in_use, cfg) -> Estimate` |
| `response` | `SensorStateMachine`, `AlertStateMachine`, `ResponseEngine.step(...)`, `approve/reject/ack_release/officer_confirm/officer_clear` |
| `community` | `render(key, lang, **fields) -> str`, `VerificationManager.open/handle_reply/tick` |
| `pipeline` | `Runner(settings, bundle, dataset_meta, audit_path, scenario_id, seed)`; `.step(tick_input) -> TickRecord`; operator methods; `.history`; `.close()` |
| `sim.scenarios` | `load_scenario(path)`, `ScenarioStream(scenario, dataset, keys, settings)`, `evaluate_expectations(scenario, records) -> list[ExpectationResult]`, `make_episodes(...)` |
| `sim.corrupt` | `emulate_sensor`, `apply_fault`, `apply_attack`, `apply_outage`, `mutate_packet` |

---

## 16. Requirement traceability

| PRD requirement | Implemented in | TASKS |
|---|---|---|
| FR-01, FR-02 | `data.py`, `scripts/prepare_data.py` | T006–T009 |
| FR-03, FR-04 | `sim/corrupt.py`, `sim/scenarios.py`, `scripts/build_scenarios.py` | T034–T040 |
| FR-05, FR-06 | `security/transport.py` | T010 |
| FR-07, FR-08, FR-09 | `checks.py` | T018–T020 |
| FR-10, FR-11 | `models.py`, `scripts/fit_models.py` | T012–T016 |
| FR-12, FR-13, FR-14 | `evidence.py`, `decision.py` | T021–T024 |
| FR-15, FR-16, FR-18, FR-19 | `response.py`, `pipeline.py` | T026–T028, T031–T033 |
| FR-17 | `estimator.py` | T025 |
| FR-20, FR-21 | `community.py` | T029, T030 |
| FR-22, FR-23 | `security/audit.py`, `pipeline.py` | T011, T032 |
| FR-24, FR-25, FR-26 | `dashboard/*` | T051–T056 |
| FR-27 | `scripts/run_scenario.py` | T041, T042 |
| FR-28 | `dashboard/app.py`, cache export | T057 |
| FR-29 | `models.py` baselines | T017 |
| FR-30 | `scripts/evaluate.py` | T043 |
| FR-31 | `tests/*` | T024, T044–T050 |

| RULES area | Implementation location |
|---|---|
| §3.1 transport evidence | `security/transport.py` |
| §3.2, 3.4, 3.6, 3.8, 3.9 checks | `checks.py` |
| §3.3, 3.5 residual/context/downstream | `models.py` (predictions) + `evidence.py` (classification) |
| §4 decision | `decision.py` |
| §5 thresholds | `config/thresholds.yaml` via `settings.py` |
| §6 persistence | `decision.py: VerdictTracker` |
| §7–8 state machines | `response.py` |
| §9–11 quarantine, fallback, warning | `response.py`, `estimator.py`, `pipeline.py` |
| §12 approvals | `response.py` + `config/policy.yaml` |
| §13 community | `community.py` |
| §14–15 security, audit | `security/*`, `pipeline.py` |
| §16–18 safety, failures, N-rules | `pipeline.py` (containment), tests (T044–T049) |
| §19 scenario contract | `sim/scenarios.py: evaluate_expectations`, `tests/test_pipeline.py` |
