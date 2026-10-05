# StageProof — DEMO.md

**Purpose:** the operational guide for presenting StageProof to judges: what to run, what to show, what should happen, what to say, and what *not* to claim.
**What this file is not:** a requirements, architecture or behavior document. It describes the demo using behavior already defined elsewhere and invents none.
**Defers to:** RULES.md (behavior and expected transitions), ARCHITECTURE.md (runtime structure, CLI/dashboard boundary), DESIGN.md (what the screens show), PRD.md (goals, MVP scope), TASKS.md (gates, acceptance, demo readiness). MEMORY.md is context only.
**Conflict rule:** if this file disagrees with a source document, the source document wins and this file must be corrected. Unresolved items are in §14.

> **Status of this file (updated 2026-10-05, pre-submission):** the implementation now exists and was exercised end-to-end via the CLI (`scripts/run_scenario.py`) and the dashboard. Commands, run lengths, on-screen labels and scenario outcomes below were verified against the running system; known deviations from the intended contracts are noted in §3, §5, §9 and detailed in `docs/FINAL_VALIDATION.md`. Where behavior and this file disagree, the running system and RULES.md win.

---

## 1. Demo goal and framing

**One-liner (opening):** *"StageProof verifies a flood-gauge reading before it is allowed to sound an alarm."* Tagline: **Verify the reading before you sound the alarm.**

The judges should leave understanding that StageProof is **not a flood predictor**. It is an integrity and decision-support layer that asks, in order:

1. Is the packet authentic?
2. Is the sensor's behavior physically plausible?
3. Does the rest of the river system (upstream, tributary, rainfall, downstream) support the reading?
4. If the target sensor cannot be trusted, can a level be estimated from trusted, independent inputs?
5. What warning decision is safe to make?
6. What evidence and audit trail prove why that decision happened?

**Central sentence to land:** *A signature proves who sent a message, not whether it is true.*

### Problem statement (≈20 seconds)
A warning system can trust a gauge reading without checking that it makes physical sense. That creates two opposite risks: a **false alarm** from a fabricated or faulty high reading, and a **missed warning** from a suppressed low reading — or from an anomaly detector that throws away a genuine record flood because it looks abnormal. Message authentication alone does not close the gap: a compromised sensor produces correctly signed lies. The river obeys physics, so upstream flow and rainfall constrain what the gauge can plausibly read. StageProof uses that as a second line of defense.

### What StageProof does (≈15 seconds)
It checks every reading for authenticity, sensor-shape plausibility, and agreement with the river system. It labels the result NORMAL, REAL FLOOD, SENSOR FAULT, POSSIBLE CYBER ATTACK or UNCERTAIN, quarantines a suspect sensor, substitutes a clearly labeled ESTIMATED level when needed, keeps humans in control of high-impact actions, and records consequential decisions in a tamper-evident log.

---

## 2. Mandatory disclaimers (say and show — never skip)

The dashboard shows these persistently (DESIGN §4.1, §11). The presenter must also say them aloud near the start and again before the warning scenarios.

| Disclaimer | Where it appears on screen |
|---|---|
| **SIMULATED DATA — NOT AN OPERATIONAL WARNING SYSTEM** | High-contrast header banner, never dismissible |
| **OFFLINE — no network used** | Header chip |
| Phone is simulated | Phone panel titled `Phone simulator — DEMO (nothing leaves this machine)`; messages are not prefixed |
| Tier 1 auto-approval is a demo setting | Action log `tier` column reads `1 · auto (demo policy)`; sidebar caption reads `Policy: tier-1 actions auto-approved in demo mode.` |

**Say aloud:** "Everything you will see is replayed or simulated. No real warning, SMS, or evacuation order is sent; actions are simulated. It runs offline. This is a hackathon prototype, not an operational or certified system."

---

## 3. Scenarios A–F (the full MVP set; no others exist)

Behavior below follows RULES §19 and ARCHITECTURE §9.3. Selector text is from DESIGN §10.

