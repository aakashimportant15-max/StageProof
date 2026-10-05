# StageProof — Final Validation Report

Date: 2026-10-05 (pre-submission). Environment: Windows, Python 3.13.7,
Streamlit 1.65.0. Method: repository inspection, full test suite, CLI scenario
runs (`scripts/run_scenario.py`) and headless dashboard validation
(Streamlit AppTest) against the real engine. No engine, model, scenario or
security code was modified for this report; measured deviations are recorded,
not papered over. Companion doc updates: `README.md`, `docs/DEMO.md`.

## 1. Test status

Command: `python -m pytest -q` → **exit code 0, 259 passed** (11.35 s).

Note: `pyproject.toml` sets `addopts = "-q"`, so that command is effectively
double-quiet and prints no summary line on success. To see the summary run:
`python -m pytest -o addopts="" -q` → `259 passed in 11.35s`.

## 2. Scenario status (CLI)

`python scripts/run_scenario.py --scenario <id>`. All run lengths include the
196-tick warm-up. Exit code is 0 only when every expectation passes **and** the
audit chain verifies.

| ID | Ticks | Audit events | Expectations | Exit | Headline |
|---|---|---|---|---|---|
| A | 964 | 76 | 5/8 | 1 | Real flood believed and warned; known deviations below |
| B | 237 | 37 | 6/7 | 1 | STUCK fault quarantined + maintenance ticket; baseline fires 3 ticks |
| C | 297 | 55 | 8/8 | 0 | Fabricated flood fully contained |
| D | 250 | 55 | 4/6 | 1 | Suppression contained, estimate-based warning; commit 1 tick late |
| E | 292 | 110 | 4/8 | 1 | Uncertainty handled; COMMUNITY-basis acceptance not met |
| F | 253 | 30 | 8/8 | 0 | Transport attack rejected, quarantined, audited |

### A — actual behavior and known limitation
- `REAL_FLOOD` first committed at **t441**; the validation window was
  [405..417] — a late-commit deviation (~24 ticks). The verdict holds through
  the flood.
- PROVISIONAL warning (basis OBSERVED) t441; CONFIRMED (DOWNSTREAM) t465;
  `SEND_PUBLIC_WARNING` present. Rolling-z baseline flags 82 ticks; threshold
  alarm 227 ticks.
- **Known limitation (not fixed):** after the peak, the downstream/suppression
  checks commit `POSSIBLE_CYBER_ATTACK` — DOWNSTREAM_MISMATCH at t455 and
  t646, SUPPRESSION at t496 — quarantining B and raising a security alert.
  65 ticks are flagged ATTACK while truth is REAL_FLOOD. This fails 3 of 8
  checks: the commit window, `excludes:QUARANTINE_SENSOR`,
  `excludes:RAISE_SECURITY_ALERT`.

### C — passes 8/8
All B packets pass transport; context PHANTOM; `POSSIBLE_CYBER_ATTACK` /
FABRICATED committed t212; B quarantined t199; alert never above WATCH; zero
public warnings; estimate in use; threshold baseline fires 13 ticks (the false
alarm a plain threshold would raise).

### D — actual behavior and persistence timing
- B quarantined t197 (SPIKE/NOISE); `POSSIBLE_CYBER_ATTACK` / SUPPRESSION
  committed **t229** — the validation window [203..228] is missed by exactly
  **1 tick** (persistence timing); B re-quarantined; PROVISIONAL warning with
  source ESTIMATE t237; `QUARANTINE_SENSOR` present; rolling-z flags 61 ticks.
- Fails 2 of 6 checks: the commit window above and
  `threshold_alarm:expected_false` (legacy baseline fires 1 tick).

### E — actual behavior and gaps
- `UNCERTAIN` t197 (verified); A/T quarantined through the outage, recovered by
  t213; WATCH t198; `REAL_FLOOD` t215 (window [200..214] missed by 1 tick).
- **Not met:** `PROVISIONAL_WARNING(COMMUNITY)` — in this build the PROVISIONAL
  transition happens at t215 via **OBSERVED**. Community rounds themselves
  work: ≥ 2 informative replies resolve a round (verified in the dashboard,
  round CONFIRMED with replies 1 and 1), and the community can never override
  a hard failure. The event tail also briefly quarantines B (t244/t283).

### B — headline deviation
`SENSOR_FAULT` / STUCK committed t211, B quarantined t208, no public warning,
maintenance ticket present — but the legacy threshold baseline fires on
3 ticks (`expected_false` fails; 6/7).

