"""Security tests: AC-TT (transport tamper) and AC-AT (audit tamper).

TASKS T045. Transport behavior is specified by RULES §3.1; the audit chain
by ARCHITECTURE §5.4.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stageproof.security import audit
from stageproof.security import transport as tr
from stageproof.security.transport import (
    FLAG_SEQ_GAP, FLAG_SEQ_REPLAY, FLAG_SIG_INVALID, FLAG_STATION_MISMATCH,
    FLAG_TS_NONMONOTONIC, FLAG_TS_SKEW, FLAG_UNKNOWN_STATION,
    TransportError, load_keys, sign_reading, verify, verify_stateless)

T0 = datetime(2020, 11, 12, 6, 0, tzinfo=timezone.utc)
SKEW_MAX = 30.0
KEYS = {"A": "key-a", "T": "key-t", "B": "key-b", "C": "key-c"}


def make_reading(seq=1, stage=12.345, ts=None, station_id="B", key="key-b"):
    ts = ts or T0
    sig = sign_reading(station_id, ts, seq, stage, "ft", key)
    from stageproof.domain import SensorReading
    return SensorReading(station_id=station_id, ts=ts, seq=seq,
                         stage=stage, unit="ft", sig=sig)


def fresh_state(role="B"):
    return tr.TransportState(role=role)


# ---------------------------------------------------------------------------
# AC-TT — transport tamper
# ---------------------------------------------------------------------------

def test_valid_packet_accepted_and_state_advances():
    state = fresh_state()
    v = verify(state, make_reading(seq=1), "B", T0, KEYS, SKEW_MAX)
    assert v.accepted and v.flags == () and v.hard == () and v.soft == ()
    assert state.last_seq == 1 and state.last_ts == T0
    v2 = verify(state, make_reading(seq=2, ts=T0 + timedelta(minutes=15)),
                "B", T0 + timedelta(minutes=15), KEYS, SKEW_MAX)
    assert v2.accepted and state.last_seq == 2


def test_canonical_string_and_signature_determinism():
    from stageproof.security.transport import canonical_string
    cs = canonical_string("B", T0, 7, 12.3456, "ft")
    assert cs == "B|2020-11-12T06:00:00+00:00|7|12.346|ft"
    s1 = sign_reading("B", T0, 7, 12.3456, "ft", "key-b")
    s2 = sign_reading("B", T0, 7, 12.3456, "ft", "key-b")
    assert s1 == s2 and len(s1) == 64


def test_altered_stage_is_sig_invalid():
    state = fresh_state()
    r = make_reading(seq=1, stage=12.345)
    tampered = type(r)(**{**r.__dict__, "stage": 99.0})
    v = verify(state, tampered, "B", T0, KEYS, SKEW_MAX)
    assert not v.accepted and FLAG_SIG_INVALID in v.hard
    assert state.last_seq == 0  # state did not advance


def test_altered_ts_is_sig_invalid():
    state = fresh_state()
    r = make_reading(seq=1)
    tampered = type(r)(**{**r.__dict__, "ts": T0 + timedelta(minutes=15)})
    v = verify(state, tampered, "B", T0, KEYS, SKEW_MAX)
    assert not v.accepted and FLAG_SIG_INVALID in v.hard


def test_altered_unit_is_sig_invalid():
    state = fresh_state()
    r = make_reading(seq=1)
    tampered = type(r)(**{**r.__dict__, "unit": "m"})
    v = verify(state, tampered, "B", T0, KEYS, SKEW_MAX)
    assert not v.accepted and FLAG_SIG_INVALID in v.hard


def test_wrong_key_is_sig_invalid():
    state = fresh_state()
    r = make_reading(seq=1, key="key-a", station_id="B")
    v = verify(state, r, "B", T0, KEYS, SKEW_MAX)
    assert not v.accepted and FLAG_SIG_INVALID in v.hard


def test_channel_mismatch_is_station_mismatch():
    state = fresh_state("A")
    r = make_reading(seq=1, station_id="A", key="key-a")  # valid A packet...
    v = verify(state, r, "B", T0, KEYS, SKEW_MAX)  # ...arriving on B
    assert not v.accepted and FLAG_STATION_MISMATCH in v.hard
    assert FLAG_SIG_INVALID not in v.hard  # signature itself was valid


def test_unregistered_station_is_unknown_station():
    state = fresh_state()
    r = make_reading(seq=1, station_id="X", key="key-x")
    v = verify(state, r, "B", T0, KEYS, SKEW_MAX)
    assert not v.accepted and FLAG_UNKNOWN_STATION in v.hard


def test_reused_and_decreasing_seq_are_replay():
    state = fresh_state()
    assert verify(state, make_reading(seq=5), "B", T0, KEYS, SKEW_MAX).accepted
    v_reuse = verify(state, make_reading(seq=5), "B", T0, KEYS, SKEW_MAX)
    assert not v_reuse.accepted and FLAG_SEQ_REPLAY in v_reuse.hard
    v_back = verify(state, make_reading(seq=3), "B", T0, KEYS, SKEW_MAX)
    assert not v_back.accepted and FLAG_SEQ_REPLAY in v_back.hard


def test_seq_gap_is_soft_and_packet_accepted():
    state = fresh_state()
    assert verify(state, make_reading(seq=1), "B", T0, KEYS, SKEW_MAX).accepted
    v = verify(state, make_reading(seq=4, ts=T0 + timedelta(minutes=15)),
               "B", T0 + timedelta(minutes=15), KEYS, SKEW_MAX)
    assert v.accepted
    assert FLAG_SEQ_GAP in v.soft and v.detail["gap"] == 2
    assert state.last_seq == 4


def test_ts_skew_beyond_limit_is_hard():
    state = fresh_state()
    late = make_reading(seq=1, ts=T0 + timedelta(minutes=2))
    v = verify(state, late, "B", T0, KEYS, SKEW_MAX)
    assert not v.accepted and FLAG_TS_SKEW in v.hard
    assert v.detail["skew_seconds"] == 120.0


def test_nonmonotonic_ts_is_hard():
    state = fresh_state()
    assert verify(state, make_reading(seq=1, ts=T0), "B", T0,
                  KEYS, SKEW_MAX).accepted
    same = verify(state, make_reading(seq=2, ts=T0), "B", T0, KEYS, SKEW_MAX)
    assert not same.accepted and FLAG_TS_NONMONOTONIC in same.hard
    earlier = verify(state, make_reading(seq=3, ts=T0 - timedelta(minutes=15)),
                     "B", T0, KEYS, SKEW_MAX)
    assert not earlier.accepted and FLAG_TS_NONMONOTONIC in earlier.hard


def test_state_not_advanced_on_hard_so_retry_works():
    state = fresh_state()
    bad = verify(state, make_reading(seq=1), "A", T0, KEYS, SKEW_MAX)  # mismatch
    assert not bad.accepted
    good = verify(state, make_reading(seq=1), "B", T0, KEYS, SKEW_MAX)
    assert good.accepted and state.last_seq == 1


def test_verify_stateless_no_state_and_no_seq_checks():
    r = make_reading(seq=1)
    v1 = verify_stateless(r, "B", T0, KEYS, SKEW_MAX)
    v2 = verify_stateless(r, "B", T0, KEYS, SKEW_MAX)
    assert v1.accepted and v2.accepted
    old = make_reading(seq=1, ts=T0 - timedelta(days=400))
    v_old = verify_stateless(old, "B", T0 - timedelta(days=400), KEYS, SKEW_MAX)
    assert v_old.accepted  # stateless ignores sequence history entirely


def test_verify_stateless_detects_tamper():
    r = make_reading(seq=1, stage=5.0)
    tampered = type(r)(**{**r.__dict__, "stage": 24.0})
    v = verify_stateless(tampered, "B", T0, KEYS, SKEW_MAX)
    assert not v.accepted and FLAG_SIG_INVALID in v.hard


def test_load_keys_maps_roles_and_fails_loud():
    env = {"STAGEPROOF_KEY_A": "ka", "STAGEPROOF_KEY_T": "kt",
           "STAGEPROOF_KEY_B": "kb"}
    stations = {"A": {"key_env": "STAGEPROOF_KEY_A"},
                "B": {"key_env": "STAGEPROOF_KEY_B"},
                "T": {"key_env": "STAGEPROOF_KEY_T"}}
    keys = load_keys(env, stations)
    assert keys == {"A": "ka", "T": "kt", "B": "kb"}
    with pytest.raises(TransportError):
        load_keys({}, stations)


def test_static_check_compare_digest_used_for_signature():
    source = Path(tr.__file__).read_text(encoding="utf-8")
    assert "hmac.compare_digest(" in source
    assert "reading.sig ==" not in source
    assert "== reading.sig" not in source


# ---------------------------------------------------------------------------
# AC-AT — audit tamper
# ---------------------------------------------------------------------------

def append_five(log: audit.AuditLog) -> None:
    for k in range(5):
        log.append(T0 + timedelta(minutes=15 * (k + 1)), "TEST_EVENT",
                   {"k": k, "note": f"event-{k}"})


def test_chain_appends_and_verifies(tmp_path):
    p = tmp_path / "audit.jsonl"
    log = audit.AuditLog(p)
    first = log.append(T0, "SESSION_START", {"scenario": "A"})
    assert first.prev_hash == audit.GENESIS_PREV_HASH
    append_five(log)
    result = audit.verify(p)
    assert result.ok and result.n_events == 6 and result.first_bad_index is None
    events = audit.load(p)
    assert [e.idx for e in events] == list(range(6))
    assert events[1].prev_hash == events[0].hash


def test_deterministic_chain_for_same_sequence(tmp_path):
    p1, p2 = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    append_five(audit.AuditLog(p1))
    append_five(audit.AuditLog(p2))
    assert p1.read_bytes() == p2.read_bytes()


def test_tampered_payload_detected_at_k(tmp_path):
    p = tmp_path / "audit.jsonl"
    append_five(audit.AuditLog(p))
    original_bytes = p.read_bytes()
    copy = audit.tamper_copy(p, 3, "payload")
    assert copy != p and copy.exists()
    assert p.read_bytes() == original_bytes  # original untouched
    result = audit.verify(copy)
    assert not result.ok and result.first_bad_index == 3


def test_tampered_hash_and_prev_hash_detected(tmp_path):
    p = tmp_path / "audit.jsonl"
    append_five(audit.AuditLog(p))
    r_hash = audit.verify(audit.tamper_copy(p, 2, "hash"))
    assert not r_hash.ok and r_hash.first_bad_index == 2
    r_prev = audit.verify(audit.tamper_copy(p, 2, "prev_hash"))
    assert not r_prev.ok and r_prev.first_bad_index == 2


def test_deleted_event_breaks_chain_at_next_index(tmp_path):
    p = tmp_path / "audit.jsonl"
    append_five(audit.AuditLog(p))
    copy = tmp_path / "deleted.jsonl"
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    del lines[2]
    copy.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = audit.verify(copy)
    assert not result.ok and result.first_bad_index == 3  # first affected


def test_swapped_events_detected(tmp_path):
    p = tmp_path / "audit.jsonl"
    append_five(audit.AuditLog(p))
    copy = tmp_path / "swapped.jsonl"
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    copy.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = audit.verify(copy)
    assert not result.ok
    # original event 2 now sits at position 1: its stored prev_hash points
    # at event 1's hash, but the running head is event 0's -> detected at 2
    assert result.first_bad_index == 2


def test_append_fails_closed_on_write_error(tmp_path):
    log = audit.AuditLog(tmp_path)  # a directory: open() for append fails
    with pytest.raises(audit.AuditError):
        log.append(T0, "TEST_EVENT", {"k": 1})


def test_verify_empty_log_is_ok(tmp_path):
    result = audit.verify(tmp_path / "missing.jsonl")
    assert result.ok and result.n_events == 0


def test_canonical_json_sorts_and_rounds():
    out = audit.canonical_json({"b": 1, "a": 2.0000004, "c": [1.0, {"z": 2, "y": 3}]})
    assert out == '{"a":2.0,"b":1,"c":[1.0,{"y":3,"z":2}]}'
