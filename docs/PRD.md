# StageProof — PRD.md

**Authority:** This document owns **WHAT** StageProof is and **WHY** it exists: scope, priorities, requirement IDs and success criteria.
**Defers to:** ARCHITECTURE.md (structure), RULES.md (behavior — wins any behavioral conflict), DESIGN.md (UI), TASKS.md (order of work), DEMO.md (judging flow), MEMORY.md (summary context only).

**Status:** Hackathon prototype. All data is replayed or simulated. Nothing here has been deployed, validated, or certified for operational use.

**Priority tags:** **MVP** (must work for submission) · **P1** (strengthens submission, cut first if late) · **STRETCH** (only if everything else is done) · **OUT** (not built).

---

## 1. Product overview

**StageProof — AI-Powered Cyber-Physical Integrity for Flood Early Warning Systems.**
Tagline: *Verify the reading before you sound the alarm.*

StageProof is an integrity layer that sits between river-level sensors and the decisions made from them. When a gauge reports a level, StageProof checks whether that reading is **trustworthy**: authentic as a message, plausible as a sensor output, and consistent with what the rest of the river system (upstream and tributary gauges, rainfall, downstream gauge) says should be happening.

An extreme reading can mean four different things. StageProof separates them:

| Outcome | Meaning | What should happen |
|---|---|---|
| **REAL FLOOD** | The river is genuinely high and the context agrees. | Warn without delay. |
| **SENSOR FAULT** | The sensor output is physically implausible (stuck, dropout, spike, noise, out-of-range, or slow drift). | Quarantine the sensor, substitute an estimate, open a maintenance ticket. |
| **POSSIBLE CYBER ATTACK** | The message failed an integrity check, or the reading looks believable but is contradicted by independent evidence (fabricated flood, suppressed flood, replay, downstream mismatch). | Quarantine, preserve evidence, alert security, never warn the public from the suspect data. |
| **UNCERTAIN** | Evidence is missing, stale or conflicting. | Notify officials, request community verification, hold at WATCH. |

(A fifth state, **NORMAL**, means no notable level and no persistent anomaly. It is not an alert.)

## 2. Problem statement

A warning system may **trust a sensor reading without verifying that it is physically consistent**. This creates two opposing risks:

- **False alarms.** A fabricated, replayed or faulty high reading triggers an unnecessary warning or emergency action, which wastes resources and erodes public trust in later warnings.
- **Missed or delayed warnings.** A suppressed or faulty low reading hides a real flood. Separately, a generic anomaly detector may reject a genuine record flood *because it looks abnormal*, which is the worst possible time to discard it.

Cryptographic message authentication does not close this gap. **A valid signature proves who sent a message, not whether it is true.** A compromised sensor, a stolen key or a tampered probe produces correctly signed lies. The river, however, obeys physics: upstream flow, tributary flow and rainfall constrain what a downstream gauge can plausibly read. StageProof uses that physical context as a second line of defense.

## 3. Product goal

> **Verify whether a sensor reading is trustworthy before it drives an emergency decision.**

Supporting objectives:

| ID | Objective |
|---|---|
| G1 | Classify the target gauge's reading as NORMAL, REAL FLOOD, SENSOR FAULT, POSSIBLE CYBER ATTACK or UNCERTAIN, with reasons a non-ML reviewer can follow. |
| G2 | Never let a suspect reading drive a public warning, while not delaying warnings for corroborated real floods. |
| G3 | Keep operating when a sensor is quarantined, by substituting a labeled estimate with an uncertainty interval. |
| G4 | Keep humans in control of high-impact actions and include a simulated community-verification path for uncertain cases. |
| G5 | Make every consequential decision tamper-evident through a hash-chained audit log. |
| G6 | Be demonstrable offline, deterministically, within a few minutes. |

## 4. Target users

StageProof is a prototype; these are the intended users of a hypothetical real deployment, not current users.

| User | Need |
|---|---|
| **Flood control / operations officer** | A clear answer to "can I trust this reading enough to act?" and a place to approve warnings. |
| **Emergency response team** | Warnings with a stated basis (observed, estimated, community-confirmed). |
| **Infrastructure operator / gauge network engineer** | Fault diagnosis (which sensor, what kind) and maintenance tickets. |
| **Technical / security team** | Evidence of suspected tampering, preserved for forensics, and a verifiable audit trail. |
| **Community verification participants** (volunteers near the gauge) | A very simple prompt that works on a basic phone, in their language, and a way to reply "yes / no / not sure". |

