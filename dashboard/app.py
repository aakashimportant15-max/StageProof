    """
    spans = _scenario_spans(manifests)

    if not spans:
        return pd.read_csv(path, index_col="ts_utc", parse_dates=True)

    lo = min(s for s, _ in spans)
    hi = max(e for _, e in spans)
    parts: list[pd.DataFrame] = []

    for chunk in pd.read_csv(path, chunksize=100_000):
        ts = pd.to_datetime(chunk.pop("ts_utc"), utc=True, format="ISO8601")

        if ((ts >= lo) & (ts <= hi)).any():
            keep = (ts >= spans[0][0]) & (ts <= spans[0][1])

            for s, e in spans[1:]:
                keep |= (ts >= s) & (ts <= e)

            if keep.any():
                part = chunk.loc[keep].copy()
                part.insert(0, "ts_utc", ts[keep])
                parts.append(part)

        if ts.iloc[-1] > hi:
            break

    if not parts:
        return pd.read_csv(path, index_col="ts_utc", parse_dates=True)

    df = pd.concat(parts, ignore_index=True)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    df = df.set_index("ts_utc").sort_index()

    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


@st.cache_resource(show_spinner=False)
def load_assets() -> dict:
    # Streamlit Cloud exposes secrets through st.secrets.
    # Bridge demo signing keys into the existing environment-based loader.
    for key in (
        "STAGEPROOF_KEY_A",
        "STAGEPROOF_KEY_T",
        "STAGEPROOF_KEY_B",
        "STAGEPROOF_KEY_C",
    ):
        if key in st.secrets:
            os.environ[key] = str(st.secrets[key])

    settings = load_settings(ROOT / "config", env_path=ROOT / ".env")
    art = load_model_artifact(ROOT / "artifacts" / "model.json")
    settings.attach_model(art)
    bundle = ModelBundle.from_dict(art)
    keys = load_keys(settings.env, settings.reach.stations)
    # mirror run_scenario.py: size the NOISE check against the fitted baseline
    stations = {role: dict(p) for role, p in settings.reach.stations.items()}
    for role, sp in art.get("station_params", {}).items():
        if role in stations and "noise_baseline_std" in sp:
            stations[role]["noise_baseline_std"] = float(
                sp["noise_baseline_std"])
    manifests: dict = {}
    for sid in SCENARIO_LABELS:
        path = ROOT / "data" / "scenarios" / f"{sid}.json"
        if path.exists():
            try:
                manifests[sid] = load_manifest(path)
            except Exception:  # a broken manifest must not kill the app
                continue
    df = _load_reach_windowed(ROOT / "data" / "reach.csv", manifests)
    return {"settings": settings, "bundle": bundle, "df": df, "keys": keys,
            "stations": stations, "manifests": manifests}


# ---------------------------------------------------------------------------
# run lifecycle (mirrors scripts/run_scenario.py)
# ---------------------------------------------------------------------------

def start_run(sid: str, assets: dict) -> dict:
    manifest = assets["manifests"][sid]
    stream = ScenarioStream(manifest, assets["df"], assets["stations"],
                            assets["keys"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_path = (ROOT / "artifacts" / "audit"
                  / f"dashboard_{manifest.id}_{stamp}.jsonl")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    runner = Runner(assets["settings"], assets["bundle"], AuditLog(audit_path))
    first_ts, _ = stream.tick(0)
    runner.start(manifest.id, manifest.seed, ts=first_ts.ts)
    return {
        "sid": manifest.id, "manifest": manifest, "stream": stream,
        "runner": runner, "audit_path": audit_path,
        "next_idx": 0, "records": [], "rain": [], "pending_replies": [],
        "playing": False, "finished": False,
        "reveal_truth": False, "autopilot": True, "speed": 8, "error": None,
    }


def _advance(run: dict, steps: int = 1) -> None:
    """Step the engine `steps` ticks — identical to the CLI driver loop."""
    stream, runner = run["stream"], run["runner"]
    try:
        for _ in range(int(steps)):
            idx = run["next_idx"]
            if idx >= len(stream):
                break
            tick_input, truth = stream.tick(idx)
            if run["autopilot"]:
                for op in stream.operator_events_at(idx):
                    if op.get("kind") == "ACK_RELEASE":
                        runner.ack_release(str(op.get("officer", "OPERATOR")))
                run["pending_replies"].extend(stream.volunteer_replies_at(idx))
                open_round = None
                if run["records"]:
                    ver = run["records"][-1].verification
                    if ver and ver.get("status") == "OPEN":
                        open_round = ver.get("round_id")
                if open_round:
                    for vrep in run["pending_replies"]:
                        runner.submit_verification_reply(VolunteerReply(
                            ts=tick_input.ts,
                            volunteer_id=str(vrep["volunteer_id"]),
                            code=int(vrep["code"]), round_id=open_round))
                    run["pending_replies"] = []
            else:
                # manual mode still queues scripted replies for the UI buttons
                run["pending_replies"].extend(stream.volunteer_replies_at(idx))
            rec = runner.step(tick_input)
            rec.truth = truth
            run["records"].append(rec)
            run["rain"].append(tick_input.rain_prev_hr_mm)
            run["next_idx"] = idx + 1
    except Exception as exc:  # never crash the whole app on one bad tick
        run["error"] = f"{type(exc).__name__}: {exc}"
        run["playing"] = False
    if run["next_idx"] >= len(stream) and not run["finished"]:
        run["finished"] = True
        run["playing"] = False
        try:
            runner.finish()
        except Exception as exc:
            run["error"] = f"{type(exc).__name__}: {exc}"


def _event_sig(rec) -> tuple:
    v = rec.verdict
    return (v.label.value if v else None, v.subtype if v else None,
            rec.alert_state.value, rec.alert_source,
            tuple(sorted((role, s.state.value)
                         for role, s in rec.sensor_status.items())))


def _is_event(prev, rec) -> bool:
    if prev is None or _event_sig(prev) != _event_sig(rec):
        return True
    return bool(rec.new_actions or rec.new_messages)
