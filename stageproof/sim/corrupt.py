"""Sensor emulator, faults, attacks and packet-mutation directives (T034/T035).

Pure functions over numpy arrays: (true series, spec, seeded RNG) ->
corrupted series. No engine imports (layer rule N-sim). Every fault/attack
takes explicit integer tick bounds and never reads or writes global state,
so a seeded run reproduces the same telemetry bit-for-bit.

Faults (RULES §3.2 families): DROPOUT, STUCK, SPIKE, NOISE_BURST, DRIFT,
RANGE_OOB. Attacks (RULES §19 C/D): FABRICATED_RAMP (hydrograph-like rise
that stays within per-tick rate limits), SUPPRESSION (blend toward a
baseline). Transport directives (RULES §19 F): UNSIGNED_INJECT mutates the
stage AFTER signing (signature no longer matches); REPLAY_PACKET re-emits an
earlier packet (sequence reused). P1 families (REPLAY_COPY, SLOW_RAMP,
STEALTH_BOUNDED, COORDINATED, adaptive noise) are added in T039.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

__all__ = [
    "ROLES", "FAULT_TYPES", "ATTACK_TYPES", "DIRECTIVE_TYPES",
    "quantize", "emulate_sensor",
    "apply_dropout", "apply_stuck", "apply_spike", "apply_noise_burst",
    "apply_drift", "apply_range_oob",
    "apply_fabricated_ramp", "apply_suppression",
    "TransportDirective", "unsigned_inject", "replay_packet",
]

ROLES = ("A", "T", "B", "C")

FAULT_TYPES = ("DROPOUT", "STUCK", "SPIKE", "NOISE_BURST", "DRIFT",
               "RANGE_OOB")
ATTACK_TYPES = ("FABRICATED_RAMP", "SUPPRESSION")
DIRECTIVE_TYPES = ("UNSIGNED_INJECT", "REPLAY_PACKET")


# ---------------------------------------------------------------------------
# sensor emulator (T034): seeded noise + quantization
# ---------------------------------------------------------------------------

def quantize(values: np.ndarray, quant_step: float) -> np.ndarray:
    """Snap to the sensor's reporting grid (e.g. 0.01 ft USGS resolution)."""
    if quant_step <= 0:
        return np.asarray(values, dtype=float)
    return np.round(np.round(np.asarray(values, dtype=float) / quant_step)
                    * quant_step, 10)


def emulate_sensor(true_stage: np.ndarray, rng: np.random.Generator,
                   noise_std: float, quant_step: float) -> np.ndarray:
    """Realistic telemetry: additive Gaussian sensor noise + quantization.

    The noise level is the station's calibrated quiet-period std
    (config/reach.yaml stations.*.noise_std; derived in T006)."""
    noise = rng.normal(0.0, noise_std, size=len(true_stage))
    return quantize(np.asarray(true_stage, dtype=float) + noise, quant_step)


# ---------------------------------------------------------------------------
# faults (T034): pure functions, explicit [start_tick, end_tick] inclusive
# ---------------------------------------------------------------------------

def apply_dropout(series: np.ndarray, start_tick: int, end_tick: int) -> np.ndarray:
    """Sensor stops reporting: NaN over the range (pipeline sees no packets)."""
    out = np.array(series, dtype=float, copy=True)
    out[start_tick:end_tick + 1] = np.nan
    return out


def _last_good(series: np.ndarray, start_tick: int) -> float:
    idx = min(start_tick, len(series) - 1)
    while idx >= 0 and not np.isfinite(series[idx]):
        idx -= 1
    if idx < 0:
        idx = start_tick
    return float(series[idx])


def apply_stuck(series: np.ndarray, start_tick: int, end_tick: int,
                level: float | None = None) -> np.ndarray:
    """Frozen output: constant value across the range (STUCK signature:
    range over the window <= 1 quantization step while the river changes)."""
    out = np.array(series, dtype=float, copy=True)
    if level is None:
        level = _last_good(series, start_tick)
    out[start_tick:end_tick + 1] = level
    return out


def apply_spike(series: np.ndarray, tick: int, magnitude: float) -> np.ndarray:
    """One-tick excursion of `magnitude`; the next tick returns to the true
    series (spike-and-revert, detected retroactively per RULES §3.2)."""
    out = np.array(series, dtype=float, copy=True)
    out[tick] = out[tick] + magnitude
    return out


