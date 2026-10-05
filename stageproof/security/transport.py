"""Transport security: HMAC signing and stateful packet verification (T010).

Implements RULES.md §3.1 and ARCHITECTURE.md §5.1-5.2. The design principle:
a valid signature proves origin, not truth — this layer authenticates
packets; whether a packet is *true* is judged by the physical-consistency
layers downstream.

Packet canonical string: "{station_id}|{ts_utc_iso}|{seq}|{stage:.3f}|{unit}";
sig = hex HMAC-SHA256 of the canonical string under the station's key,
compared with hmac.compare_digest. verify() collects every applicable flag;
any HARD flag rejects the packet — its stage value MUST NOT be used as an
observation (treated as missing for modeling) but the failure is recorded as
evidence. State advances only on valid packets.

Flags (RULES §3.1): SIG_INVALID / STATION_MISMATCH / UNKNOWN_STATION /
SEQ_REPLAY / TS_SKEW / TS_NONMONOTONIC (HARD); SEQ_GAP (SOFT, gap size in
detail). MISSING (SOFT — no packet on a channel this tick) is produced by the
pipeline, not here. verify_stateless runs signature, binding and skew checks
without touching state (Proof screen packet-tamper demonstration).
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..domain import SensorReading

__all__ = [
    "TransportError", "TransportState", "TransportVerdict",
    "canonical_string", "sign_reading", "verify", "verify_stateless",
    "load_keys", "utc_iso",
    "FLAG_SIG_INVALID", "FLAG_STATION_MISMATCH", "FLAG_UNKNOWN_STATION",
    "FLAG_SEQ_REPLAY", "FLAG_TS_SKEW", "FLAG_TS_NONMONOTONIC",
    "FLAG_SEQ_GAP", "FLAG_MISSING", "HARD_FLAGS", "SOFT_FLAGS",
]

FLAG_SIG_INVALID = "SIG_INVALID"
FLAG_STATION_MISMATCH = "STATION_MISMATCH"
FLAG_UNKNOWN_STATION = "UNKNOWN_STATION"
FLAG_SEQ_REPLAY = "SEQ_REPLAY"
FLAG_TS_SKEW = "TS_SKEW"
FLAG_TS_NONMONOTONIC = "TS_NONMONOTONIC"
FLAG_SEQ_GAP = "SEQ_GAP"
FLAG_MISSING = "MISSING"

HARD_FLAGS = (FLAG_SIG_INVALID, FLAG_STATION_MISMATCH, FLAG_UNKNOWN_STATION,
              FLAG_SEQ_REPLAY, FLAG_TS_SKEW, FLAG_TS_NONMONOTONIC)
SOFT_FLAGS = (FLAG_SEQ_GAP, FLAG_MISSING)


class TransportError(Exception):
    """Transport setup problem (e.g. a signing key is not available)."""


def utc_iso(ts: datetime) -> str:
    """ISO-8601 UTC rendering used in the canonical string and audit log."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


def canonical_string(station_id: str, ts: datetime, seq: int,
                     stage: float, unit: str) -> str:
    return f"{station_id}|{utc_iso(ts)}|{seq}|{stage:.3f}|{unit}"


