# StageProof — DESIGN.md

**Authority:** This document owns **how StageProof looks, reads and behaves for a human**: screens, components, visual tokens, terminology on screen, message wording, interaction and UI states.
**Defers to:** RULES.md (what the system decides — the UI only displays it), ARCHITECTURE.md (data available in `TickRecord`, module boundaries), PRD.md (scope).
**Rule of thumb:** the UI **displays** decisions; it never recomputes them. If something needs a new number, the engine must produce it and ARCHITECTURE.md must list it.

---

## 1. Design philosophy

1. **Operations console, not an analytics dashboard.** Calm, dark, dense-but-readable; the first glance answers: *Is the river high? Can I trust this reading? What did the system do about it?*
2. **Explain the reasoning to non-ML judges.** Every verdict is accompanied by plain-language reasons and a small evidence checklist (✓ / ✗ / ?). No jargon without a label ("z-score" is shown as "deviation from expected").
3. **Distinguish what is measured from what is inferred.** *Observed* (sensor said), *Expected* (what upstream + rain imply), and *ESTIMATED* (substitute while the sensor is quarantined) are always visually and verbally distinct.
4. **Never rely on color alone.** Each state has a label, an icon and a line/pattern style.
5. **Honest about being a prototype.** A persistent SIMULATED banner is always visible.
6. **No decoration without information.** No animations beyond smooth chart playback.

## 2. UX goals

| Goal | Measure |
|---|---|
| A judge understands the Scenario C "wow" within 10 seconds of the verdict appearing | Verdict card + chart + "no public warning" line all visible without scrolling |
| An operator can tell trusted data from quarantined data at a glance | Sensor chips and chart shading |
| The audit/tamper story is demonstrable in under 40 seconds | Proof screen: verify → tamper → fail |
| The presenter never needs to type | All controls are buttons/selectors |

## 3. Information hierarchy (top to bottom)

1. **Is this a simulation?** (always)
2. **What is the verdict, how confident, and what is the alert state?**
3. **What is the river doing vs. what it should be doing?** (observed vs. expected)
4. **Which sensors do we trust?**
5. **What did the system do?** (actions, messages)
6. **Why?** (evidence — Screen 2)
7. **Can it be proven?** (audit — Screen 3)

## 4. Application structure

Streamlit, wide layout, dark theme, three tabs (`st.tabs`): **Live Operations · Incident / Evidence · Proof**. A persistent **header strip** and a **sidebar control panel** appear on all tabs.

### 4.1 Header strip (all tabs)
`StageProof` wordmark · tagline · **SIMULATED DATA — NOT AN OPERATIONAL WARNING SYSTEM** (high-contrast banner, never dismissible) · scenario name · simulated clock (UTC) · tick *n / N* · run mode chip (`LIVE` or `CACHED PLAYBACK`) · **Audit chain** chip (`✔ VERIFIED · 37 events` / `✖ BROKEN at #12` / `…`) · offline chip (`OFFLINE — no network used`).

### 4.2 Sidebar controls
| Control | Behavior |
|---|---|
| Scenario selector | A–F with one-line descriptions (see §10); selecting resets the run |
| ▶ Play / ⏸ Pause | Advances ticks on a timer |
| ⏭ Step +1 tick · ⏩ Step +1 hour (4 ticks) | Manual stepping |
| ⇥ Jump to next event | Advances to the next verdict change or alert transition |
| Speed | 2 / 8 / 24 ticks per second (default 8) |
| ↺ Reset | Rebuild `Runner` and stream (same seed) |
| Reveal ground truth (toggle, default OFF) | Shows injected-attack/fault intervals and true level on the chart, labeled "GROUND TRUTH (not visible to StageProof)" |
| Autopilot operators (toggle, default ON in demo) | Applies the scenario's scripted volunteer replies and officer actions |
| Tier-1 approval mode (read-only chip) | `AUTO-APPROVED (demo)` or `MANUAL` from `policy.yaml` |
| Cached playback (P1) | Plays exported `TickRecord`s instead of running the engine |
| Warm-up | On scenario start the first `warmup_ticks` run instantly behind a "Initializing…" progress bar |