### F — passes 8/8
Altered/unsigned packet rejected (`SIG_INVALID`) and a replayed sequence
rejected (`SEQ_REPLAY`) — rejected packets are never used as observations;
`POSSIBLE_CYBER_ATTACK` committed t212 (and t220); B quarantined t212;
security-alert and evidence actions present; zero public warnings;
`TRANSPORT_FAILURE` events recorded in the audit.

## 3. Security validation

- **HMAC-SHA256 transport:** packets are signed per station with keys from
  `.env` (not committed) and verified before use. Altering stage, station or
  timestamp yields `SIG_INVALID` / `STATION_MISMATCH` / `TS_SKEW` — also
  demonstrated live in the Proof tab on a copy of the packet.
- **Replay / sequence:** duplicate or backward sequence numbers are rejected
  (`SEQ_REPLAY` in F); HARD failures are never used as observations; SOFT flags
  (e.g. gaps) are noted but never alone imply an attack.
- **Quarantine:** attack verdicts quarantine B and exclude it from decisions;
  release of an attack quarantine is Tier-2 — human-only, disabled until the
  clean condition holds. Acknowledge and confirm/clear paths are audited.
- **Audit chain:** SHA-256 hash-chained JSONL; every scenario run verifies
  (`[OK] audit chain verifies: N events`). Any edit, deletion or reordering is
  caught at the first affected event.
- **Tamper detection:** each CLI run tampers a **copy** (temp dir, deleted
  afterwards) → detected as `hash mismatch at event 0: content was modified
  after sealing`. In the dashboard Proof tab the copy-tamper was detected at
  event 27 of a 55-event log; the live log still verified and no tampered files
  were left behind. The live log is never modified by these demos.

## 4. Dashboard validation

- Startup: `python -m streamlit run dashboard/app.py` — exactly what
  `make demo` executes.
- Validated headlessly with Streamlit AppTest against the real engine:
  scenarios C (8/8), F (8/8), A (5/8), D (4/6) — identical expectation
  outcomes and failing-check ids to the CLI; audit verified after every run;
  no render exceptions; Start / restart, +1 tick, Jump to next event and
  Run to end all function. All disclaimers (SIMULATED DATA banner, OFFLINE
  chip, DEMO phone panel) render from the first screen.
- Proof tab verified: altered packet → `SIG_INVALID`; Verify chain → VERIFIED
  (55 events); Tamper a copy → BROKEN with reason; original log intact.
- Manual controls verified: E volunteer replies resolve the round on ≥ 2
  informative replies; officer confirm; F acknowledge-release mid-run adds
  3 audited events and the chain still verifies.
- Not validated here: pixel-level layout at 1440×900 (no browser in this
  environment) — do one manual visual pass before presenting.

## 5. Known limitations (flagged, not fixed)

- **A:** late first commit (t441 vs window [405..417]); post-peak tail commits
  a POSSIBLE_CYBER_ATTACK and quarantines B (t455/t496/t646; 65 ticks ATTACK
  vs truth REAL_FLOOD).
- **D:** SUPPRESSION commit 1 tick outside its window (t229 vs [203..228]);
  legacy threshold baseline fires 1 tick.
- **E:** PROVISIONAL by OBSERVED, not COMMUNITY; tail quarantine (t244/t283).
- **B:** legacy threshold baseline fires 3 ticks where the suite expects
  silence.
- The model/engine/security code was intentionally frozen for submission;
  these deviations are acceptance-check mismatches, not safety failures —
  warnings still escalate, quarantines are conservative and human-released.

## 6. Final demo commands (all verified on this machine)

```bash
pip install -e .[data,dev]
python -m pytest -q                                  # exit 0 (259 passed)
python scripts/run_scenario.py --scenario C          # exit 0 (8/8)
python scripts/run_scenario.py --scenario F          # exit 0 (8/8)
python scripts/run_scenario.py --scenario A          # exit 1 (5/8, documented)
python scripts/run_scenario.py --scenario D          # exit 1 (4/6, documented)
python scripts/run_scenario.py --scenario E          # exit 1 (4/8, documented)
python scripts/run_scenario.py --scenario B          # exit 1 (6/7, documented)
python scripts/run_scenario.py --scenario F --explain 24
python scripts/run_scenario.py --scenario D --export artifacts/exports
python -m streamlit run dashboard/app.py             # dashboard (= make demo)
```

This host has no `make`; run the underlying commands directly (the `Makefile`
documents each target). Every CLI run also performs the audit verify + tamper-
on-copy checks and reports `RESULT scenario <id>: PASS|FAIL (n/m, audit ok)`.