## 5. Core use cases

| ID | Use case | Demo scenario | Primary outcome |
|---|---|---|---|
| UC-01 | Genuine flood | A | REAL FLOOD, warning progression, no false rejection |
| UC-02 | Sensor fault | B | SENSOR FAULT (stuck), quarantine, estimate, ticket |
| UC-03 | Fabricated flood signal (valid key) | C | POSSIBLE CYBER ATTACK, quarantine, no public warning |
| UC-04 | Suppressed flood signal (valid key) | D | POSSIBLE CYBER ATTACK (suppression), warning from the estimate |
| UC-05 | Uncertain conditions | E | UNCERTAIN, WATCH, community verification |
| UC-06 | Unsigned / replayed transport attack | F | POSSIBLE CYBER ATTACK (transport), immediate quarantine |
| UC-07 | Fallback estimation | B, C, D, F | Labeled estimate with interval while a sensor is quarantined |
| UC-08 | Community verification | E | Posterior from volunteer replies; confirmation enables a warning |
| UC-09 | Audit verification | all | Chain verifies; a tampered record is detected |

## 6. Functional requirements

References in brackets point to the owning behavior in RULES.md; structure is in ARCHITECTURE.md.

### Data and simulation
| ID | Requirement | Priority |
|---|---|---|
| FR-01 | Load a pre-prepared, aligned 15-minute dataset (target, upstream, tributary, downstream stage and discharge, plus hourly rainfall) from disk with no network access. | MVP |
| FR-02 | Provide a data-preparation script that downloads and aligns source data once (the only network-using component). | MVP |
| FR-03 | Run deterministic scenarios A–F from manifests and a seed, with sensor-noise emulation, faults, attacks, transport attacks and feed outages. Ground truth is kept separate from the engine. | MVP |
| FR-04 | Extended adversary models (verbatim replay copy, slow ramp, bounded stealth, coordinated A+B) and random labeled episodes for evaluation. | P1 |

### Ingestion and transport integrity
| ID | Requirement | Priority |
|---|---|---|
| FR-05 | Every sensor message carries station id, timestamp, sequence number, stage, unit and an HMAC signature. Verify signature, station binding, sequence and timestamp. [RULES §3.1, §14] | MVP |
| FR-06 | HARD transport failures reject the packet and are evidence; SOFT flags (gap, missing) are tracked but never alone imply an attack. | MVP |

### Sensor health and physical consistency
| ID | Requirement | Priority |
|---|---|---|
| FR-07 | Sensor-health checks: dropout, range, spike-and-revert, stuck (target only), noise burst (target only). A rate of rise alone never rejects a reading. [RULES §3.2, N-10] | MVP |
| FR-08 | Upstream trend, rainfall support (completed hourly values only) and downstream confirmation (delayed by physics). [RULES §3.4–3.6] | MVP |
| FR-09 | Replay-match detection (verbatim copies) and "noise too clean" booster. | P1 |

### ML integrity model
| ID | Requirement | Priority |
|---|---|---|
| FR-10 | A Ridge regression on lagged log-discharge and rainfall accumulations predicts the expected target flow from **trusted** inputs only; residuals are converted to calibrated z-scores. Variants exist for degraded input sets. | MVP |
| FR-11 | A fit script trains on a chronological train range, calibrates on a held-out range, never touches scenario windows, and writes a JSON model plus a fit report with go/no-go gates. | MVP |

### Evidence, decision, persistence
| ID | Requirement | Priority |
|---|---|---|
| FR-12 | Produce one typed `Evidence` record per tick covering transport, health, residual/context, upstream trend, rainfall, downstream, replay and notable level. | MVP |
| FR-13 | Deterministic decision engine implementing RULES R1–R7 and post-adjustments, with reason codes and confidence. | MVP |
| FR-14 | Persistence/hysteresis converts candidate verdicts into committed verdicts; REAL commits immediately, FAULT/ATTACK need persistence unless immediate subtype. [RULES §6] | MVP |