## 5. Visual system

### 5.1 Palette (dark theme; colorblind-aware, always paired with icon + label)

| Token | Hex | Used for |
|---|---|---|
| `bg` | `#0E1117` | page |
| `panel` | `#161B22` | cards |
| `border` | `#2A313C` | dividers |
| `text` | `#E6EDF3` | primary text |
| `muted` | `#8B98A5` | secondary text |
| `observed` | `#E6EDF3` | observed line (solid) |
| `expected` | `#56B4E9` @ 25% fill | expected band |
| `estimate` | `#C9D1D9` | estimate line (dotted) with "ESTIMATED" tag |
| `quarantine` | `#6E7681` hatched | quarantined interval shading |
| `real` | `#56B4E9` | REAL FLOOD |
| `fault` | `#E69F00` | SENSOR FAULT |
| `attack` | `#FF5C5C` | POSSIBLE CYBER ATTACK |
| `uncertain` | `#CC79A7` | UNCERTAIN |
| `normal` | `#009E73` | NORMAL |

### 5.2 Verdict visualization

| State | Icon | Label (exact text) | Line/marker style on chart | Card treatment |
|---|---|---|---|---|
| NORMAL | ✔ | `NORMAL` | thin solid | muted border |
| REAL FLOOD | 🌊 | `REAL FLOOD` | thick solid | blue left bar |
| SENSOR FAULT | 🔧 | `SENSOR FAULT · <subtype>` | dashed + ✕ markers | orange left bar |
| POSSIBLE CYBER ATTACK | 🛡 | `POSSIBLE CYBER ATTACK · <subtype>` | dotted thick + ▲ markers | red left bar, subtle red tint |
| UNCERTAIN | ❓ | `UNCERTAIN` | dash-dot | purple left bar |

The card always states **committed** verdict and, when different, the **candidate** with progress, e.g. `candidate: POSSIBLE CYBER ATTACK (2 / 3)`.

### 5.3 Alert severity ladder
A four-segment meter plus text, independent of verdict colors:

| State | Meter | Text |
|---|---|---|
| NONE | ▯▯▯▯ | `NO ALERT` |
| WATCH | ▮▯▯▯ | `WATCH` |
| PROVISIONAL_WARNING | ▮▮▯▯ | `PROVISIONAL WARNING` |
| CONFIRMED_WARNING | ▮▮▮▮ | `CONFIRMED WARNING` |
| CLEARED | ✔ | `ALL CLEAR` |

Each alert shows its **basis tag**: `BASIS: OBSERVED | ESTIMATE | COMMUNITY | DOWNSTREAM`.

### 5.4 Observed vs. estimated, trusted vs. quarantined
- **Observed:** solid white line, source label `OBSERVED (sensor B)`.
- **Expected:** blue band labeled `EXPECTED from upstream + rain` (band = interval).
- **Estimate in use:** dotted line plus a tag `ESTIMATED — B quarantined` and a hatched background strip; numbers carry the suffix `(est.)` and an interval `±`.
- **Sensor chips:** `A · TRUSTED`, `B · 🔒 QUARANTINED (attack)`, with reason text on hover/expander; SUSPECT and RECOVERING have their own chips (`SUSPECT`, `RECOVERING 3/8`).
- **Rejected packets** (HARD transport flag) are drawn as ✖ markers on the observed axis with tooltip `rejected: SIG_INVALID`.

### 5.5 Typography
System sans-serif for UI; monospace for numbers, hashes and signatures. Minimum body size 14 px; KPI numbers 28–32 px.

## 6. Screen 1 — Live Operations

### 6.1 Layout (desktop 1440 × 900)