| ID | What is happening | StageProof should conclude | Sensor state | Alert state | Key action(s) | Judge takeaway |
|---|---|---|---|---|---|---|
| **A** `A · Real flood — everything agrees` | A genuine flood; upstream, rain, and target all agree | `REAL_FLOOD` committed and held — measured: first commit t441 (validation window expected t405–417: late-commit deviation). On the receding tail the downstream/suppression checks can briefly commit `POSSIBLE_CYBER_ATTACK` (DOWNSTREAM_MISMATCH t455/t646, SUPPRESSION t496) — **do not claim "no attack verdict anywhere"** | B TRUSTED through the rise; briefly QUARANTINED on the tail (see left) | PROVISIONAL (basis OBSERVED) t441; CONFIRMED (DOWNSTREAM) t465 — both verified | `SEND_PUBLIC_WARNING` (provisional → confirmed); rolling-z baseline flags 82 ticks (≥ 1 required) | An extreme reading is not automatically a fault — the flood is believed and warned on |
| **B** `B · Sensor fault — gauge B gets stuck` | B's output freezes while the river changes | `SENSOR_FAULT` / `STUCK` committed t211 (verified) | B QUARANTINED t208; recovery path after the fault ends | No public warning unless the estimate itself satisfies RULES §10.5 | `QUARANTINE_SENSOR`, `OPEN_MAINTENANCE_TICKET`, `USE_ESTIMATE`; legacy threshold baseline fires on 3 ticks (expected silent — known deviation) | A broken gauge is quarantined and replaced by a labeled estimate |
| **C** `C · Fabricated flood — valid key, no physical support` | B (compromised key) reports a flood in a dry period; upstream flat, no rain | All packets pass transport; context `PHANTOM`; **`POSSIBLE_CYBER_ATTACK` / `FABRICATED`** committed within `fault_attack_ticks` of the context becoming implausible | B QUARANTINED | **Never above WATCH; zero public warnings** | `QUARANTINE_SENSOR`, `RAISE_SECURITY_ALERT`, `PRESERVE_EVIDENCE`, `NOTIFY_OFFICER`, `USE_ESTIMATE`; estimate stays below `action_stage`; threshold baseline fires | Valid signature ≠ true reading; false alarm avoided |
| **D** `D · Suppressed flood — valid key, real flood hidden` | A real flood; compromised B reports a level kept below `action_stage` | Context `SUPPRESSED`; `POSSIBLE_CYBER_ATTACK` / `SUPPRESSION` committed t229 — the validation window [203..228] is missed by 1 tick (known persistence-timing deviation). First quarantine t197 (SPIKE/NOISE), re-quarantined t229 | B QUARANTINED; estimate in use | **PROVISIONAL, source ESTIMATE** t237 (verified) | `SEND_PUBLIC_WARNING` with basis ESTIMATE; legacy threshold baseline fires on 1 tick (expected silent — known deviation) | Protects against missed warnings, not only false alarms |
| **E** `E · Uncertain — upstream telemetry lost during a rise` | Feeds A and T go down during a real rise | `UNCERTAIN` committed t197 (verified); then `REAL_FLOOD` committed t215 — the validation window [200..214] is missed by 1 tick (known deviation). Later tail commits `POSSIBLE_CYBER_ATTACK` (DOWNSTREAM_MISMATCH t244, SUPPRESSION t283) | A/T: SUSPECT → QUARANTINED → RECOVERING → TRUSTED (t213). B: SUSPECT during the outage, quarantined on the tail (t244) — see note below | WATCH t198; PROVISIONAL t215 with basis **OBSERVED** — the intended COMMUNITY-basis transition is **not met in this build** (community rounds still resolve on ≥ 2 replies — verified) | `NOTIFY_OFFICER`, `REQUEST_VERIFICATION`, `SEND_PUBLIC_WARNING` (via OBSERVED, not COMMUNITY); no automatic public warning from the rain-only estimate (LOW confidence) | Uncertainty is first-class; community input can resolve a round but never overrides hard failures |
| **F** `F · Unsigned & replayed packets — transport attack` | Outsider injects an altered/unsigned packet, then re-sends an earlier sequence | `SIG_INVALID` / `SEQ_REPLAY` packets rejected (not used as observations); **`POSSIBLE_CYBER_ATTACK` / `TRANSPORT`** committed at 1 tick | B QUARANTINED; estimate in use | Not specified by §19 beyond the above | `QUARANTINE_SENSOR`, `RAISE_SECURITY_ALERT`, `PRESERVE_EVIDENCE`, `NOTIFY_OFFICER`, `USE_ESTIMATE`; `TRANSPORT_FAILURE` audit events; audit verifies OK; tampered copy fails | Transport attacks are caught immediately and provably |

> **Scenario E note:** RULES §19 does not state B's sensor state for E. Measured: B becomes SUSPECT during the outage and is quarantined on the event tail — the presenter must **read B's chip on screen** and describe only what it shows.
> **Scenario C note:** the contract is "alert never exceeds WATCH". Whether the display shows NO ALERT or WATCH is whatever the engine produced. Do not claim WATCH specifically.
> **Acceptance status from the CLI (measured 2026-10-05):** C 8/8 and F 8/8 pass (exit 0). A 5/8, B 6/7, D 4/6, E 4/8 exit non-zero with exactly the deviations noted above. Never present a scenario as "passing its contract" when it did not; full numbers in `docs/FINAL_VALIDATION.md`.

---

## 4. Time-budgeted demos

All paths use only buttons and selectors in the dashboard (DESIGN §2: "The presenter never needs to type"). Speed control offers 2 / 8 / 24 ticks per second (default 8); "Jump to next event" advances to the next verdict change or alert transition. Measured run lengths (incl. the 196-tick warm-up): **A 964 · B 237 · C 297 · D 250 · E 292 · F 253 ticks** — at 8 ticks/s: C ≈ 37 s, D ≈ 31 s, F ≈ 32 s, A ≈ 2 min; use 24 ticks/s or "Jump to next event" when time is short.