def sign_reading(station_id: str, ts: datetime, seq: int,
                 stage: float, unit: str, key: str) -> str:
    msg = canonical_string(station_id, ts, seq, stage, unit).encode("utf-8")
    return hmac.new(key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def load_keys(env: dict, stations: dict) -> dict[str, str]:
    """Map role -> signing key from the settings env + reach stations table
    (RULES §14.2: keys live in environment variables only)."""
    keys: dict[str, str] = {}
    missing: list[str] = []
    for role, entry in stations.items():
        key_env = (entry or {}).get("key_env", "")
        key = env.get(key_env)
        if not key:
            missing.append(key_env or role)
        else:
            keys[role] = key
    if missing:
        raise TransportError(
            f"signing key(s) not set: {', '.join(sorted(missing))} — copy "
            f".env.example to .env (DEMO-ONLY keys) or export them")
    return keys


@dataclass
class TransportState:
    """Per-channel acceptance state; advances only on valid packets."""
    role: str
    last_seq: int = 0
    last_ts: Optional[datetime] = None

    def advance(self, seq: int, ts: datetime) -> None:
        self.last_seq = seq
        self.last_ts = ts


@dataclass(frozen=True)
class TransportVerdict:
    accepted: bool
    flags: tuple[str, ...] = ()
    hard: tuple[str, ...] = ()
    soft: tuple[str, ...] = ()
    detail: dict = field(default_factory=dict)


def verify(state: TransportState, reading: SensorReading, channel: str,
           tick_ts: datetime, keys: dict[str, str],
           ts_skew_max_seconds: float) -> TransportVerdict:
    """Stateful verification of one packet arriving on `channel` (RULES §3.1).

    Evaluates signature, station binding, station registration, sequence
    replay/gap, timestamp skew and non-monotonic timestamps; advances
    `state` only when no HARD flag fired.
    """
    hard: dict[str, None] = {}   # ordered set
    soft: dict[str, None] = {}
    detail: dict = {}

    if reading.station_id not in keys:
        hard[FLAG_UNKNOWN_STATION] = None
    else:
        expected = sign_reading(reading.station_id, reading.ts, reading.seq,
                                reading.stage, reading.unit,
                                keys[reading.station_id])
        if not hmac.compare_digest(expected, reading.sig):
            hard[FLAG_SIG_INVALID] = None
    if reading.station_id != channel:
        hard[FLAG_STATION_MISMATCH] = None

    rts = datetime.fromisoformat(utc_iso(reading.ts))
    tts = datetime.fromisoformat(utc_iso(tick_ts))
    skew = abs((rts - tts).total_seconds())
    if skew > ts_skew_max_seconds:
        hard[FLAG_TS_SKEW] = None
        detail["skew_seconds"] = skew

    if state.last_seq > 0:
        if reading.seq <= state.last_seq:
            hard[FLAG_SEQ_REPLAY] = None
            detail["last_seq"] = state.last_seq
        elif reading.seq > state.last_seq + 1:
            soft[FLAG_SEQ_GAP] = None
            detail["gap"] = reading.seq - state.last_seq - 1
    if state.last_ts is not None:
        last = datetime.fromisoformat(utc_iso(state.last_ts))
        if rts <= last:
            hard[FLAG_TS_NONMONOTONIC] = None
            detail["last_ts"] = utc_iso(state.last_ts)

    hard_flags = tuple(hard)
    soft_flags = tuple(soft)
    accepted = not hard_flags
    if accepted:
        state.advance(reading.seq, reading.ts)
    return TransportVerdict(accepted=accepted,
                            flags=hard_flags + soft_flags,
                            hard=hard_flags, soft=soft_flags, detail=detail)


def verify_stateless(reading: SensorReading, channel: str,
                     tick_ts: datetime, keys: dict[str, str],
                     ts_skew_max_seconds: float) -> TransportVerdict:
    """Signature, binding and skew only — no sequence/monotonic state.

    Used by the Proof screen's packet-tamper demonstration.
    """
    hard: dict[str, None] = {}
    detail: dict = {}
    if reading.station_id not in keys:
        hard[FLAG_UNKNOWN_STATION] = None
    else:
        expected = sign_reading(reading.station_id, reading.ts, reading.seq,
                                reading.stage, reading.unit,
                                keys[reading.station_id])
        if not hmac.compare_digest(expected, reading.sig):
            hard[FLAG_SIG_INVALID] = None
    if reading.station_id != channel:
        hard[FLAG_STATION_MISMATCH] = None
    rts = datetime.fromisoformat(utc_iso(reading.ts))
    tts = datetime.fromisoformat(utc_iso(tick_ts))
    skew = abs((rts - tts).total_seconds())
    if skew > ts_skew_max_seconds:
        hard[FLAG_TS_SKEW] = None
        detail["skew_seconds"] = skew
    hard_flags = tuple(hard)
    return TransportVerdict(accepted=not hard_flags, flags=hard_flags,
                            hard=hard_flags, soft=(), detail=detail)