```text
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ HEADER STRIP (banner · scenario · clock · tick · mode · audit chip · offline chip)        │
├─────────┬────────────────────────────────────────────────────────────────────────────────┤
│ SIDEBAR │ KPI ROW                                                                         │
│ controls│ [VERDICT] [ALERT] [TARGET SENSOR B] [OBSERVED vs EXPECTED] [BEST LEVEL]         │
│         ├───────────────────────────────────────────────┬────────────────────────────────┤
│         │ MAIN CHART (shared time axis)                  │ WHY (reasons, plain language)  │
│         │  ① Rainfall (bars)                             │ SENSORS (A T B C chips)        │
│         │  ② Upstream A and tributary T                  │ BASELINES (2 chips)            │
│         │  ③ Target B: observed, expected band, estimate,│ ACTION TIMELINE                │
│         │     watch/action lines, rejected packets,      │ COMMUNITY / OFFICER PANEL      │
│         │     quarantine shading, (ground truth if on)   │  (phone simulator + approvals) │
│         │  ④ Downstream C                                │                                │
│         ├───────────────────────────────────────────────┴────────────────────────────────┤
│         │ EVENT TICKER (latest verdict/alert/sensor transitions)                          │
└─────────┴────────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 KPI cards

| KPI | Content | Data source (`TickRecord`) |
|---|---|---|
| **Verdict** | icon + label + confidence chip (LOW/MED/HIGH) + `candidate (n/N)` when pending + rule id (small) | `verdict.label/subtype/confidence/candidate/persist_*`, `verdict.rule_id` |
| **Alert** | severity meter + text + basis tag | `alert_state`, `alert_source` |
| **Target sensor B** | state chip + reason + "excluded from decisions" note when not TRUSTED | `sensor_status["B"]` |
| **Observed vs expected** | `Observed 14.82 ft` · `Expected 12.10 ft (±)` · `Deviation +4.1σ (PHANTOM)` | `evidence.obs_stage`, `prediction.stage_pred`, `evidence.z_mean`, `evidence.context` |
| **Best level** | value with `OBSERVED` or `ESTIMATED` tag, action-stage reference, interval when estimated | `best_level`, `best_source`, `estimate`, config levels |

### 6.3 Main chart
- Four stacked panels with shared x-axis and a vertical cursor at the current tick.
- ③ includes horizontal lines for `watch_stage`, `action_stage`; verdict-colored segments/markers on the observed line; hatched quarantine strips; dotted estimate line when `estimate.in_use`; ✖ for rejected packets.
- If **Reveal ground truth** is ON: true B level as a faint green line and shaded "INJECTED: fabricated ramp" interval, labeled as not visible to the system.
- Data: `TickRecord` history → `plots.py` builds figures. No computation of verdicts in plots.

### 6.4 Why panel (plain-language reasons)
Up to 4 lines produced from `verdict.reasons` using the English text for each reason code (RULES Appendix A), ordered by importance. Example (Scenario C): 
- "Message signature is valid." 
- "The reading is much higher than upstream gauges and rainfall can explain."
- "Upstream gauge is flat. No rainfall in the last 48 hours."
- "Result: possible cyber attack — sensor quarantined, no public warning sent."
The last line is generated from the committed actions.

### 6.5 Sensors panel
Four chips (A upstream, T tributary, B target, C downstream) with state, plus a one-line input-usage note: `Model inputs in use: A+T+R (HIGH confidence)` or `DEGRADED: using T+R (A quarantined)` from `prediction.variant`, `degraded`.

### 6.6 Baselines strip
Two chips that make the contrast explicit: `Threshold alarm: FIRING / silent` and `Single-sensor anomaly detector: FLAGGED / ok` (from `baselines`), with a small caption "for comparison — not part of StageProof".

### 6.7 Action timeline
Latest 8 actions: time · icon · action name · tier chip (`T0 AUTO`, `T1 AUTO-APPROVED (demo)` / `T1 PENDING`, `T2 HUMAN`) · status · short note. Examples: `QUARANTINE_SENSOR · B · T0 · EXECUTED`; `SEND_PUBLIC_WARNING · PROVISIONAL · basis ESTIMATE · T1`.

### 6.8 Community / officer panel
- **Officer controls:** for `PENDING` Tier 1 actions: `Approve` / `Reject`; for attack-quarantined sensors: `Acknowledge & release (Tier 2)` (enabled only when the clean condition holds, else disabled with the reason); `Confirm warning` / `Mark all clear` when applicable. All call `Runner` operator methods; results appear in the next record.
- **Phone simulator (inclusion element):** a phone frame labeled `SIMULATED DEVICE`. Shows the thread for the selected audience (Public / Volunteer V1 / V2 / Officer) with a language switch (English / हिन्दी). Messages are shown with a leading `DEMO ·`. Volunteer prompts show three reply buttons `1 Yes · 2 No · 3 Not sure`; in Autopilot mode scripted replies appear automatically. A small panel shows the verification round: replies received, posterior bar with the `confirm` and `refute` marks, timeout countdown.

## 7. Screen 2 — Incident / Evidence

### 7.1 Purpose
Answer **"Why did StageProof decide that?"** for one incident, in a layout a judge can read top-to-bottom.

### 7.2 Layout

```text
┌ Incident selector (list of verdict changes / alert transitions; default = latest) ──────┐
├ INCIDENT HEADER: icon · label · subtype · confidence · committed at tick/time · rule ────┤
│   one-sentence summary (template) · "Candidate → Committed" persistence indicator          │
├──────────────────────────────────────────┬───────────────────────────────────────────────┤
│ EVIDENCE CHECKLIST (✓ / ✗ / ?)           │ OBSERVED vs EXPECTED                           │
│  Transport · Sensor health · Upstream     │  observed stage · expected stage ± · residual │
│  consistency · Upstream trend · Rainfall  │  deviation (σ) · regime · model variant/inputs│
│  · Downstream · Replay · Notable level    ├───────────────────────────────────────────────┤
│  · Degraded inputs                        │ DEVIATION TIMELINE (z_mean with ±thresholds,  │
│                                           │  persistence markers, committed-at marker)    │
├──────────────────────────────────────────┴───────────────────────────────────────────────┤
│ FALLBACK ESTIMATE card · ACTIONS triggered by this incident · AUDIT info (events #, hashes)│
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 7.3 Evidence checklist rows

| Row | Shows | Symbols | Source |
|---|---|---|---|
| Message integrity | signature, station binding, sequence, timestamp | ✓ all passed / ✗ list of flags | `evidence.transport_flags`, `transport_hard` |
| Sensor health | dropout / stuck / spike / noise / range | ✓ shape plausible / ✗ subtype | `evidence.health_flags`, `shape_ok` |
| Matches upstream + rain? | context class, deviation σ | ✓ consistent / ✗ phantom or suppressed / ? ambiguous or insufficient | `evidence.context`, `z_mean` |
| Upstream trend | rising / flat / falling / unknown | text + arrow | `evidence.up_trend` |
| Rainfall | 48-h total and support | ✓ yes / ✗ no / ? unknown | `evidence.rain` |
| Downstream response | yes / no / pending / unknown | ✓ ✗ ⏳ ? | `evidence.downstream`, `z_down_mean` |
| Replay | exact copy of earlier data? | ✓ no match / ✗ match / – not enabled | `evidence.replay_match` |
| Notable level | above watch stage? | text | `evidence.notable` |
| Input quality | variant and degraded flag | chip | `evidence.degraded`, `prediction.variant` |

Each row has a one-line plain-language explanation drawn from the reason texts. A **"What would change this verdict?"** footnote lists the missing/ambiguous evidence when the verdict is UNCERTAIN.

### 7.4 Observed vs. expected panel
Numbers: observed stage; expected stage with interval; residual in ln-discharge (shown as "% higher/lower than expected" = `exp(residual) − 1`); deviation in σ; regime (normal/high flow); variant and the list of trusted inputs used.

### 7.5 Fallback estimate card
`Estimate (not measured): 12.4 ft  [11.8 – 13.1]  ·  source: upstream A+T + rain  ·  confidence HIGH  ·  IN USE since tick 143`. If no estimate: `No estimate available — inputs insufficient`.

### 7.6 Actions and audit info
List of actions triggered at this incident (with tier and status) and the related audit events: `#14 VERDICT_CHANGE · hash 9f3a…c1`, `#15 SENSOR_STATE ·…`, linking to the Proof screen with that event selected.

## 8. Screen 3 — Proof

### 8.1 Purpose
Show that the system's claims are **verifiable**: packets are authenticated, a valid signature is not treated as truth, the audit chain is tamper-evident, and each scenario behaves as specified.

### 8.2 Sections (top to bottom)

**A. Signed packet inspector.** Choose a channel and tick. Shows `station_id, ts, seq, stage, unit`, the canonical string, the signature (truncated, monospace) and the transport result (`✔ passed` or the failing flags). Buttons **"Alter stage"**, **"Change station"**, **"Shift timestamp"** re-run the stateless verification on a copy and show `✖ SIG_INVALID` / `STATION_MISMATCH` / `TS_SKEW`.

**B. "Valid signature ≠ true reading".** Side-by-side card (most useful for Scenario C): left, `Signature: ✔ valid · sequence ✔ · timestamp ✔`; right, `Physical consistency: ✖ PHANTOM — upstream flat, no rain`. Caption: *A signature proves who sent a message, not whether it is true.*

**C. Sequence and replay evidence.** Per channel plot of `seq` vs tick, with duplicate/backward sequence numbers highlighted and soft gaps annotated; list of HARD/SOFT flags with tick and meaning.

**D. Audit log.** Table: `# · time · kind · summary · hash (first 10) · prev (first 10)`, filterable by kind. Buttons: **Verify chain** → `✔ VERIFIED (n events)` or `✖ BROKEN at #k (reason)`. Selected row shows full payload (evidence snapshot for verdict changes).

**E. Tamper demonstration.** Pick an event and a field (e.g., change `payload.label` or delete the event), press **Tamper a copy**. The app writes a modified **copy** (never the live log), verifies it, and displays `✖ BROKEN at #k` while the original still shows `✔ VERIFIED`. A short caption explains that any edit, deletion or reordering is detected at the first affected event.

**F. Scenario classification.** Table of scenarios A–F: `scenario · expected committed label · observed committed label · alert outcome · sensor outcome · result (✔ / ✖ / not run)` from `evaluate_expectations`. Clicking a row loads the cached/live run.

**G. Evaluation (P1).** Figures from `artifacts/eval/`: real-extreme false-rejection (StageProof vs. baselines), detection delay by attack magnitude, estimate error and interval coverage, and a clearly labeled list of adversary families that are **not** caught. If not generated: empty-state message (see §11).

## 9. Copy deck

### 9.1 Display labels (must match RULES exactly)
`NORMAL` · `REAL FLOOD` · `SENSOR FAULT` · `POSSIBLE CYBER ATTACK` · `UNCERTAIN`. Sensor states: `TRUSTED` · `SUSPECT` · `QUARANTINED` · `RECOVERING`. Alert states: `NO ALERT` (NONE) · `WATCH` · `PROVISIONAL WARNING` · `CONFIRMED WARNING` · `ALL CLEAR` (CLEARED). Never display "attack detected" as certain; always "possible".

### 9.2 Reason texts
English texts for reason codes are exactly those in RULES.md Appendix A (stored in `messages.yaml → reasons`). Hindi reason texts are not required.

### 9.3 Message templates (`messages.yaml → templates`)
Fields: `{station}` station display name, `{level}` stage with unit, `{status}` current status text, `{basis}` basis phrase. Hindi text should be reviewed by a native speaker before any real use.

| Key | English | हिन्दी |
|---|---|---|
| `public_watch` | `River level near {station} may be rising. Stay alert and follow local updates.` | `{station} के पास नदी का जलस्तर बढ़ सकता है। सतर्क रहें और स्थानीय सूचनाओं पर ध्यान दें।` |
| `warning_provisional` | `FLOOD WARNING (provisional): the river at {station} is above the warning level ({level}). Basis: {basis}. Stay away from the river bank and low-lying areas. Follow instructions from local authorities.` | `बाढ़ चेतावनी (अस्थायी): {station} पर नदी का जलस्तर चेतावनी स्तर ({level}) से ऊपर है। आधार: {basis}। नदी किनारे और निचले इलाकों से दूर रहें। स्थानीय प्रशासन के निर्देशों का पालन करें।` |
| `warning_confirmed` | `FLOOD WARNING (confirmed): the river at {station} remains above the warning level ({level}). Basis: {basis}. Follow instructions from local authorities.` | `बाढ़ चेतावनी (पुष्ट): {station} पर नदी का जलस्तर चेतावनी स्तर ({level}) से ऊपर बना हुआ है। आधार: {basis}। स्थानीय प्रशासन के निर्देशों का पालन करें।` |
| `correction` | `UPDATE: the earlier river alert for {station} was based on an unreliable reading and is withdrawn. Current status: {status}.` | `अपडेट: {station} के लिए पहले भेजी गई नदी चेतावनी एक अविश्वसनीय रीडिंग पर आधारित थी और वापस ली जाती है। वर्तमान स्थिति: {status}।` |
| `all_clear` | `ALL CLEAR: the river level at {station} is back below the warning level.` | `स्थिति सामान्य: {station} पर नदी का जलस्तर चेतावनी स्तर से नीचे लौट आया है।` |
| `volunteer_prompt` | `Please check the river at {station}. Is the water above the red mark? Reply 1 = Yes, 2 = No, 3 = Not sure.` | `कृपया {station} पर नदी देखें। क्या पानी लाल निशान से ऊपर है? उत्तर दें: 1 = हाँ, 2 = नहीं, 3 = पता नहीं।` |
| `volunteer_thanks` | `Thank you. Your reply was recorded.` | `धन्यवाद। आपका उत्तर दर्ज कर लिया गया है।` |

Basis phrases (`templates.basis_*`): 
| Basis | English | हिन्दी |
|---|---|---|
| OBSERVED | `verified river-gauge readings` | `सत्यापित नदी-गेज रीडिंग` |
| ESTIMATE | `an estimate from upstream gauges and rainfall` | `ऊपरी गेज और वर्षा से लगाया गया अनुमान` |
| COMMUNITY | `reports from local volunteers` | `स्थानीय स्वयंसेवकों की रिपोर्ट` |
| DOWNSTREAM | `gauge readings confirmed by the downstream gauge` | `निचले गेज द्वारा पुष्ट गेज रीडिंग` |

Officer/ticket messages (English only): `officer_uncertain` ("Reading at {station} cannot be verified ({reason}). Community verification requested."), `officer_security` ("POSSIBLE CYBER ATTACK on sensor {sensor}: {reason}. Sensor quarantined; evidence preserved."), `officer_fault` ("SENSOR FAULT ({subtype}) on {sensor}. Maintenance ticket opened; estimate in use."), `officer_recommend_evac` ("Evacuation RECOMMENDATION for human decision at {station}. No order has been sent.").

Display rule: the phone simulator prefixes every message with `DEMO ·` (display only; templates remain unchanged).

## 10. Scenario controls — descriptions

| ID | Selector text |
|---|---|
| A | `A · Real flood — everything agrees` |
| B | `B · Sensor fault — gauge B gets stuck` |
| C | `C · Fabricated flood — valid key, no physical support` |
| D | `D · Suppressed flood — valid key, real flood hidden` |
| E | `E · Uncertain — upstream telemetry lost during a rise` |
| F | `F · Unsigned & replayed packets — transport attack` |

Selecting a scenario shows a one-paragraph "what to watch" note under the selector (from the manifest `description`).

## 11. UI states

| State | Treatment |
|---|---|
| **Loading / warm-up** | Progress bar "Initializing (n/N warm-up ticks)"; controls disabled until done. |
| **Empty** | No scenario started: centered prompt "Select a scenario and press Play". Incident screen with no incidents: "No incidents yet — the system has seen nothing unusual." Evaluation not generated: "Run `make eval` to generate evaluation figures." |
| **Error** | Component-level error card with a short message and a "Details" expander; the rest of the app keeps working. Missing artifacts: blocking message "Model not found — run `make fit`" (and `make data` / `make scenarios`). |
| **Degraded data** | Yellow chip `DEGRADED INPUTS · using <variant>`; reason text names the missing input (e.g., "Upstream gauge A unavailable"). Stale rain: chip `RAIN FEED STALE`. |
| **Halted audit** | Blocking red banner: "Audit log unavailable — actions are paused" (RULES §17). |
| **Simulated** | Persistent banner (§4.1); phone frame labeled `SIMULATED DEVICE`. |
| **Offline** | Chip `OFFLINE — no network used`. |
| **Cached playback** | Header chip `CACHED PLAYBACK`; operator controls disabled; tooltip explains why. |

## 12. Accessibility

- Every state conveyed by color is also conveyed by an icon and text (§5.2–5.4); chart lines differ in dash pattern and marker shape.
- Text/background contrast target ≥ 4.5:1; large KPI text ≥ 3:1.
- Chart alternatives: each chart has a one-sentence text summary ("Gauge B rose above the action level while upstream stayed flat") and the underlying values are available in a table expander.
- Keyboard: all controls are native Streamlit widgets (focusable); no hover-only information — hover content is duplicated in expanders.
- No flashing or rapidly blinking elements; motion is limited to chart playback.
- Language: the phone simulator offers English and Hindi; all other UI text is English.

## 13. Responsive behavior

Primary target is a **desktop/laptop at ≥ 1280 px** (judging environment). Below ~1100 px the right column stacks under the chart; below ~800 px the KPI row wraps to two columns. Mobile is not a design target; the layout must remain readable but is not optimized.

## 14. Data-binding summary (UI element → `TickRecord` field)

| UI element | Field(s) |
|---|---|
| Header clock / tick | `ts`, `tick_idx` |
| Audit chip | `audit_head_hash`, `audit.verify()` result (via Proof helpers) |
| Verdict KPI | `verdict.*` |
| Alert KPI | `alert_state`, `alert_source` |
| Sensor chips | `sensor_status`, `prediction.variant`, `prediction.degraded` |
| Observed/expected KPI | `evidence.obs_stage`, `prediction.stage_pred`, `evidence.z_mean`, `evidence.context` |
| Best level KPI | `best_level`, `best_source`, `estimate` |
| Chart ①–④ | history of `readings/accepted` (stage), rain values, `prediction`, `estimate`, `verdict.label`, `sensor_status`, `transport_flags` |
| Baseline chips | `baselines.threshold_alert`, `baselines.rollz_flag` |
| Action timeline | `new_actions` (accumulated) |
| Phone simulator | `new_messages` (accumulated), `verification` |
| Evidence checklist | `evidence.*` |
| Fallback card | `estimate.*` |
| Proof screen packets | `readings`, `transport_flags` |
| Proof screen audit | audit JSONL via `security.audit` |
| Scenario classification | `sim.scenarios.evaluate_expectations(scenario, history)` |
| Ground-truth overlay | `truth` (attached by the driver; hidden by default) |