### A. Elevator explanation (30–60 s — no live scenario)
Say: problem statement (§1, trimmed) + the central sentence + "It handles both false alarms and missed warnings, keeps humans in control, and every decision is audited." Optionally show the Scenario C Verdict card, if already loaded. **Skipped:** everything else.

### B. Core demo (~3 min)
| Step | Time | Show |
|---|---|---|
| 1 | 0:00–0:25 | Disclaimers (§2) + one-liner |
| 2 | 0:25–1:25 | **Scenario C** on Live Operations, ending on the Proof "valid signature ≠ true" card (§8 step 2) |
| 3 | 1:25–2:25 | **Scenario D** on Live Operations |
| 4 | 2:25–3:00 | Proof: Verify chain → Tamper a copy (§8 steps 5–6) |
| — | skipped | A, B, E, F (offered as Q&A backups) |

### C. Full judge demo (~5 min) — the recommended primary path
| Step | Time | Show |
|---|---|---|
| 1 | 0:00–0:30 | Disclaimers, one-liner, problem statement |
| 2 | 0:30–1:45 | **C** — false-alarm defense; Incident/Evidence checklist; Proof "valid ≠ true" |
| 3 | 1:45–2:45 | **D** — missed-warning defense |
| 4 | 2:45–3:30 | **A** — real flood not rejected; baseline comparison strip |
| 5 | 3:30–4:30 | Proof tab: packet inspector alterations, audit verify, **tamper a copy** |
| 6 | 4:30–5:00 | "5 things to remember" (§10) + limitations (§11) |
| — | skipped | B, E, F (backup / Q&A) |

This matches PRD SC-10 (the demo covers A, C, D and the audit proof). **Time budgets above are targets set here; T060 requires three timed runs to complete within the budget, and DEMO.md is the owner of that budget.** Record actual times in MEMORY.md at T060.

### D. Backup / deep dive (as time allows)
Add **E** (community verification, phone simulator, English/Hindi, replies 1 and 1), **F** (transport attack, Proof sections A and C), **B** (stuck sensor, maintenance ticket), the Incident/Evidence screen "What would change this verdict?" footnote for UNCERTAIN, the Evaluation section G if generated (P1), and the baseline contrast on every scenario. Use the cached path (§12) if live execution is unstable.

---

## 5. Primary "wow" flow — presenter script

Use **Live Operations** unless stated. Watch the KPI row (Verdict · Alert · Target sensor B · Observed vs expected · Best level) and the four-panel chart. Chart elements and chips come from DESIGN §5–6; the dashboard only displays engine output.

### Part 1 — False-alarm defense: Scenario C (~75 s)

**Setup:** sidebar → select `C · Fabricated flood — valid key, no physical support` → wait for the warm-up spinner (`Warming up N ticks…`) → Play (default 8 ticks/s). Leave **Reveal ground truth** OFF (turn it on only after the verdict, if you want to show the injected interval, labeled "GROUND TRUTH (not visible to StageProof)").

| Presenter shows | Presenter says |
|---|---|
| Panel ③: B's observed line rising toward the action line while upstream (②) is flat and rainfall (①) is empty | "Gauge B suddenly looks like a flood. A simple threshold alarm would fire here." Point at the **Baselines strip**: `Threshold alarm: FIRING` |
| **Why panel** | "The message signature is valid. But the reading is much higher than upstream gauges and rainfall can explain." (reason codes: `CONTEXT_PHANTOM`, `UPSTREAM_FLAT`, `RAIN_NO`) |
| **Verdict KPI** | "StageProof says: **POSSIBLE** cyber attack, subtype fabricated — and note it only says *possible*." Point at `candidate … (n / N)` if still pending, then the committed label |
| **Sensors panel** | "B is now quarantined and excluded from decisions. It stays visible." |
| **Alert KPI** + Action timeline | "No public warning was sent. Security alert raised, evidence preserved, officer notified, estimate in use." |
| **Best level** | "This is an **ESTIMATED** level built from trusted inputs only — it stays below the action stage." |

**Expected (RULES §19 / AC-C):** all B packets pass transport (no HARD flags); context PHANTOM; `POSSIBLE_CYBER_ATTACK` / `FABRICATED` committed; B QUARANTINED; actions `QUARANTINE_SENSOR`, `RAISE_SECURITY_ALERT`, `PRESERVE_EVIDENCE`, `NOTIFY_OFFICER`, `USE_ESTIMATE`; alert never above WATCH; **no `SEND_PUBLIC_WARNING` in the run**; threshold baseline fires at least once.
**DESIGN target:** the verdict, chart, and "no public warning" line are visible together without scrolling at 1440×900 (T053 acceptance).

