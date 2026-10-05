<div align="center">

# StageProof — AI-Powered Cyber-Physical Integrity for Flood Early Warning Systems

**Verify the reading before you sound the alarm.**

<img src="https://readme-typing-svg.demolab.com?font=Inter&weight=600&size=21&duration=3000&pause=900&color=2563EB&center=true&vCenter=true&width=900&lines=Verify+the+reading+before+you+sound+the+alarm.;Detect+false+alarms+before+they+become+warnings.;Protect+against+suppressed+flood+signals.;Evidence+first.+Decision+second.+Action+last." alt="StageProof animated tagline" />

<br/>

[![Python](https://img.shields.io/badge/Python-3.11%2B-2563EB?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-Dashboard-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Security](https://img.shields.io/badge/Security-HMAC--SHA256-2563EB)](#security-and-audit)
[![Audit](https://img.shields.io/badge/Audit-SHA--256%20Hash%20Chain-2563EB)](#security-and-audit)
[![Tests](https://img.shields.io/badge/Tests-259%20Passing-16A34A)](#validation)
[![Status](https://img.shields.io/badge/Status-Hackathon%20Prototype-F59E0B)](#limitations)

</div>

---

## 🎯 What is StageProof?

**StageProof — AI-Powered Cyber-Physical Integrity for Flood Early Warning Systems** is an integrity layer between river-level sensors and the decisions made from them.

Instead of blindly trusting an extreme gauge reading, StageProof asks:

> **Is the message authentic? Is the sensor behavior plausible? Does the reading agree with the rest of the river system?**

Only then can the reading influence a warning decision.

This creates a second line of defense against both:

- 🚨 **False alarms** — fabricated or faulty high readings.
- ⚠️ **Missed warnings** — suppressed or manipulated low readings.
- 🔐 **Transport attacks** — unsigned, altered, stale, or replayed packets.

> **Hackathon prototype:** all data is replayed or simulated. This is **not an operational warning system** and is not deployed, certified, or guaranteed.

---

## 🧠 The Core Idea

```text
                    ┌──────────────────┐
                    │ Rainfall / Context│
                    └────────┬─────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
   Upstream A          Tributary T           Target B
        │                    │               observed
        └────────────┬───────┘                    │
                     ▼                            │
              ┌──────────────┐                    │
              │ AI Expectation│◄───────────────────┘
              │ Ridge model   │
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │ Evidence     │
              │ + Physics    │
              │ + Security   │
              └──────┬───────┘
                     ▼
        ┌──────────────────────────────┐
        │ Decision + Persistence Layer │
        └──────────────┬───────────────┘
                       ▼
       NORMAL / REAL_FLOOD / SENSOR_FAULT
       POSSIBLE_CYBER_ATTACK / UNCERTAIN
                       │
                       ▼
              Response + Audit
```

**Key principle:**

> **A valid signature proves who sent a message — not whether the reading is true.**

A compromised sensor can send a correctly signed lie. StageProof therefore combines transport integrity with physical/contextual evidence.

---

# 🖼️ Dashboard Gallery

The repository includes seven captured dashboard views from the real Streamlit application.

## 1. Live Operations — Landing

![StageProof Dashboard — Landing](https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_landing.png)

**Direct image URL:**  
https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_landing.png

---

## 2. Scenario A — Real Flood

![StageProof — Scenario A Real Flood](https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_A_real_flood.png)

**Direct image URL:**  
https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_A_real_flood.png

Scenario A demonstrates a corroborated flood where upstream, tributary, rainfall and target behavior support the event.

---

## 3. Scenario C — Fabricated Flood / Cyber Attack

![StageProof — Fabricated Flood Attack](https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_C_fabricated_attack.png)

**Direct image URL:**  
https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_C_fabricated_attack.png

The target gauge reports an extreme flood, but trusted upstream/context evidence does not support it. The reading is quarantined and the system blocks escalation beyond the appropriate warning level.

---

## 4. Scenario C — Incident & Evidence

![StageProof — Incident and Evidence](https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_C_incident_evidence.png)

**Direct image URL:**  
https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_C_incident_evidence.png

The evidence view exposes the reasoning behind the verdict rather than hiding it inside an opaque classifier.

---

## 5. Scenario C — Proof & Audit

![StageProof — Proof and Audit](https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_C_proof_audit.png)

**Direct image URL:**  
https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_C_proof_audit.png

Signed packet inspection, replay/security evidence, and the tamper-evident audit chain are surfaced in the proof layer.

---

## 6. Scenario D — Suppressed Flood

![StageProof — Suppressed Flood](https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_D_suppressed_flood.png)

**Direct image URL:**  
https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_D_suppressed_flood.png

This is the opposite failure mode: a real flood is hidden by a manipulated target reading. StageProof quarantines the compromised gauge and uses a clearly labelled fallback **estimate** from trusted evidence.

---

## 7. Scenario F — Replay / Transport Security

![StageProof — Replay Security](https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_F_replay_security.png)

**Direct image URL:**  
https://raw.githubusercontent.com/aakashimportant15-max/StageProof/main/scr/dashboard_F_replay_security.png

Unsigned or replayed packets are rejected before they can become observations used by the decision pipeline.

---

# ⚙️ How It Works

Every reading passes through an ordered pipeline:

### 1. Transport Security

Each packet is authenticated using **HMAC-SHA256** and checked for:

- signature validity
- station binding
- timestamp freshness
- timestamp monotonicity
- sequence/replay violations

Rejected packets are preserved as security evidence and never become trusted observations.

### 2. AI/ML Model Expectation

A lightweight **Ridge regression** model is fitted offline on the river reach history.

It estimates the expected target behavior from trusted upstream/tributary flow and rainfall context.

The model provides an **expected value and residual evidence**. It does not directly decide the final verdict.

### 3. Evidence Layer

The observed reading is compared against expected behavior and classified into contextual evidence such as:

- `CONSISTENT`
- `PHANTOM`
- `SUPPRESSED`
- `AMBIGUOUS`
- `INSUFFICIENT`

Sensor-shape checks also detect:

- stuck readings
- spikes
- dropouts
- excessive noise
- drift
- range violations

### 4. Decision + Persistence

The engine can commit:

```text
NORMAL
REAL_FLOOD
SENSOR_FAULT
POSSIBLE_CYBER_ATTACK
UNCERTAIN
```

Persistence/hysteresis prevents noisy one-tick changes from becoming unstable operational decisions.

### 5. Response

When a sensor becomes untrusted:

```text
TRUSTED → SUSPECT → QUARANTINED → RECOVERING → TRUSTED
```

A quarantined target can be replaced by a clearly labelled **ESTIMATE** generated from trusted inputs, including an uncertainty interval and confidence.

High-impact actions remain human-controlled.

### 6. Audit Integrity

Consequential events are appended to a **SHA-256 hash-chained JSONL audit log**.

If an event is edited, deleted, or reordered, chain verification detects the break.

Tamper demonstrations operate on copies rather than mutating the live audit record.

---

# 🧪 Six Security & Integrity Scenarios

| ID | Scenario | Intended behavior |
|---|---|---|
| **A** | Real flood — everything agrees | `REAL_FLOOD`; warning progresses toward confirmation |
| **B** | Sensor fault — gauge B gets stuck | `SENSOR_FAULT`; B quarantined; estimate in use |
| **C** | Fabricated flood — valid key, no physical support | `POSSIBLE_CYBER_ATTACK / FABRICATED`; B quarantined; no public warning |
| **D** | Suppressed flood — valid key, real flood hidden | `POSSIBLE_CYBER_ATTACK / SUPPRESSION`; B quarantined; estimate-based warning |
| **E** | Uncertain — upstream telemetry lost during a rise | `UNCERTAIN`; community verification; no low-confidence automatic warning |
| **F** | Unsigned & replayed packets | Packets rejected; security response; B quarantined |

Scenario manifests live in:

```text
data/scenarios/
```

and are machine-checked after replay.

> The final validation report documents known acceptance/timing gaps; StageProof does **not** claim that every scenario passes every acceptance check.

---

# 🔐 Security & Audit

StageProof intentionally separates **message authenticity** from **physical truth**.

### Transport

```text
Sensor packet
     │
     ├── HMAC valid? ────────► continue
     ├── station valid? ─────► continue
     ├── timestamp valid? ───► continue
     └── sequence valid? ────► continue
```

A failure produces security evidence and prevents the packet from entering the trusted observation path.

### Audit

```text
Event N-1 ──hash──► Event N ──hash──► Event N+1
                         │
                         └── tampering breaks verification
```

The audit verifier reports the first broken chain position.

---

# 🖥️ Dashboard

Run the Streamlit dashboard with:

```bash
python -m streamlit run dashboard/app.py
```

The dashboard uses the same underlying engine as the CLI replay.

### Live Operations

- scenario selector
- play/pause
- tick stepping
- event jumping
- playback speed
- verdict and alert KPIs
- observed vs expected behavior
- sensor trust state
- action timeline
- simulated community/officer interaction

### Incident & Evidence

- incident selection
- verdict/subtype/confidence
- candidate → committed persistence
- evidence checklist
- observed vs expected
- residual/z-score evidence
- fallback estimate
- actions and audit references

### Proof & Audit

- signed packet inspector
- signature/replay evidence
- audit-chain verification
- tamper-a-copy demonstration
- scenario evaluation results

---

# 🚀 Quick Start

```bash
pip install -e .[data,dev]

make data
make fit
make scenarios
make test
make demo
```

`make data` is the one-time network-using preparation step. The demo itself is designed for offline replay.

### CLI replay

```bash
python scripts/run_scenario.py --scenario C
python scripts/run_scenario.py --scenario C --explain 250
python scripts/run_scenario.py --scenario F --export out/
```

The CLI replays the real engine, prints committed verdicts/actions, verifies the audit chain, performs a tamper check on a copy, and evaluates the scenario manifest.

### Tests

```bash
python -m pytest -q
```

---

# 📊 Validation

The final hardened repository reports:

- **259 tests passing**
- transport security coverage
- model/evidence pipeline coverage
- decision and persistence coverage
- quarantine isolation
- estimator fallback
- scenario golden runs
- determinism checks
- no-future-leakage checks
- community verification
- audit-chain integrity
- dashboard runtime validation

Detailed results and known limitations:

```text
docs/FINAL_VALIDATION.md
```

---

# 🗂️ Project Structure

```text
StageProof/
├── artifacts/       # fitted model + validation artifacts
├── config/          # thresholds, policy, messages, reach configuration
├── dashboard/       # Streamlit UI
├── data/            # prepared data + scenario manifests
├── docs/             # PRD, architecture, rules, design, demo, validation
├── scr/              # captured dashboard screenshots
├── scripts/          # data preparation, fitting, evaluation, replay
├── stageproof/       # core integrity engine
│   ├── security/     # HMAC transport + audit chain
│   └── sim/          # deterministic scenario simulation
├── tests/             # automated tests
├── Makefile
├── pyproject.toml
└── README.md
```

---

# 📚 Documentation

- [`docs/PRD.md`](docs/PRD.md) — product scope
- [`docs/RULES.md`](docs/RULES.md) — decision and safety rules
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system structure
- [`docs/DESIGN.md`](docs/DESIGN.md) — dashboard/UI design
- [`docs/DEMO.md`](docs/DEMO.md) — demo walkthrough
- [`docs/TASKS.md`](docs/TASKS.md) — implementation plan
- [`docs/FINAL_VALIDATION.md`](docs/FINAL_VALIDATION.md) — final validation and known gaps
- [`docs/MEMORY.md`](docs/MEMORY.md) — project context

---

# ⚠️ Limitations

StageProof is a **hackathon prototype**, not an operational flood-warning system.

It:

- does not automatically evacuate communities
- does not automatically release quarantined sensors
- does not claim perfect cyber attribution
- does not treat a valid signature as proof of truthful data
- does not use future data at runtime
- does not use an LLM in the safety-critical decision path
- does not claim that all scenario acceptance checks pass
- uses simulated/replayed data for demonstration

The system is designed to make its uncertainty and evidence visible rather than hide them behind a single confidence number.

---

<div align="center">

## StageProof

**Verify the reading before you sound the alarm.**

Built as a hackathon prototype for demonstrating cyber-physical integrity in flood early-warning workflows.

</div>
