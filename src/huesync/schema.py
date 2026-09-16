"""Current persisted schema. Historical conversions live only in migration.py."""
from __future__ import annotations

import math
import types
from enum import Enum
from typing import get_args, get_origin, get_type_hints

from .models import (
    EFFECT_IDS,
    ONSET_METHODS,
    Analyser,
    Controller,
    Coupling,
    Effect,
    EnergyProfile,
    PlayerLatency,
    VirtualPlayer,
    Zone,
)

SCHEMA_VERSION = 1
COLLECTIONS = {
    "controllers": Controller,
    "virtual_players": VirtualPlayer,
    "zones": Zone,
    "analysers": Analyser,
    "effects": Effect,
    "energy_profiles": EnergyProfile,
    "couplings": Coupling,
    "player_latencies": PlayerLatency,
}


_FIELD_TYPES = {key: get_type_hints(model) for key, model in COLLECTIONS.items()}

def empty_config() -> dict:
    return {"schema_version": SCHEMA_VERSION, "active_coupling_id": None,
            **{key: [] for key in COLLECTIONS}}


def validate_current(data: dict, *, references: bool = False) -> None:
    """Reject non-current data. Optional full reference check at deployment boundary.

    CRUD can temporarily contain unbound entities; migration must not introduce
    dangling nonempty references. Empty selections remain valid editable objects.
    """
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
            or data.get("schema_version") != SCHEMA_VERSION):
        raise ValueError("Unsupported persisted schema; run scripts/install-huesync.sh to migrate")
    if set(data) != set(empty_config()):
        raise ValueError("Unexpected/missing persisted collections; run the repository migration")
    ids = {}
    for key, model in COLLECTIONS.items():
        rows = data[key]
        if not isinstance(rows, list):
            raise ValueError(f"{key}: expected a list")
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{key}: expected objects")
            identity = row.get("player_mac" if key == "player_latencies" else "id")
            if not isinstance(identity, str) or not identity or identity in seen:
                raise ValueError(f"{key}: missing/duplicate ID {identity!r}")
            seen.add(identity)
            model.from_dict(row)
            for field, value in row.items():
                expected = _FIELD_TYPES[key][field]
                choices = (get_args(expected) if get_origin(expected) is types.UnionType
                           else (expected,))
                valid = False
                for choice in choices:
                    if isinstance(choice, type) and issubclass(choice, Enum):
                        valid |= value in {item.value for item in choice}
                    elif choice is float:
                        valid |= type(value) in (int, float) and math.isfinite(value)
                    else:
                        valid |= type(value) is choice
                if not valid:
                    raise ValueError(f"{key}/{identity}: invalid type/value for {field}")
            if key == "effects" and row.get("effect_type", "spectrum_rgb") not in EFFECT_IDS:
                raise ValueError(f"{key}/{identity}: unsupported effect_type")
            if key == "analysers" and row.get("onset_method", "combined") not in ONSET_METHODS:
                raise ValueError(f"{key}/{identity}: unsupported onset_method")
        ids[key] = seen
    if data["active_coupling_id"] is not None and not isinstance(data["active_coupling_id"], str):
        raise ValueError("active_coupling_id must be a string or null")
    if references:
        for collection, field, target in (
            ("zones", "controller_id", "controllers"),
            ("energy_profiles", "high_energy_effect_id", "effects"),
            ("energy_profiles", "low_energy_effect_id", "effects"),
            ("couplings", "player_id", "virtual_players"),
            ("couplings", "zone_id", "zones"),
            ("couplings", "analyser_id", "analysers"),
            ("couplings", "energy_profile_id", "energy_profiles"),
        ):
            for row in data[collection]:
                value = row.get(field, "")
                if value and value not in ids[target]:
                    raise ValueError(f"{collection}/{row['id']}: dangling {field}={value!r}")
        active = data["active_coupling_id"]
        if active and active not in ids["couplings"]:
            raise ValueError(f"Dangling active_coupling_id={active!r}")