def apply_noise_burst(series: np.ndarray, start_tick: int, end_tick: int,
                      rng: np.random.Generator, ratio: float,
                      baseline_std: float) -> np.ndarray:
    """Extra noise on top of the sensor layer: std = ratio * baseline_std
    over the range (NOISE signature: diff-std / baseline > noise_ratio_max)."""
    out = np.array(series, dtype=float, copy=True)
    span = end_tick - start_tick + 1
    out[start_tick:end_tick + 1] += rng.normal(0.0, ratio * baseline_std, span)
    return out


def apply_drift(series: np.ndarray, start_tick: int, end_tick: int,
                total_offset: float) -> np.ndarray:
    """Slow sustained calibration drift: linear ramp 0 -> total_offset across
    the range (small, slow deviation; RULES SENSOR_FAULT/DRIFT family)."""
    out = np.array(series, dtype=float, copy=True)
    span = end_tick - start_tick + 1
    ramp = np.linspace(0.0, total_offset, span)
    out[start_tick:end_tick + 1] += ramp
    return out


def apply_range_oob(series: np.ndarray, start_tick: int, end_tick: int,
                    sensor_min: float, sensor_max: float,
                    margin: float = 0.5, high: bool = True) -> np.ndarray:
    """Out-of-range fault: flatlined beyond the physical envelope
    ([sensor_min, sensor_max] from reach config -> RANGE flags)."""
    out = np.array(series, dtype=float, copy=True)
    pinned = sensor_max + margin if high else sensor_min - margin
    out[start_tick:end_tick + 1] = pinned
    return out


# ---------------------------------------------------------------------------
# attacks (T035)
# ---------------------------------------------------------------------------

def apply_fabricated_ramp(series: np.ndarray, start_tick: int,
                          peak_stage: float, rise_ticks: int, hold_ticks: int,
                          decline_ticks: int,
                          max_per_tick: float | None = None) -> np.ndarray:
    """Fabricated flood: hydrograph-like rise from the pre-event level to
    `peak_stage`, hold, decline — each per-tick step capped at
    `max_per_tick` (the station's spike_delta_max) so the fake hydrograph is
    shape-plausible and never trips the informational rate check (T035
    acceptance). If the cap forces a slower rise/decline, the phase is
    stretched in ticks (the fault spans longer, it never exceeds the rate)."""
    out = np.array(series, dtype=float, copy=True)
    base = _last_good(series, start_tick)
    rise = max(int(rise_ticks), 1)
    hold = max(int(hold_ticks), 0)
    decline = max(int(decline_ticks), 1)

    def stretched(delta: float, ticks: int) -> int:
        if max_per_tick and delta > 0 and delta / ticks > max_per_tick:
            return int(np.ceil(delta / max_per_tick))
        return ticks

    rise = stretched(abs(peak_stage - base), rise)
    decline = stretched(abs(peak_stage - base), decline)

    profile = np.concatenate([
        np.linspace(0.0, peak_stage - base, rise, endpoint=False),
        np.full(hold, peak_stage - base),
        np.linspace(peak_stage - base, 0.0, decline + 1),
    ])
    end = min(start_tick + len(profile), len(out))
    out[start_tick:end] = base + profile[:end - start_tick]
    return out


def apply_suppression(series: np.ndarray, start_tick: int, end_tick: int,
                      baseline: float, strength: float) -> np.ndarray:
    """Suppressed real flood: observed = (1-strength)*true + strength*baseline.
    Strength in (0, 1); the observed series stays plausible in shape while
    the crest is held near the baseline (RULES §19 D)."""
    out = np.array(series, dtype=float, copy=True)
    blended = ((1.0 - strength) * out[start_tick:end_tick + 1]
               + strength * baseline)
    out[start_tick:end_tick + 1] = blended
    return out


# ---------------------------------------------------------------------------
# transport directives (T035): applied AFTER signing (RULES §19 F)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransportDirective:
    """One post-signing packet mutation scheduled by a scenario manifest."""
    type: str                     # UNSIGNED_INJECT | REPLAY_PACKET
    role: str
    tick: int
    params: dict = field(default_factory=dict)


def unsigned_inject(reading, new_stage: float):
    """Return the packet with the stage value altered AFTER signing — the
    stale signature no longer matches (SIG_INVALID)."""
    return replace(reading, stage=float(new_stage))


def replay_packet(history: list, from_offset: int = 4):
    """Return an EARLIER packet from the channel history (default 1 h back) —
    its sequence number is already consumed (SEQ_REPLAY)."""
    if not history:
        return None
    idx = max(len(history) - 1 - max(int(from_offset), 0), 0)
    return history[idx]