**Then go to Incident / Evidence** (≈10 s): the checklist should show **✓ Message integrity** and **✗ Matches upstream + rain? (PHANTOM)** (T055 acceptance).
**Then go to Proof, section B** and say the key line (§8 step 2): *"The signature proves who sent the message. It doesn't prove the river is actually at that level."*

**Why it matters:** a compromised key cannot make StageProof panic the public.

### Part 2 — Missed-warning defense: Scenario D (~60 s)

**Setup:** select `D · Suppressed flood — valid key, real flood hidden` → Play.

| Presenter shows | Presenter says |
|---|---|
| Panel ③: the observed B line stays below the action line while truth is above it; the Baselines strip shows `Threshold alarm` silent through the suppressed rise | "This is the opposite attack. A real flood is happening, but a compromised gauge is hiding it. A threshold alarm stays silent through this window." *(Measured caveat: in the full run the legacy baseline fires on 1 tick — do not claim "never fires".)* |
| Verdict KPI | "StageProof sees the reading is **much lower than upstream and rainfall imply**: possible cyber attack, subtype suppression." |
| Sensors panel; panel ③ hatched quarantine strip; dotted estimate line tagged `ESTIMATED — B quarantined` | "B is quarantined. We switch to a transparent **ESTIMATED** level from trusted upstream, tributary and rain inputs, with an uncertainty interval — never presented as a measurement." |
| **Alert KPI** (`PROVISIONAL WARNING`, `BASIS: ESTIMATE`) + Action timeline: `SEND_PUBLIC_WARNING · PROVISIONAL · basis ESTIMATE · T1 AUTO-APPROVED (demo)` | "The warning is issued from the estimate, and its basis is stated. The auto-approval is a demo setting; in real use a human officer would tap approve." |

**Expected (RULES §19 / AC-D):** `POSSIBLE_CYBER_ATTACK` / `SUPPRESSION` committed; B QUARANTINED; PROVISIONAL with source ESTIMATE; a `SEND_PUBLIC_WARNING` with basis ESTIMATE exists; no `REAL_FLOOD` committed from B once suppression is in effect.
**Do not claim** the estimate is always available or always warning-capable: it warns only if confidence is HIGH or MED and `stage_lo ≥ action_stage` for `est_warn_ticks` consecutive ticks (RULES §10.5). LOW confidence → WATCH only, no automatic public warning.

**Why it matters:** the system defends against missed warnings, not just false alarms.

### Part 3 — Real flood not rejected: Scenario A (~50 s)

**Setup:** select `A · Real flood — everything agrees` → Play.

| Presenter shows | Presenter says |
|---|---|
| Panel ①②③ rising together; Baselines strip `Single-sensor anomaly detector: FLAGGED` | "Here is a genuine flood. A generic anomaly detector flags it as abnormal — exactly when you least want to discard it." |
| Verdict KPI: `REAL FLOOD` | "StageProof commits **REAL FLOOD** because upstream and rain support the reading — the warning goes provisional, then confirmed once the downstream gauge responds." |
| Alert KPI progressing; downstream chart ④ | "The warning escalates to provisional, then confirmed once the downstream gauge responds." |

**Expected (RULES §19 / AC-A):** REAL_FLOOD committed (measured t441 — later than the validation window); PROVISIONAL (OBSERVED) t441; CONFIRMED (DOWNSTREAM) t465; rolling-z baseline flags 82 ticks. **Measured deviations — do not hide if they appear live:** the first `REAL_FLOOD` commit lands ~20+ ticks after the expected window [405..417], and after the peak the downstream/suppression checks briefly commit `POSSIBLE_CYBER_ATTACK` (DOWNSTREAM_MISMATCH t455/t646, SUPPRESSION t496) and quarantine B; 65 ticks are flagged ATTACK while truth is REAL_FLOOD. State the tail behavior as a documented limitation (`docs/FINAL_VALIDATION.md`), not a demo glitch.
**Why it matters:** "An extreme reading is not automatically a fault." A rise rate alone never rejects a reading (N-10).

### Part 4 — Proof / audit (~60 s)
See §8 for the exact order.

---

## 6. Presenter language

### Use
"possible cyber attack", "physically inconsistent", "valid signature, unsupported reading", "ESTIMATED", "quarantined", "simulated", "decision support", "a human decides evacuation".

### Never say or imply
| Avoid | Why |
|---|---|
| "100% accurate", "unhackable", "first ever", "production-ready", "operational", "certified" | Unsupported (PRD: nothing deployed, validated or certified; no accuracy promised) |
| "attack detected" / "we caught the hacker" | Cyber verdicts are always **POSSIBLE** (N-9) |
| "it predicts floods" | Out of scope; it is an integrity layer |
| "the estimate is the real level" | Always **ESTIMATED**, with an interval (N-7) |
| "AI decides" / "the model classifies it" | Decisions are transparent rules; the Ridge model supplies an expected value only |
| "it warns the public" / "it evacuates" | Actions are simulated; evacuation is never automatic and is human-only (N-6) |
| "community says it's real, so it's confirmed" | One reply never resolves a round; community never overrides a hard failure or releases a quarantine (N-12) |
| "it catches every attack" | Bounded stealth attacks and non-verbatim replays are documented limits (§11) |

