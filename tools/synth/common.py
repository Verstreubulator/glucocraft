# License: AGPL-3.0-only (LICENSE).
"""Shared pieces of the synthetic-data tools: units, rounding, and the source states that make up a series.

Nothing here imports GlucoCraft. The conversion factor and the consensus pairs are the published values that
GlucoCraft's documentation states (docs/SYSTEM_DESIGN.md, section 4.3): 1 mmol/L = 18.016 mg/dL, and the consensus
thresholds 54 / 70 / 140 / 180 / 250 mg/dL = 3.0 / 3.9 / 7.8 / 10.0 / 13.9 mmol/L.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

UNIT_MGDL = "mg/dL"
UNIT_MMOLL = "mmol/L"
UNITS = (UNIT_MGDL, UNIT_MMOLL)
MGDL_PER_MMOLL = 18.016
CONSENSUS_MMOLL = {54: 3.0, 70: 3.9, 140: 7.8, 180: 10.0, 250: 13.9}
DEFAULT_TZ = "America/Los_Angeles"

# Texts a source shows instead of a number. "LO" / "HI": FreeStyle Libre; 39 / 401: the codes of Dexcom Share.
LO_TEXT, HI_TEXT = "LO", "HI"
DEXCOM_LO_CODE, DEXCOM_HI_CODE = "39", "401"


def round_half_up(x: float | Fraction | Decimal, decimals: int = 0) -> float:
    """Round halves away from zero, exactly (a float is taken as its shortest decimal representation)."""
    if isinstance(x, Fraction):
        d = Decimal(x.numerator) / Decimal(x.denominator)
    else:
        d = Decimal(repr(float(x))) if not isinstance(x, Decimal) else x
    q = Decimal(1).scaleb(-decimals)
    return float(d.quantize(q, rounding=ROUND_HALF_UP))


_MGDL_NAMES = frozenset({"mg/dl", "mgdl", "mg/100ml", "mg"})
_MMOLL_NAMES = frozenset({"mmol/l", "mmoll", "mmol", "mmol/liter", "mmol/litre"})


def unit_name(unit: str) -> str:
    """Return the canonical unit name for 'mg/dl', 'mgdl', 'mmol', 'mmol/l' and the like.

    Only glucose concentration units are accepted: 'mmol/mol' (the A1C unit) or a text naming two units is refused,
    never taken as mmol/L.
    """
    u = str(unit).strip().lower().replace(" ", "")
    if u in _MMOLL_NAMES:
        return UNIT_MMOLL
    if u in _MGDL_NAMES:
        return UNIT_MGDL
    raise ValueError(f"unknown glucose unit: {unit!r} (use mg/dL or mmol/L)")


def mgdl_to_unit(mg: float, unit: str) -> float:
    """Return a mg/dL value in `unit` at the source precision: mg/dL whole numbers, mmol/L one decimal."""
    if unit_name(unit) == UNIT_MMOLL:
        return round_half_up(mg / MGDL_PER_MMOLL, 1)
    return round_half_up(mg, 0)


def value_text(value: float, unit: str) -> str:
    """Return a value already in `unit` as the source's state text (mg/dL '120', mmol/L '6.7')."""
    if unit_name(unit) == UNIT_MMOLL:
        return f"{round_half_up(value, 1):.1f}"
    return f"{int(round_half_up(value, 0))}"


def mgdl_text(mg: float, unit: str) -> str:
    """Return a mg/dL value as the state text of a source in `unit`."""
    return value_text(mgdl_to_unit(mg, unit), unit)


@dataclass
class SourceState:
    """One recorded state of a glucose source entity.

    recorded: when Home Assistant recorded the state (epoch seconds); measured: the CGM's measurement time, given to
    the entity as the attribute ``measurement_timestamp`` (None: the state has no measurement time, for example
    "unavailable"); state: the state text exactly as the entity shows it.
    """

    recorded: int
    state: str
    measured: int | None = None
    attributes: dict = field(default_factory=dict)


def load_connection(path: str | Path) -> tuple[str, str]:
    """Return (base URL, token) from a JSON file with the keys url and token."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    url, token = str(data.get("url") or "").rstrip("/"), str(data.get("token") or "")
    if not url or not token:
        raise SystemExit(f"{path}: the file needs the keys 'url' and 'token'")
    return url, token


def inside_repository(path: str | Path) -> bool:
    """True when `path` lies in a folder that holds GlucoCraft's source: personal data (a fitted profile, a real
    history) must never land there."""
    resolved = Path(path).expanduser().resolve()
    return any(
        (parent / "custom_components" / "glucocraft").is_dir() for parent in [resolved.parent, *resolved.parent.parents]
    )


@dataclass
class Series:
    """The source states of one entity in recording order, plus its description: generated or scenario data
    (artificial), or a real history read from Home Assistant (real_history.py; artificial False, personal health
    information, never written inside the repository)."""

    unit: str
    interval: int
    tz: str
    states: list[SourceState]
    description: str = ""
    meta: dict = field(default_factory=dict)
    artificial: bool = True

    def to_json(self) -> dict:
        """Return the series as plain JSON data."""
        return {
            "artificial": self.artificial,
            "unit": self.unit,
            "interval": self.interval,
            "tz": self.tz,
            "description": self.description,
            "meta": self.meta,
            "states": [asdict(s) for s in self.states],
        }

    @classmethod
    def from_json(cls, data: dict) -> Series:
        """Build a series from to_json() data."""
        return cls(
            unit=data["unit"],
            interval=int(data["interval"]),
            tz=data["tz"],
            states=[SourceState(**s) for s in data["states"]],
            description=data.get("description", ""),
            meta=data.get("meta") or {},
            artificial=data.get("artificial", True) is not False,
        )

    def save(self, path: str | Path) -> None:
        """Write the series as JSON. A real (not artificial) history is never written inside the repository."""
        if not self.artificial and inside_repository(path):
            raise SystemExit(f"refusing to write a real history inside the repository: {path}")
        Path(path).write_text(json.dumps(self.to_json(), separators=(",", ":")), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Series:
        """Read a series written by save()."""
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))
