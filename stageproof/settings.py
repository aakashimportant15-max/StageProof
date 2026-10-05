"""Settings loader (ARCHITECTURE.md §3 L0, §13; RULES.md §5).

Loads config/*.yaml and .env into a typed, validated Settings object. This is
the only module that reads configuration files or environment variables
(ARCHITECTURE §3 rule 10). Threshold precedence (RULES §5): explicit non-null
value in thresholds.yaml > value derived by fit_models in model.json > error —
resolved lazily via Settings.threshold() once a model is attached.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union, get_args, get_origin, get_type_hints

import yaml

from .domain import ActionType

__all__ = ["ConfigError", "Settings", "load_settings", "parse_env"]


class ConfigError(Exception):
    """Configuration problem with an actionable message (ARCHITECTURE §16)."""


# ---------------------------------------------------------------------------
# .env parsing (small internal parser; no extra dependency)
# ---------------------------------------------------------------------------

def parse_env(text: str) -> dict:
    """Parse .env content: KEY=VALUE lines, '#' comments, optional 'export',
    optional matching quotes. Existing environment variables win."""
    env: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            env[key] = value
    for key in list(env):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _read_env_file(path: Path) -> dict:
    if not path.exists():
        return {}
    return parse_env(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Typed threshold sections (RULES.md §5 — every key, exact names)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransportThresholds:
    ts_skew_max_seconds: int
    soft_flag_suspect_ticks: int


@dataclass(frozen=True)
class ContextThresholds:
    z_consistent: float
    z_implausible: float
    z_window_ticks: int
    scale_floor: float
    band_z: float


@dataclass(frozen=True)
class TrendThresholds:
    up_window_ticks: int
    up_rising_min: float
    up_flat_max: float


@dataclass(frozen=True)
class RainThresholds:
    window_hours: int
    yes_mm: Optional[float]   # null → derived by fit_models (model.json)
    no_fraction: float
    no_mm: Optional[float]    # null → derived by fit_models (model.json)
    stale_hours: int


@dataclass(frozen=True)
class HealthThresholds:
    dropout_ticks: int
    stuck_ticks: int
    stuck_pred_change_min: float
    spike_revert_ticks: int
    spike_revert_tol_steps: int
    noise_window_ticks: int
    noise_ratio_max: float
    clean_window_ticks: int
    clean_ratio_min: float
    clean_min_move_steps: int


@dataclass(frozen=True)
class DriftThresholds:
    max_dev: float
    max_rate_per_hour: float


@dataclass(frozen=True)
class ReplayThresholds:
    window_ticks: int
    tol_steps: int
    min_range_steps: int
    min_age_days: int


@dataclass(frozen=True)
class PersistenceThresholds:
    fault_attack_ticks: int
    real_downgrade_ticks: int
    immediate_subtypes: tuple[str, ...]


@dataclass(frozen=True)
class SensorThresholds:
    clean_ticks_to_recover: int
    recovery_ticks: int


@dataclass(frozen=True)
class AlertThresholds:
    watch_idle_ticks: int
    clear_ticks: int
    est_warn_ticks: int


@dataclass(frozen=True)
class VerificationThresholds:
    prior_real: float
    default_reliability: float
    confirm_posterior: float
    refute_posterior: float
    min_replies: int
    timeout_ticks: int
    cooldown_ticks: int
    max_volunteers: int


@dataclass(frozen=True)
class Thresholds:
    transport: TransportThresholds
    context: ContextThresholds
    trend: TrendThresholds
    rain: RainThresholds
    health: HealthThresholds
    drift: DriftThresholds
    replay: ReplayThresholds
    persistence: PersistenceThresholds
    sensor: SensorThresholds
    alert: AlertThresholds
    verification: VerificationThresholds


THRESHOLD_SECTIONS = {
    "transport": TransportThresholds,
    "context": ContextThresholds,
    "trend": TrendThresholds,
    "rain": RainThresholds,
    "health": HealthThresholds,
    "drift": DriftThresholds,
    "replay": ReplayThresholds,
    "persistence": PersistenceThresholds,
    "sensor": SensorThresholds,
    "alert": AlertThresholds,
    "verification": VerificationThresholds,
}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Any:
    if not path.exists():
        raise ConfigError(
            f"missing configuration file: {path} — restore it from the "
            f"documented defaults (RULES.md §5, ARCHITECTURE.md §13)"
        )
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc


def _check_scalar(name: str, value: Any, tp: Any, where: str) -> Any:
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{where}: '{name}' must be a number, got {value!r}")
        return float(value)
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{where}: '{name}' must be an integer, got {value!r}")
        return int(value)
    if tp is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{where}: '{name}' must be true/false, got {value!r}")
        return value
    if tp is str:
        if not isinstance(value, str):
            raise ConfigError(f"{where}: '{name}' must be a string, got {value!r}")
        return value
    return value


def _check_value(name: str, value: Any, tp: Any, where: str) -> Any:
    if value is None:
        return None
    origin = get_origin(tp)
    if origin is Union:
        errors = []
        for arg in get_args(tp):
            if arg is type(None):
                continue
            try:
                return _check_value(name, value, arg, where)
            except ConfigError as exc:
                errors.append(str(exc))
        raise ConfigError(f"{where}: '{name}' has an invalid value {value!r}")
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise ConfigError(f"{where}: '{name}' must be a list, got {value!r}")
        inner = get_args(tp)[0] if get_args(tp) else Any
        return tuple(_check_value(name, v, inner, where) for v in value)
    return _check_scalar(name, value, tp, where)


def _build_section(cls: type, raw: Any, section: str, path: Path) -> Any:
    where = f"{path} [{section}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a mapping")
    hints = get_type_hints(cls)
    missing = [name for name in hints if name not in raw]
    if missing:
        raise ConfigError(
            f"{where}: missing required key(s): {', '.join(missing)} "
            f"(RULES.md §5 requires every key; use null only where the docs allow it)"
        )
    unknown = [key for key in raw if key not in hints]
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s): {', '.join(unknown)} — "
            f"fix the typo or remove the key"
        )
    kwargs = {name: _check_value(name, raw[name], hints[name], where)
              for name in hints}
    return cls(**kwargs)


# ---------------------------------------------------------------------------
# Policy / messages / reach wrappers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Policy:
    demo_auto_approve_tier1: bool
    tiers: dict                       # action type -> tier int
    triggers: dict                    # trigger name -> list of entries

    def tier(self, action: str) -> int:
        if action not in self.tiers:
            raise ConfigError(
                f"no approval tier configured for action '{action}' — "
                f"add it to config/policy.yaml [tiers] (RULES.md §12)"
            )
        return self.tiers[action]

    def trigger_actions(self, trigger: str) -> list:
        if trigger not in self.triggers:
            raise ConfigError(
                f"unknown policy trigger '{trigger}' — "
                f"add it to config/policy.yaml [triggers] (RULES.md §12)"
            )
        return list(self.triggers[trigger])


@dataclass(frozen=True)
class Messages:
    templates: dict                   # template_key -> {lang: text}
    reasons: dict                     # reason code -> text (RULES Appendix A)
    volunteers: dict                  # volunteer id -> {name, language, station, reliability}


@dataclass(frozen=True)
class Reach:
    raw: dict

    @property
    def tick_minutes(self) -> int:
        return self.raw["tick_minutes"]

    @property
    def tick_seconds(self) -> int:
        return self.raw["tick_minutes"] * 60

    @property
    def units(self) -> dict:
        return self.raw.get("units") or {}

    @property
    def stations(self) -> dict:
        return self.raw.get("stations") or {}

    @property
    def levels(self) -> dict:
        return self.raw.get("levels") or {}

    @property
    def rain(self) -> dict:
        return self.raw.get("rain") or {}

    @property
    def splits(self) -> dict:
        return self.raw.get("splits") or {}

    @property
    def events(self) -> dict:
        return self.raw.get("events") or {}

    @property
    def baselines(self) -> dict:
        return self.raw.get("baselines") or {}

    @property
    def fit(self) -> dict:
        return self.raw.get("fit") or {}

    def station(self, role: str) -> dict:
        stations = self.stations
        if role not in stations:
            raise ConfigError(
                f"no station configured for role '{role}' — "
                f"add it to config/reach.yaml [stations]"
            )
        return stations[role]

    def validate(self) -> None:
        if not isinstance(self.raw.get("tick_minutes"), int) or self.tick_minutes <= 0:
            raise ConfigError(
                "config/reach.yaml: 'tick_minutes' must be a positive integer"
            )
        stations = self.stations
        for role in ("A", "T", "B", "C"):
            entry = stations.get(role)
            if not isinstance(entry, dict):
                raise ConfigError(
                    f"config/reach.yaml: missing station '{role}' under "
                    f"[stations] (upstream, tributary, target, downstream are all required)"
                )
            if not isinstance(entry.get("key_env"), str) or not entry["key_env"]:
                raise ConfigError(
                    f"config/reach.yaml: station '{role}' needs a non-empty 'key_env'"
                )
        levels = self.levels.get("B")
        if not isinstance(levels, dict):
            raise ConfigError(
                "config/reach.yaml: missing 'levels.B' (watch/action/clear/evac stage for the target sensor)"
            )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class Settings:
    """Typed configuration (ARCHITECTURE §13; RULES §5)."""

    def __init__(self, config_dir: Path, thresholds: Thresholds, policy: Policy,
                 messages: Messages, reach: Reach, env: dict):
        self.config_dir = config_dir
        self.thresholds = thresholds
        self.policy = policy
        self.messages = messages
        self.reach = reach
        self.env = env
        self._model: Optional[dict] = None

    # -- threshold precedence (RULES §5): yaml non-null > model.json > error --

    def attach_model(self, model: Optional[dict]) -> None:
        """Attach the fitted model artifact (artifacts/model.json content)."""
        self._model = model

    def threshold(self, section: str, key: str) -> Any:
        if not hasattr(self.thresholds, section):
            raise ConfigError(
                f"unknown threshold section '{section}' — "
                f"expected one of: {', '.join(THRESHOLD_SECTIONS)}"
            )
        group = getattr(self.thresholds, section)
        if not hasattr(group, key):
            raise ConfigError(
                f"unknown threshold key '{section}.{key}' — check RULES.md §5"
            )
        value = getattr(group, key)
        if value is not None:
            return value
        model = self._model or {}
        derived = model.get("thresholds", {}).get(section, {}).get(key)
        if derived is not None:
            return derived
        # fit_models writes some derived values at the artifact top level
        # (e.g. model.json "rain": {"yes_mm", "no_mm"}); honor RULES §5.
        derived = model.get(section, {}).get(key)
        if derived is not None:
            return derived
        raise ConfigError(
            f"threshold '{section}.{key}' is null in config/thresholds.yaml and "
            f"was not derived into artifacts/model.json — run "
            f"'python scripts/fit_models.py' or set the value explicitly in "
            f"config/thresholds.yaml"
        )

    # -- signing keys (RULES §14.2: environment variables only) --

    def station_key(self, role: str) -> str:
        entry = self.reach.station(role)
        key_env = entry["key_env"]
        value = self.env.get(key_env)
        if not value:
            raise ConfigError(
                f"signing key for station '{role}' is not set: environment "
                f"variable '{key_env}' is empty or missing — copy "
                f".env.example to .env (DEMO-ONLY keys) or export {key_env}"
            )
        return value

    # -- convenience --

    @property
    def station_roles(self) -> tuple:
        return ("A", "T", "B", "C")


# ---------------------------------------------------------------------------
# load_settings
# ---------------------------------------------------------------------------

def _load_policy(path: Path) -> Policy:
    raw = _load_yaml(path)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a mapping")
    if "demo_auto_approve_tier1" not in raw:
        raise ConfigError(f"{path}: missing 'demo_auto_approve_tier1' (RULES.md §12)")
    tiers = raw.get("tiers")
    if not isinstance(tiers, dict) or not tiers:
        raise ConfigError(f"{path}: [tiers] must be a non-empty mapping (RULES.md §12)")
    expected = {action.value for action in ActionType}
    missing = sorted(expected - set(tiers))
    if missing:
        raise ConfigError(
            f"{path}: [tiers] is missing action(s): {', '.join(missing)} (RULES.md §12)"
        )
    unknown = sorted(set(tiers) - expected)
    if unknown:
        raise ConfigError(
            f"{path}: [tiers] has unknown action(s): {', '.join(unknown)} (RULES.md §12)"
        )
    for action, tier in tiers.items():
        if tier not in (0, 1, 2) or isinstance(tier, bool):
            raise ConfigError(
                f"{path}: tier for '{action}' must be 0, 1 or 2, got {tier!r} (RULES.md §12)"
            )
    triggers = raw.get("triggers")
    if not isinstance(triggers, dict) or not triggers:
        raise ConfigError(f"{path}: [triggers] must be a non-empty mapping (RULES.md §12)")
    for trigger, entries in triggers.items():
        if not isinstance(entries, list) or not entries:
            raise ConfigError(
                f"{path}: trigger '{trigger}' must be a non-empty list of actions"
            )
        for entry in entries:
            if not isinstance(entry, (str, dict)):
                raise ConfigError(
                    f"{path}: trigger '{trigger}' entries must be action names or mappings"
                )
    return Policy(
        demo_auto_approve_tier1=bool(raw["demo_auto_approve_tier1"]),
        tiers=dict(tiers),
        triggers=triggers,
    )


def _load_messages(path: Path) -> Messages:
    raw = _load_yaml(path)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a mapping")
    templates = raw.get("templates")
    if not isinstance(templates, dict) or not templates:
        raise ConfigError(f"{path}: [templates] must be a non-empty mapping (DESIGN.md §9.3)")
    for key, langs in templates.items():
        if not isinstance(langs, dict) or "en" not in langs:
            raise ConfigError(
                f"{path}: template '{key}' must provide at least an 'en' text (DESIGN.md §9.3)"
            )
    reasons = raw.get("reasons")
    if not isinstance(reasons, dict) or not reasons:
        raise ConfigError(f"{path}: [reasons] must be a non-empty mapping (RULES.md Appendix A)")
    for code, text in reasons.items():
        if not isinstance(text, str) or not text:
            raise ConfigError(f"{path}: reason '{code}' must have non-empty text")
    volunteers_raw = raw.get("volunteers")
    # Stored as a list of {id, name, language, station, reliability}; normalized
    # to a mapping keyed by volunteer id for lookup.
    if not isinstance(volunteers_raw, list) or not volunteers_raw:
        raise ConfigError(f"{path}: [volunteers] must be a non-empty list (DESIGN.md §9.3)")
    volunteers: dict = {}
    for entry in volunteers_raw:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise ConfigError(f"{path}: every volunteer needs an 'id'")
        if entry["id"] in volunteers:
            raise ConfigError(f"{path}: duplicate volunteer id '{entry['id']}'")
        volunteers[entry["id"]] = entry
    return Messages(templates=templates, reasons=reasons, volunteers=volunteers)


def _load_reach(path: Path) -> Reach:
    raw = _load_yaml(path)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a mapping")
    reach = Reach(raw)
    reach.validate()
    return reach


def load_settings(config_dir: Union[str, Path] = "config",
                  env_path: Union[str, Path] = ".env") -> Settings:
    """Load and validate config/*.yaml plus .env into a typed Settings."""
    config_dir = Path(config_dir)
    env = _read_env_file(Path(env_path))
    thresholds_raw = _load_yaml(config_dir / "thresholds.yaml")
    if not isinstance(thresholds_raw, dict):
        raise ConfigError(f"{config_dir / 'thresholds.yaml'} must be a mapping")
    thresholds = Thresholds(**{
        section: _build_section(cls, thresholds_raw.get(section), section,
                                config_dir / "thresholds.yaml")
        for section, cls in THRESHOLD_SECTIONS.items()
    })
    return Settings(
        config_dir=config_dir,
        thresholds=thresholds,
        policy=_load_policy(config_dir / "policy.yaml"),
        messages=_load_messages(config_dir / "messages.yaml"),
        reach=_load_reach(config_dir / "reach.yaml"),
        env=env,
    )