---

## 7. Evidence screen (supporting step in C; use for questions)

For the Scenario C attack incident the checklist should show:

- Message integrity **✓** (signature, station binding, sequence, timestamp all passed)
- Sensor health **✓** shape plausible
- Matches upstream + rain? **✗ PHANTOM**
- Upstream trend: flat · Rainfall: no · Downstream: pending/unknown depending on elapsed time
- Observed vs expected numbers and deviation, plus the fallback estimate card (`Estimate (not measured): … · confidence … · IN USE since tick …`)

Say: "Every line is a plain-language reason. Nothing here needs ML knowledge." Do not read out raw statistics; the screen shows "deviation from expected" rather than a z-score (DESIGN §1).

---

## 8. Proof screen — exact order

Section letters below are **DESIGN §8.2 letters**. The demo order, requested for clarity, is a rearrangement for the narrative (see §14 note N1).

| Demo step | DESIGN section | Presenter action | Expected on screen | Say |
|---|---|---|---|---|
| 1 | **A. Signed packet inspector** | Choose a channel and tick (use B during C). Point at station, timestamp, sequence, stage, unit, the canonical string, and the truncated signature | Transport result `✔ passed` | "This is the signed packet. The signature is an HMAC over these fields." |
| 2 | **A (buttons)** then **B. "Valid signature ≠ true reading"** | Click **Alter stage**, **Change station**, **Shift timestamp** (stateless verification on a copy); then the side-by-side card | `✖ SIG_INVALID` / `STATION_MISMATCH` / `TS_SKEW` on tampering. Card: left `Signature: ✔ valid · sequence ✔ · timestamp ✔`; right `Physical consistency: ✖ PHANTOM — upstream flat, no rain` | "If someone **changes** the message, the signature breaks. But here the message is untouched and signed — yet physically wrong. **A signature proves who sent a message, not whether it is true.**" |
| 3 | **C. Sequence and replay evidence** | Show the `seq` vs tick plot (most useful with Scenario F loaded) | Duplicate/backward sequence numbers highlighted; HARD/SOFT flags listed | "A reused sequence number is a replay. Soft gaps are noted but never alone imply an attack." |
| 4 | **D. Audit log** | Show the table: `# · time · kind · summary · hash · prev` | Rows in order; select a `VERDICT_CHANGE` row to show its evidence snapshot | "Every consequential event is logged, each hash chained to the previous one." |
| 5 | **D (button)** Verify chain | Press **Verify chain** | `✔ VERIFIED (n events)`; header chip `✔ VERIFIED · n events` | "Untouched log: verified." |
| 6 | **E. Tamper demonstration** | Pick an event and a field (or delete the event); press **Tamper a copy** | Copy: `✖ BROKEN at #k (reason)`. Live log: still `✔ VERIFIED` | "Any edit, deletion or reordering is caught at the first affected event. I'm editing a **copy**; the live log is never touched." |
| 7 | **F. Scenario classification** | Show the A–F table (`expected` vs `observed` committed label, alert outcome, sensor outcome, result) | Each row ✔ / ✖ / not run | "Each scenario is checked against its stated contract." Show only rows that have actually been run |
| 8 | **G. Evaluation (P1)** | Only if generated | Figures, with a list of adversary families **not** caught | Show only if present; otherwise the empty-state message appears — do not apologise for it |

**Never** tamper with the live audit log. The Proof screen's demo works on a copy by design (ARCHITECTURE §2.4, RULES §15.5).

---

## 9. Supporting demonstrations (backup / Q&A)

### Scenario E — Uncertain / upstream outage
Select E → Play. Watch: verdict `UNCERTAIN` (reason `INSUFFICIENT_INPUTS`, t197); alert `WATCH` (t198); sensors A and T lose their feeds (SUSPECT → QUARANTINED, recovering to TRUSTED by t213); phone simulator shows the volunteer prompt in English/हिन्दी; with **Autopilot operators** ON, scripted replies 1 and 1 arrive and the community round resolves CONFIRMED on ≥ 2 informative replies (verified in the dashboard; press the reply buttons manually if Autopilot is OFF); the verdict becomes `REAL FLOOD` at t215 and the warning goes `PROVISIONAL`.
**Measured deviation:** in this build the PROVISIONAL transition is based on **OBSERVED** (t215), not COMMUNITY — the intended COMMUNITY-basis acceptance is not met; and after the peak the tail can briefly quarantine B again (DOWNSTREAM_MISMATCH t244, SUPPRESSION t283). Tell it straight if it appears; see `docs/FINAL_VALIDATION.md`.
Say: "With the upstream gauges lost, StageProof can't verify the reading. It holds at WATCH, tells the officer, and asks local volunteers. **One reply is never enough** — it takes at least two informative replies. And community replies can never override a hard failure or release a quarantine."

