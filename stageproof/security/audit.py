"""Tamper-evident audit log (T011; ARCHITECTURE.md §5.4).

JSONL; each line is {"idx","ts","kind","payload","prev_hash","hash"} with
hash = SHA-256(canonical_json({idx, ts, kind, payload, prev_hash})).
Canonical JSON: sorted keys, compact separators, floats rounded to 6
decimals. Genesis prev_hash is 64 zeros. Timestamps are the SIMULATED tick
time, so a seeded run reproduces the chain bit-for-bit.

append fails closed: a write error raises AuditError (the pipeline enters
HALTED_AUDIT and executes no action). verify(path) recomputes every hash and
chain link; tamper_copy(path, idx, field) writes a modified COPY — it never
alters the original file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..domain import AuditEvent

__all__ = ["AuditError", "VerifyResult", "AuditLog",
           "canonical_json", "event_hash", "load", "verify", "tamper_copy",
           "GENESIS_PREV_HASH", "TAMPERED_SUFFIX"]

GENESIS_PREV_HASH = "0" * 64
TAMPERED_SUFFIX = ".tampered.jsonl"


class AuditError(Exception):
    """Audit log problem: write failure (fail closed) or unreadable file."""


def _round_floats(obj: Any) -> Any:
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {k: _round_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_floats(v) for v in obj]
    return obj


def canonical_json(obj: Any) -> str:
    return json.dumps(_round_floats(obj), sort_keys=True,
                      separators=(",", ":"))


def event_hash(idx: int, ts_iso: str, kind: str, payload: dict,
               prev_hash: str) -> str:
    basis = {"idx": idx, "ts": ts_iso, "kind": kind,
             "payload": payload, "prev_hash": prev_hash}
    return hashlib.sha256(canonical_json(basis).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    n_events: int
    first_bad_index: Optional[int] = None
    reason: Optional[str] = None


class AuditLog:
    """Append-only hash-chained event log backed by a JSONL file."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._n = 0
        self._head = GENESIS_PREV_HASH

    @property
    def head_hash(self) -> str:
        return self._head

    @property
    def n_events(self) -> int:
        return self._n

    def append(self, ts: datetime, kind: str, payload: dict) -> AuditEvent:
        """Append one event; simulated ts only. Fails closed on write error."""
        ts_iso = ts.isoformat() if isinstance(ts, datetime) else str(ts)
        event = AuditEvent(
            idx=self._n, ts=ts, kind=kind, payload=payload,
            prev_hash=self._head,
            hash=event_hash(self._n, ts_iso, kind, payload, self._head),
        )
        line = json.dumps({
            "idx": event.idx, "ts": ts_iso, "kind": event.kind,
            "payload": event.payload, "prev_hash": event.prev_hash,
            "hash": event.hash,
        }, sort_keys=True, separators=(",", ":"))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
                fh.flush()
        except OSError as exc:
            raise AuditError(
                f"audit append failed for {self.path}: {exc} — failing "
                f"closed; no dependent action may execute (HALTED_AUDIT)")
        self._n += 1
        self._head = event.hash
        return event


def _decode_event(line: str, lineno: int) -> AuditEvent:
    try:
        raw = json.loads(line)
        return AuditEvent(
            idx=int(raw["idx"]),
            ts=datetime.fromisoformat(raw["ts"]),
            kind=str(raw["kind"]),
            payload=raw["payload"],
            prev_hash=str(raw["prev_hash"]),
            hash=str(raw["hash"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise AuditError(f"audit line {lineno} is malformed: {exc}") from exc


def load(path: Path | str) -> list[AuditEvent]:
    path = Path(path)
    if not path.exists():
        return []
    events: list[AuditEvent] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if line:
                events.append(_decode_event(line, lineno))
    return events


def verify(path: Path | str) -> VerifyResult:
    """Recompute every hash and chain link; report the first bad event."""
    events = load(path)
    prev = GENESIS_PREV_HASH
    for ev in events:
        recomputed = event_hash(ev.idx, ev.ts.isoformat(), ev.kind,
                                ev.payload, ev.prev_hash)
        if ev.prev_hash != prev:
            return VerifyResult(
                ok=False, n_events=len(events), first_bad_index=ev.idx,
                reason=f"prev_hash mismatch at event {ev.idx}: chain link "
                       f"broken (expected hash of event {ev.idx - 1})")
        if ev.hash != recomputed:
            return VerifyResult(
                ok=False, n_events=len(events), first_bad_index=ev.idx,
                reason=f"hash mismatch at event {ev.idx}: content was "
                       f"modified after sealing")
        prev = ev.hash
    return VerifyResult(ok=True, n_events=len(events))


def tamper_copy(path: Path | str, idx: int, field: str,
                value: Any = None) -> Path:
    """Write a modified copy of the log with event `idx`'s `field` altered.

    The original file is never touched. Defaults: payload -> {"tampered":
    True}; hash/prev_hash -> 64 'f' chars; anything else -> "TAMPERED".
    Returns the copy path (original name + TAMPERED_SUFFIX).
    """
    path = Path(path)
    copy_path = path.with_name(path.name + TAMPERED_SUFFIX)
    events = load(path)
    if not any(ev.idx == idx for ev in events):
        raise AuditError(f"tamper_copy: no event with idx {idx} in {path}")
    lines = []
    for ev in events:
        d = {
            "idx": ev.idx, "ts": ev.ts.isoformat(), "kind": ev.kind,
            "payload": ev.payload, "prev_hash": ev.prev_hash,
            "hash": ev.hash,
        }
        if ev.idx == idx:
            if value is not None:
                d[field] = value
            elif field == "payload":
                d[field] = {"tampered": True}
            elif field in ("hash", "prev_hash"):
                d[field] = "f" * 64
            else:
                d[field] = "TAMPERED"
        lines.append(json.dumps(d, sort_keys=True, separators=(",", ":")))
    with open(copy_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return copy_path