### Response: quarantine, fallback, alerts
| ID | Requirement | Priority |
|---|---|---|
| FR-15 | Sensor state machine (TRUSTED, SUSPECT, QUARANTINED, RECOVERING) per sensor, with automatic quarantine and the release rules in RULES §7. | MVP |
| FR-16 | A quarantined or suspect sensor is excluded from decisions and from other sensors' model inputs. [N-1, N-13] | MVP |
| FR-17 | Forward substitute estimate from trusted upstream, tributary and rain inputs only, with interval, variant, confidence and "ESTIMATED" provenance. [RULES §10] | MVP |
| FR-18 | Alert state machine (NONE, WATCH, PROVISIONAL, CONFIRMED, CLEARED) driven by the best level, with retraction. [RULES §8, §11] | MVP |
| FR-19 | Action policy with approval tiers (0 automatic, 1 one-tap, 2 human only); evacuation is never automatic. [RULES §12] | MVP |

### Community verification
| ID | Requirement | Priority |
|---|---|---|
| FR-20 | Simulated volunteers receive a localized prompt (English and Hindi), reply 1/2/3, and a posterior is updated; confirmation needs at least two informative replies. [RULES §13] | MVP |
| FR-21 | Public and volunteer messages are rendered from templates (no generated text). | MVP |

### Audit
| ID | Requirement | Priority |
|---|---|---|
| FR-22 | Append-only, hash-chained audit log of the events listed in RULES §15, with a verifier that identifies the first bad index. | MVP |
| FR-23 | No action is executed before its audit event is appended. [N-5] | MVP |

### Dashboard and tooling
| ID | Requirement | Priority |
|---|---|---|
| FR-24 | Streamlit dashboard with three screens: Live Operations, Incident/Evidence, Proof. [DESIGN.md] | MVP |
| FR-25 | Scenario selector, play/pause/step/speed, ground-truth reveal toggle, visible SIMULATED-mode indicator, officer approval controls and a simulated phone panel. | MVP |
| FR-26 | Proof screen: signed-packet inspector, replay/sequence evidence, audit table, hash-chain verification and tamper demonstration, scenario expectation results. | MVP |
| FR-27 | Headless CLI to run a scenario, print a verdict timeline, explain any tick, export a replay cache, and fail with a non-zero exit code if expectations are not met. | MVP |
| FR-28 | Cached playback of exported `TickRecord`s as a fallback when live execution misbehaves. | P1 |
| FR-29 | Comparison baselines computed alongside every run: static threshold alarm and rolling robust-z "single-sensor anomaly detector". | MVP |
| FR-30 | Batch evaluation script producing metrics and figures (including the real-extreme false-rejection comparison and held-out adversary families). | P1 |
| FR-31 | Automated tests covering the decision truth table, transport tamper, audit tamper, scenario golden behavior (A–F), no-future-data leakage, quarantine isolation, determinism and import layering. | MVP |

## 7. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-01 | **Deterministic:** same data, config, model and seed produce identical verdicts, actions and audit hashes. |
| NFR-02 | **Explainable:** every verdict has reason codes mapped to plain language; no black-box classifier. |
| NFR-03 | **Safe:** asymmetric caution and human approval for high-impact actions (RULES §16, §12). |
| NFR-04 | **Secure:** HMAC with constant-time comparison, no secrets in the repository, tamper-evident audit. |
| NFR-05 | **Testable:** decision logic is pure; scenarios are manifest-driven with golden expectations. |
| NFR-06 | **Reproducible:** pinned dependency ranges, seeds recorded in the audit log, data prepared by a single script. |
| NFR-07 | **Offline at runtime:** after data preparation, the demo MUST run with networking disabled. |
| NFR-08 | **No future-data leakage:** the engine uses only data available at the current tick. [N-14] |
| NFR-09 | **No LLM and no remote call in the safety path.** [N-8] |
| NFR-10 | **Responsive enough for a live demo:** processing one tick MUST feel instantaneous at the demo's playback speed on a laptop. |
| NFR-11 | **No database and no API server** in this project; state is in memory plus a JSONL audit file and JSON artifacts. |
| NFR-12 | **Minimal dependencies:** only those listed in ARCHITECTURE.md §12. |

## 8. Scope

### MVP
- Scenarios **A–F** with manifests, deterministic streams, sensor-noise emulation, faults, fabricated/suppressed attacks, transport attacks and feed outages.
- Transport security (HMAC, station binding, sequence, timestamp, replay) and hash-chained audit.
- Physical checks (health, trend, rain support, downstream).
- Ridge lagged log-discharge model with calibration and degraded variants.
- Evidence, decision engine, persistence, sensor and alert state machines, quarantine.
- Forward substitute estimate; response policy with approval tiers.
- Simulated community verification (English and Hindi templates).
- Baseline comparators; CLI; tests listed in FR-31.
- Streamlit dashboard (three screens).

