# StageProof

**Verify the reading before you sound the alarm.**

StageProof — AI-Powered Cyber-Physical Integrity for Flood Early Warning Systems.
An integrity layer between river-level sensors and the decisions made from them: it
checks whether a gauge reading is authentic as a message, plausible as a sensor
output, and consistent with the rest of the river system before it can drive a warning.

> **Hackathon prototype.** All data is replayed or simulated. Not an operational
> warning system. Nothing is deployed, certified or guaranteed. See
> `docs/PRD.md` §11 and `docs/FINAL_VALIDATION.md` for limitations.

## The problem

A warning system can trust a gauge reading without checking that it makes physical
sense. That creates two opposite risks: a **false alarm** from a fabricated or faulty
high reading, and a **missed warning** from a suppressed low reading. Message
authentication alone does not close the gap — a compromised sensor produces correctly
signed lies. The river obeys physics, so upstream flow and rainfall constrain what the
gauge can plausibly read. StageProof uses that as a second line of defense.

## How it works

Every reading passes an ordered pipeline; each stage only sees what the previous one
accepted:

1. **Transport security** — each packet is HMAC-SHA256 signed by its station key.
   Unsigned, altered, replayed or stale packets are rejected, recorded as evidence,
   and never used as observations.
2. **Model expectation (AI/ML role)** — a RidgeCV regression, fitted offline on the
   reach's historical record, predicts what gauge B should read from upstream and
   tributary discharge (rating-converted) plus rainfall. The model supplies an
   expected value only; every decision is a transparent rule, not a black-box
   classifier.
3. **Evidence** — the reading is compared with the prediction (PHANTOM / SUPPRESSED /
   CONSISTENT), its sensor shape is checked (stuck, spike, dropout, noise) and replay
   and drift patterns are flagged.
4. **Verdict & persistence** — NORMAL, REAL_FLOOD, SENSOR_FAULT, POSSIBLE_CYBER_ATTACK
   or UNCERTAIN must persist for a configured number of consecutive ticks before it is
   committed, so verdicts do not flip-flop.
5. **Response** — suspect sensors are quarantined; a quarantined gauge is replaced by a
   clearly labeled **estimate** built from trusted inputs, with an uncertainty interval
   and confidence. Public warnings from unverified data are blocked; high-impact
   actions (releasing an attack quarantine, evacuation recommendations) are human-only.
6. **Audit integrity** — every consequential event is appended to a SHA-256
   hash-chained JSONL log. Any later edit, deletion or reordering breaks the chain and
   is detected; tamper demonstrations run on copies, never the live log.

Community verification (simulated volunteers replying to an SMS/IVR prompt) can help
resolve an UNCERTAIN window when telemetry is missing — one reply is never enough, and
community input can never override a hard failure.

## Scenarios

Six replayed scenarios are included (`data/scenarios/`); each manifest states its own
expected outcome and is machine-checked after a run:

| ID | What happens | Intended behavior |
|---|---|---|
| A | Real flood — everything agrees | REAL_FLOOD committed; warning provisional → confirmed; no fault/attack commit |
| B | Sensor fault — gauge B gets stuck | SENSOR_FAULT; B quarantined; estimate in use |
| C | Fabricated flood — valid key, no physical support | POSSIBLE_CYBER_ATTACK/FABRICATED; B quarantined; alert never above WATCH; no public warning |
| D | Suppressed flood — valid key, real flood hidden | POSSIBLE_CYBER_ATTACK/SUPPRESSION; B quarantined; provisional warning from the ESTIMATE |
| E | Uncertain — upstream telemetry lost during a rise | UNCERTAIN; community verification; no automatic warning from a low-confidence estimate |
| F | Unsigned & replayed packets — transport attack | Packets rejected; security response; B quarantined; no public warning |

Current acceptance status per scenario (including known gaps in A and D) is recorded in
`docs/FINAL_VALIDATION.md` — this project does not claim all six scenarios meet every
one of their acceptance checks.

## Quick start

```bash
pip install -e .[data,dev]
make data        # one-time download + alignment (the only network-using step)
make fit         # rating curves + Ridge integrity model -> artifacts/model.json
make scenarios   # write scenario manifests A-F
make test
make demo        # streamlit dashboard
```

No `make` on your machine? Run the underlying commands directly (see `Makefile`).

## Running a scenario (CLI)

```bash
python scripts/run_scenario.py --scenario C
python scripts/run_scenario.py --scenario C --explain 250    # per-tick reasoning
python scripts/run_scenario.py --scenario F --export out/    # per-tick JSONL dump
```

The CLI replays the scenario through the real engine, prints each committed verdict /
alert transition / action, verifies the audit chain (including a tamper check on a
copy), then evaluates the manifest's expectations and prints PASS/FAIL per check. Exit
code is 0 only when every expectation passes.

## Running the dashboard

```bash
python -m streamlit run dashboard/app.py
```

(equivalent to `make demo`.) The dashboard drives the same engine as the CLI over a
replay of the selected scenario. Sidebar: scenario selector, play/pause, +1 tick, jump
to next event, run to end, speed, reveal-ground-truth and autopilot toggles. Three
tabs: **Live operations** (KPIs, chart, why-panel, sensor trust, action log, phone
simulator), **Incident & evidence** (tick-by-tick checklist), **Proof & audit**
(signed-packet inspector with tamper re-checks on copies, live chain verification,
audit-copy tamper demonstration, automatic scenario checks). Everything shown is
simulated and offline; ground truth stays behind an evaluation toggle that StageProof
itself never reads.

## Tests

```bash
python -m pytest -q
```

covers transport security, the model pipeline, evidence and decision rules,
persistence, quarantine isolation, estimator fallback, scenario golden runs,
determinism, no-future-leakage, community verification and the audit chain.

## Documentation

`docs/PRD.md` (scope) · `docs/RULES.md` (behavior) · `docs/ARCHITECTURE.md` (structure)
· `docs/DESIGN.md` (UI) · `docs/DEMO.md` (demo script) · `docs/TASKS.md` (plan) ·
`docs/FINAL_VALIDATION.md` (final test/run results) · `docs/MEMORY.md` (context).