### Scenario F — Unsigned & replayed packets
Select F → Play. Watch: rejected packets as ✖ markers `rejected: SIG_INVALID`; verdict `POSSIBLE CYBER ATTACK · TRANSPORT` at 1 tick; B quarantined; estimate in use. Then Proof sections A, C, D, E.
Say: "No key, no trust. Rejected packets are never used as observations, but the failure is recorded as evidence."

### Scenario B — Sensor fault
Select B → Play. Watch: `SENSOR FAULT · STUCK` (t211); B quarantined (t208); maintenance ticket in the Action timeline; estimate in use; no public warning. *(Measured caveat: the legacy threshold baseline fires on 3 ticks here — don't claim it stays silent.)*
Say: "A frozen gauge is a fault, not an attack. The response is maintenance and a labeled estimate."

### Officer controls (Tier 2 demonstration)
When a sensor is quarantined for ATTACK, **Acknowledge & release (Tier 2)** is disabled until the clean condition holds, then requires a human click. Say: "Releasing a possible-attack quarantine and any evacuation recommendation are human-only. The system never evacuates automatically." (No evacuation recommendation appears unless `evac_stage` is configured — RULES §11. Do not promise one.)

---

## 10. Five things judges should remember

1. **A valid signature does not prove physical truth.** Authenticity and plausibility are checked separately.
2. **It does not trust a single gauge.** Upstream, tributary, rainfall and downstream all vote on whether the reading is possible.
3. **It guards against both false alarms and missed warnings** (Scenarios C and D), and does not discard a genuine extreme flood (Scenario A).
4. **A quarantined sensor is replaced by a transparent ESTIMATED level** with an interval and stated basis — never presented as a measurement.
5. **Every consequential decision is audited** in a tamper-evident log, and humans keep control of high-impact actions.

---

## 11. What not to claim — and the limitations to volunteer

State honestly if asked (PRD §11, ARCHITECTURE §5.5):
- All data, attacks, faults and volunteers are **simulated**; real adversaries may behave differently.
- One reach and one model per reach; no generalization claim. Rainfall is a coarse gridded product.
- **Measured tail deviation (A, D, E):** after a genuine flood peaks, the downstream/suppression checks can briefly commit `POSSIBLE_CYBER_ATTACK` and quarantine B (A: DOWNSTREAM_MISMATCH t455/t646, SUPPRESSION t496; E: t244/t283). The first `REAL_FLOOD` commit in A also lands later than the validation window (t441 vs [405..417]). Both are known acceptance deviations of the current fit, not demo glitches — see `docs/FINAL_VALIDATION.md`.
- **Legacy baselines are not perfect:** in the measured runs the threshold alarm fires on 3 ticks in B and 1 tick in D even though the suite expects silence there. They are shown for comparison only.
- **Replay detection is verbatim only** (and is P1 — check whether it exists before mentioning it). A **bounded stealth attacker** who stays inside the model's tolerance is **not detected**.
- A fully compromised upstream chain, or a flood with no rain and no upstream signal (dam release, snowmelt), may end UNCERTAIN or be caught only by the delayed downstream check.
- Fault-vs-attack separation is heuristic; ambiguous cases are labeled POSSIBLE or UNCERTAIN by design.
- It is decision support, not a replacement for official warning authorities.
- No accuracy figure is claimed. If asked for numbers, only quote what `artifacts/fit_report.json` or the evaluation outputs actually contain, with their limitations.

---

## 12. Commands

Only commands established by TASKS.md / ARCHITECTURE.md appear. Anything else is deliberately unspecified.

| Purpose | Command | Source |
|---|---|---|
| Install | `pip install -e .[data,dev]` | verified — pyproject extras; Makefile `setup` runs exactly this |
| One-time setup / data / fit / manifests / tests | `make setup`, `make data`, `make fit`, `make scenarios`, `make test` | TASKS T002 (Makefile targets `setup data fit scenarios test demo eval cache clean`); README quick start (T058) |
| Run a scenario headless | `python scripts/run_scenario.py --scenario A` — ids are single letters `A`–`F`; options `--scenario`, `--explain TICK`, `--export DIR`, `--no-autopilot` | verified; exit 0 only when every expectation passes **and** the audit verifies |
| Explain a tick | `python scripts/run_scenario.py --scenario C --explain 24` | verified |
| Export a replay cache | `python scripts/run_scenario.py --scenario C --export artifacts/exports` → writes `artifacts/exports/scenario_C_records.jsonl` (one JSON record per tick); Makefile target `make cache` runs A, C, D, E, F | verified |
| Launch the dashboard | `python -m streamlit run dashboard/app.py` — this is exactly what `make demo` executes | verified (Makefile `demo`) |
| Generate evaluation figures (P1) | `make eval` | DESIGN §11 |
| Prepare data (the only network step) | `make data` / `scripts/prepare_data.py` | ARCHITECTURE §2.4, §10 |

**Rule for the live presentation:** the presenter should not type commands during the judge demo. Every live control is a dashboard button or selector. Terminal commands belong to pre-demo preparation (§13).

---

## 13. Fail-safe behavior

Do not change rules, thresholds, config, or code during a presentation. Do not weaken a verdict to make a scenario "pass". Prefer the known-good fallbacks below, which exist only if the corresponding task was completed.

| Situation | What the presenter does | Supported by |
|---|---|---|
| **Model artifacts missing** | The dashboard shows a blocking message `Model not found — run make fit` (and `make data` / `make scenarios` as applicable). This is a preparation failure — do not present live. Switch to the backup recording or cached playback. | DESIGN §11; RULES §17 (refuse to start); TASKS T061 |
| **A scenario fails** (CLI exits non-zero, or on-screen result is ✖) | Do not present that scenario as passing. Use the Scenario classification table honestly, skip to another scenario, or play its cached run. State plainly that it did not meet its contract. Fix the engine or scenario construction **after** the demo — never relax RULES. | TASKS T042 |
| **Dashboard component fails** | An error card appears for that component only; the app and the engine keep running. Continue on other tabs. If the whole app fails: run the CLI timeline for the scenario and narrate from its printed output, or play the backup video. | ARCHITECTURE §14; TASKS T041, T061 |
| **Audit verification fails unexpectedly** | Do not "repair" the log. An unexpected `BROKEN` on the live log is a **real finding**, not a demo glitch. Say so, press **Start / restart run** (rebuilds the run with the same seed and a new audit file) and re-run, or fall back to cached playback and the backup recording. | RULES §15; TASKS T052 (restart reproduces the run) |
| **`HALTED_AUDIT` banner** (audit write failed) | Actions are paused by design. Explain it as the safety behavior it is ("no action runs unless it is audited first"). Then Reset or switch to cache. | RULES §17; DESIGN §11 |
| **Cached playback unavailable** | Cached playback is **P1 (T057)** and may not exist. Fallback order: live run → CLI timeline narration → backup screen recording (T061). Do not claim cached mode if the header chip does not read `CACHED PLAYBACK`. | TASKS T057, T061 |
| **Cached playback in use** | The header shows `CACHED PLAYBACK`; operator controls (approve, reject, release, volunteer replies) are disabled with an explanation chip. Say so: "this is a recorded run of the engine's output". | DESIGN §11, TASKS T057 |
| **Live demo becomes unstable** | Pause, press **Start / restart run** (deterministic: same seed, same run), and re-run once. If it is still unstable, move to the fallback order above. | NFR-01; T052 |
| **Network unexpectedly required** | It should not be. The demo must run with networking disabled. If something tries to reach the network, stop and use the backup; do not enable networking to patch it live. | NFR-07; SC-10 |

---

## 14. Demo open questions / conflicts

None of the source documents contradict this demo flow. These items need attention:

| # | Item | Action |
|---|---|---|
| **N1** | **Proof-screen lettering.** Your requested demo order lists the sections as A signed packet → B signature verification → C physical consistency → D sequence/replay → E audit verify → F tamper → G evaluation. DESIGN §8.2 uses A inspector (including the verification buttons) → B "Valid signature ≠ true" (physical consistency) → C sequence/replay → D audit log (with Verify) → E tamper → F scenario classification → G evaluation. §8 above keeps DESIGN's letters as the authority and maps the demo steps onto them. | None required; DESIGN is authoritative. |
| **N2** | **Dashboard launch command.** TASKS names the `make demo` target but not what it executes. | **Resolved (verified):** `make demo` runs `python -m streamlit run dashboard/app.py`. §12 updated. |
| **N3** | **Demo time budget is owned here.** TASKS T060 requires completing the DEMO.md time budget three times in a row but no document specifies the budget. The 3-min and 5-min budgets in §4 are targets set in this file; actual per-scenario run lengths are unknown. | **Run lengths measured (2026-10-05):** A 964 · B 237 · C 297 · D 250 · E 292 · F 253 ticks (incl. 196-tick warm-up); §4 updated. Rehearsals (T060) still required for wall-clock timing. |
| **N4** | **Cached playback is P1.** TASKS cut order drops T057 first, yet T061 exports caches (if T057 is done) and T060 rehearsals depend on the fallback. | If T057 is cut, rely on the backup recording and CLI timeline (§13). |
| **N5** | **Inherited from MEMORY.md Q1:** the persistence mapping for `TS_SKEW` / `TS_NONMONOTONIC` is ambiguous. Not used in the A/C/D flow, but it affects how quickly Scenario F-style failures with those flags commit. | Resolve in RULES before T023. Do not demo timestamp-only attacks until resolved. |
| **N6** | **Inherited from MEMORY.md Q2:** the Why-panel lines for "signature valid" and the closing "Result: …" sentence (DESIGN §6.4) have no reason-code/template keys defined in RULES Appendix A or DESIGN §9. §5 quotes DESIGN's example lines; the exact wording on screen may differ. | Resolve before T003 finalizes `messages.yaml`; then align §5. |
| **N7** | **Scenario E sensor state / Scenario C alert display** are not specified beyond the contracts quoted in §3. | Describe only what the screen shows. |

---

## 15. Pre-demo checklist

Run this before every rehearsal and before the real presentation. Commands are only those in §12; anything marked is unconfirmed.

**Environment and artifacts**
- [ ] Clean checkout of the tagged commit (T062); `pip install -e .[data,dev]` succeeds.
- [ ] `data/reach.csv`, `artifacts/model.json`, `artifacts/fit_report.json`, `data/scenarios/` manifests A–F all present; fit gates passed or the relaxation decision is recorded in MEMORY.md.
- [ ] `.env` present locally with the demo-only keys; **no real secrets, no keys visible on screen**; `.env` not committed.
- [ ] `make test` passes (including security, scenario golden, no-future-leakage, quarantine-isolation, determinism and layering tests).

**Behavior gates**
- [ ] **G3 (measured 2026-10-05):** C and F pass from the CLI with exit 0; A (5/8), B (6/7), D (4/6), E (4/8) exit non-zero with the deviations recorded in §3/§5/§9 and `docs/FINAL_VALIDATION.md`. Present honestly — do not claim all six pass.
- [ ] **G4:** dashboard plays A and C end-to-end; C shows quarantine, "no public warning", and the baseline false alarm without scrolling at 1440×900.
- [ ] **G5 / offline:** with networking **disabled**, `pytest` passes and the dashboard plays A, C, D and the audit proof (T059). Header chip reads `OFFLINE — no network used`.
- [ ] No real network calls occur during the run.

**Determinism and audit**
- [ ] Same-seed run reproduces identical results (**Start / restart run** reproduces the same run).
- [ ] `SESSION_START` shows the model hash and config hash for the run, and these match across rehearsals (visible in the audit table payload).
- [ ] **Verify chain** → `✔ VERIFIED`; **Tamper a copy** → `✖ BROKEN at #k`; the live log still verifies; the **original audit log is not edited** by anything.

**Screen state**
- [ ] `SIMULATED DATA — NOT AN OPERATIONAL WARNING SYSTEM` banner visible; the phone panel is titled `Phone simulator — DEMO (nothing leaves this machine)`; action log `tier` column shows `1 · auto (demo policy)` for demo auto-approvals.
- [ ] **Reveal ground truth** is OFF at the start of each scenario (turn it on deliberately and only after the verdict; label it as not visible to StageProof).
- [ ] Tier-1 mode visible: sidebar caption `Policy: tier-1 actions auto-approved in demo mode.` when `demo_auto_approve_tier1: true`; action log `tier` column shows `1 · auto (demo policy)`.
- [ ] Tier-2 actions (`ACK_RELEASE`, `RECOMMEND_EVACUATION`) remain `PENDING` until a human clicks; nothing auto-releases an attack quarantine.
- [ ] Dashboard shows engine decisions only (no UI-computed verdicts) — by design; nothing on screen should differ from `--explain` output for the same tick.

**Backup**
- [ ] Backup screen recording of the full demo exists (T061).
- [ ] Replay caches exported for A, C, D, E, F **if T057 was completed** (T061); otherwise the CLI timeline and recording are the fallbacks.
- [ ] Presenter knows the fallback order in §13.

**Rehearsals (T060)**
- [ ] Rehearsal 1: ___ min · Rehearsal 2: ___ min · Rehearsal 3: ___ min — all within the §4 budget, three times in a row. Record in MEMORY.md.
- [ ] Presenter practised the disclaimers and the "never say" list (§6).

---

## 16. One-page cheat sheet

1. Disclaimers → one-liner → problem in 20 s.
2. **C:** valid packets → PHANTOM → POSSIBLE CYBER ATTACK / FABRICATED → B quarantined → no public warning → threshold alarm would have fired. *Signature valid ≠ reading true.*
3. **D:** real flood hidden → SUPPRESSED → POSSIBLE CYBER ATTACK / SUPPRESSION → B quarantined → ESTIMATED level → PROVISIONAL warning, basis ESTIMATE.
4. **A:** real flood not rejected → REAL FLOOD (first commit t441) → provisional → confirmed after downstream (t465). Tail can briefly quarantine B — say it's a documented limitation if it happens.
5. **Proof:** verify → tamper a copy → BROKEN at #k; live still VERIFIED.
6. Five takeaways (§10). Limitations (§11). Never say "attack detected", "100%", "production-ready".