### P1 (cut in this order if time is short)
Cached playback · random labeled episodes and extended adversaries · replay-match check and "too clean" booster · batch evaluation script and Proof-screen evaluation panel · estimator/response unit tests.

### Stretch (only if everything above is complete)
Docker packaging of the demo · exploratory notebooks · additional reaches via configuration only. Anything not listed in this document is out of scope.

## 9. Out of scope

- Real emergency deployment, integration with real warning systems, or any claim of operational readiness.
- **Automatic evacuation orders** (the system may only *recommend*, for human decision).
- Real SMS/IVR/telephony; real volunteers.
- Cloud services or any runtime network dependency.
- **SQLite or any database.**
- **API servers or microservices** (including FastAPI), message brokers, containers orchestration.
- **LLM-based safety decisions** or generated text in the decision path.
- Flood forecasting, hydrodynamic modeling, maps/GIS, mobile apps, authentication, multi-tenant roles.
- Deep learning models, tree ensembles for the integrity model, fusion classifiers.
- Hardware, LoRa or physical sensors.

## 10. Success criteria

Criteria are pass/fail engineering checks or **reported measurements**. No accuracy percentage is promised; measured values are reported as found, with their limitations.

| ID | Criterion | How verified |
|---|---|---|
| SC-01 | Scenarios A–F meet the behavioral contract in RULES §19. | `tests/test_pipeline.py` golden checks; CLI exit code |
| SC-02 | In Scenario C, all packets pass transport yet the system commits POSSIBLE CYBER ATTACK, quarantines B, and issues **zero** public warnings. | CLI + test |
| SC-03 | In Scenario D, the system issues a warning sourced from the **estimate** while B is quarantined. | CLI + test |
| SC-04 | In Scenario A, no FAULT/ATTACK commit occurs, and the single-sensor rolling-z baseline flags the real flood as anomalous at least once. | test + dashboard comparison strip |
| SC-05 | Transport tampering (altered value, reused sequence, wrong station, bad key, timestamp skew) is detected. | `tests/test_security.py` |
| SC-06 | Audit chain verifies when untouched and fails at the correct index after any edit, deletion or reordering. | `tests/test_security.py` + Proof screen demo |
| SC-07 | Prefix invariance: results up to tick *t* are identical when all data after *t* is replaced. | no-leakage test |
| SC-08 | Quarantined sensors do not influence other sensors' predictions; B's estimate is invariant to B's own quarantined readings. | quarantine-isolation test |
| SC-09 | Same seed ⇒ identical `TickRecord`s and audit hashes. | determinism test |
| SC-10 | The full demo (A, C, D, audit proof) runs offline from a clean checkout after data preparation. | demo rehearsal, networking disabled |
| SC-11 | Model fit report passes its go/no-go gates (ARCHITECTURE.md §6.7) or documents why not. | `artifacts/fit_report.json` |
| SC-12 | Reported (not targeted): real-extreme false-rejection rate and detection delay, StageProof vs. baselines, on held-out episodes; attack families the system does not catch (stealth) are reported explicitly. | `scripts/evaluate.py` (P1) |

## 11. Known limitations

- Attacks, faults and volunteers are **simulated**; real adversaries may behave differently.
- A single reach and one model per reach; no claim of generalization.
- Rainfall comes from a coarse gridded product at prep time; local convective bursts may be under-represented.
- Floods without rain and without upstream signal (dam release, snowmelt, a fully compromised upstream chain) can end as UNCERTAIN or be caught only by the delayed downstream check.
- Verbatim replay detection only; a bounded stealth attacker that stays inside the model's tolerance is **not** detected and is reported as such.
- Fault vs. attack separation is heuristic; ambiguous cases are labeled "POSSIBLE" or UNCERTAIN by design.
- Not a replacement for official warning authorities.

## 12. External data (prep-time only)

River stage/discharge from the USGS water-data services and hourly precipitation from a public historical-weather API are downloaded once by `scripts/prepare_data.py`. Source endpoints may change; the script isolates this risk, and the cached CSV is committed or archived so the runtime never depends on them.
