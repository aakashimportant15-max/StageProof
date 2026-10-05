"""Community layer: template rendering and verification rounds.

T029 — `render` (DESIGN §9.3): template-based messages only, no generated
text; missing fields are errors. Basis phrase lookup per language.
T030 — `VerificationManager` (RULES §13): opens a round on a committed
notable UNCERTAIN (cooldown-gated), prompts registered volunteers in their
language, applies the odds-form posterior per reply (dedupe, registered
only, code 3 ignored), resolves at min_replies, and escalates on timeout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .domain import Message, VolunteerReply
from .settings import Messages, Settings

__all__ = ["MessageError", "render", "basis_phrase", "make_message",
           "VerificationRound", "VerificationManager"]


class MessageError(ValueError):
    """Template lookup or field substitution failed (DESIGN §9.3)."""


def render(messages: Messages, key: str, lang: str = "en", **fields) -> str:
    if key not in messages.templates:
        raise MessageError(f"unknown message template '{key}'")
    texts = messages.templates[key]
    text = texts.get(lang) or texts.get("en")
    if text is None:
        raise MessageError(
            f"template '{key}' has no text for language '{lang}' or 'en'")
    try:
        return text.format(**fields)
    except KeyError as exc:
        raise MessageError(
            f"template '{key}' ({lang}) missing field {exc}") from exc
    except (IndexError, ValueError) as exc:
        raise MessageError(f"template '{key}' render failed: {exc}") from exc


def basis_phrase(messages: Messages, basis: str, lang: str = "en") -> str:
    return render(messages, f"basis_{str(basis).lower()}", lang)


def make_message(msg_id: str, ts: datetime, messages: Messages, key: str, *,
                 channel: str, audience: str, lang: str = "en",
                 fields: Optional[dict] = None,
                 action_id: Optional[str] = None,
                 recipient_id: Optional[str] = None) -> Message:
    return Message(msg_id=msg_id, ts=ts, channel=channel, audience=audience,
                   language=lang, text=render(messages, key, lang,
                                              **(fields or {})),
                   action_id=action_id, recipient_id=recipient_id)


@dataclass
class VerificationRound:
    round_id: str
    ts: datetime
    opened_tick: int
    station: str
    prompts: dict[str, str] = field(default_factory=dict)
    replies: dict[str, int] = field(default_factory=dict)
    informative: int = 0
    log_odds: float = 0.0
    status: str = "OPEN"            # OPEN | CONFIRMED | REFUTED | TIMEOUT
    resolved_ts: Optional[datetime] = None

    def posterior(self) -> float:
        odds = math.exp(self.log_odds)
        return odds / (1.0 + odds)

    def summary(self) -> dict:
        return {"round_id": self.round_id, "status": self.status,
                "informative": self.informative,
                "posterior": round(self.posterior(), 4),
                "replies": dict(self.replies),
                "opened_tick": self.opened_tick}


class VerificationManager:
    """RULES §13 round lifecycle for one station (B)."""

    def __init__(self, settings: Settings, station: str = "B",
                 station_name: str = ""):
        self._cfg = settings.thresholds.verification
        self._messages = settings.messages
        self._volunteers = settings.messages.volunteers
        self._station = station
        self._station_name = station_name
        self._counter = 0
        self._last_open_tick: Optional[int] = None
        self.round: Optional[VerificationRound] = None

    @property
    def confirmed(self) -> bool:
        return self.round is not None and self.round.status == "CONFIRMED"

    def maybe_open(self, tick_idx: int, ts: datetime, *,
                   uncertain_notable: bool) -> Optional[VerificationRound]:
        if (not uncertain_notable
                or (self.round is not None and self.round.status == "OPEN")):
            return None
        if (self._last_open_tick is not None
                and tick_idx - self._last_open_tick < self._cfg.cooldown_ticks):
            return None
        self._counter += 1
        rnd = VerificationRound(round_id=f"VR-{self._counter:04d}", ts=ts,
                                opened_tick=tick_idx, station=self._station)
        for vid, vol in self._volunteers.items():
            if vol.get("station") != self._station:
                continue
            if len(rnd.prompts) >= self._cfg.max_volunteers:
                break
            rnd.prompts[vid] = render(self._messages, "volunteer_prompt",
                                      vol.get("language", "en"),
                                      station=self._station_name)
        self.round = rnd
        self._last_open_tick = tick_idx
        return rnd

    def submit_reply(self, reply: VolunteerReply) -> tuple[bool, str]:
        """Returns (accepted, detail). Unregistered senders, duplicates and
        replies to closed rounds are ignored (RULES §13.3)."""
        rnd = self.round
        if rnd is None or rnd.status != "OPEN":
            return False, "no open round"
        if reply.round_id != rnd.round_id:
            return False, "unknown round"
        vol = self._volunteers.get(reply.volunteer_id)
        if vol is None or vol.get("station") != rnd.station:
            return False, "unregistered"
        if reply.volunteer_id in rnd.replies:
            return False, "duplicate"
        code = int(reply.code)
        rnd.replies[reply.volunteer_id] = code
        if code == 3:
            return True, "noted"          # "not sure" never moves the odds
        reliability = float(vol.get("reliability",
                                    self._cfg.default_reliability))
        reliability = min(max(reliability, 1e-6), 1.0 - 1e-6)
        rnd.informative += 1
        if code == 1:
            rnd.log_odds += math.log(reliability / (1.0 - reliability))
        else:
            rnd.log_odds += math.log((1.0 - reliability) / reliability)
        posterior = rnd.posterior()
        if rnd.informative >= self._cfg.min_replies:
            if posterior >= self._cfg.confirm_posterior:
                rnd.status = "CONFIRMED"
                rnd.resolved_ts = reply.ts
                return True, "confirmed"
            if posterior <= self._cfg.refute_posterior:
                rnd.status = "REFUTED"
                rnd.resolved_ts = reply.ts
                return True, "refuted"
        return True, "recorded"

    def tick(self, tick_idx: int, ts: datetime) -> Optional[VerificationRound]:
        """Per-tick timeout check; returns the round when it times out."""
        rnd = self.round
        if rnd is None or rnd.status != "OPEN":
            return None
        if tick_idx - rnd.opened_tick >= self._cfg.timeout_ticks:
            rnd.status = "TIMEOUT"
            rnd.resolved_ts = ts
            return rnd
        return None

    def summary(self) -> Optional[dict]:
        if self.round is None:
            return None
        return self.round.summary()
