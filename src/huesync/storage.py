"""Persistence layer.

Deliberately a single JSON file rather than a database: HueSync manages at
most a handful of entities, so a flat file is easier to inspect, back up,
and diff than a SQLite schema — and it's trivial to hand-edit if something
ever needs fixing outside the GUI.

A simple file lock avoids corruption if the API and a background task write
concurrently (unlikely at this scale, but cheap to guard against).
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path

from .models import (
    Analyser,
    Controller,
    Coupling,
    Effect,
    EnergyProfile,
    PlayerLatency,
    VirtualPlayer,
    Zone,
)
from .schema import REFERENCES, empty_config, validate_current

_lock = threading.Lock()


class ReferencedEntityError(ValueError):
    """A delete would break a persisted relationship."""


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(empty_config())
        # Deployment migration must run before current runtime opens old data.
        self._read()

    def _read(self) -> dict:
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        validate_current(data)
        return data

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp.replace(self.path)

    def read_configuration(self) -> dict:
        """Read one complete persisted snapshot under the same lock as CRUD."""
        with _lock:
            return self._read()

    @staticmethod
    @contextmanager
    def configuration_transaction():
        """Exclude all in-process CRUD writes during an atomic full restore."""
        with _lock:
            yield

    def _delete_entity(self, collection: str, identity: str) -> None:
        # The reference check and write share the CRUD lock: no check/delete gap.
        with _lock:
            data = self._read()
            blockers = [
                f"{source}: {row.get('name', '(unnamed)')} ({row['id']}) via {field}"
                for source, field, target in REFERENCES if target == collection
                for row in data[source] if row.get(field) == identity
            ]
            if blockers:
                raise ReferencedEntityError(
                    "Cannot delete: referenced by " + "; ".join(blockers)
                    + ". Reassign or remove these references first.")
            data[collection] = [row for row in data[collection] if row['id'] != identity]
            self._write(data)

    # -- Player latencies ---------------------------------------------------

    def list_player_latencies(self) -> list[PlayerLatency]:
        with _lock:
            return [PlayerLatency.from_dict(p) for p in self._read()["player_latencies"]]

    def get_player_latency(self, player_mac: str) -> PlayerLatency | None:
        mac = player_mac.strip().lower()
        return next((p for p in self.list_player_latencies() if p.player_mac == mac), None)

    def save_player_latency(self, pl: PlayerLatency) -> None:
        pl.player_mac = pl.player_mac.strip().lower()
        with _lock:
            data = self._read()
            data["player_latencies"] = [
                p for p in data["player_latencies"] if p["player_mac"] != pl.player_mac
            ]
            data["player_latencies"].append(pl.to_dict())
            self._write(data)

    def delete_player_latency(self, player_mac: str) -> None:
        mac = player_mac.strip().lower()
        with _lock:
            data = self._read()
            data["player_latencies"] = [
                p for p in data["player_latencies"] if p["player_mac"] != mac
            ]
            self._write(data)

    # -- Controllers --------------------------------------------------------

    def list_controllers(self) -> list[Controller]:
        with _lock:
            return [Controller.from_dict(c) for c in self._read()["controllers"]]

    def get_controller(self, controller_id: str) -> Controller | None:
        return next((c for c in self.list_controllers() if c.id == controller_id), None)

    def save_controller(self, controller: Controller) -> None:
        with _lock:
            data = self._read()
            data["controllers"] = [c for c in data["controllers"] if c["id"] != controller.id]
            data["controllers"].append(controller.to_dict())
            self._write(data)

    def delete_controller(self, controller_id: str) -> None:
        self._delete_entity("controllers", controller_id)

    # -- VirtualPlayers -----------------------------------------------------

    def list_virtual_players(self) -> list[VirtualPlayer]:
        with _lock:
            return [VirtualPlayer.from_dict(p) for p in self._read()["virtual_players"]]

    def get_virtual_player(self, player_id: str) -> VirtualPlayer | None:
        return next((p for p in self.list_virtual_players() if p.id == player_id), None)

    def save_virtual_player(self, player: VirtualPlayer) -> None:
        with _lock:
            data = self._read()
            data["virtual_players"] = [p for p in data["virtual_players"] if p["id"] != player.id]
            data["virtual_players"].append(player.to_dict())
            self._write(data)

    def delete_virtual_player(self, player_id: str) -> None:
        self._delete_entity("virtual_players", player_id)

    # -- Zones --------------------------------------------------------------

    def list_zones(self) -> list[Zone]:
        with _lock:
            return [Zone.from_dict(z) for z in self._read()["zones"]]

    def get_zone(self, zone_id: str) -> Zone | None:
        return next((z for z in self.list_zones() if z.id == zone_id), None)

    def save_zone(self, zone: Zone) -> None:
        with _lock:
            data = self._read()
            data["zones"] = [x for x in data["zones"] if x["id"] != zone.id]
            data["zones"].append(zone.to_dict())
            self._write(data)

    def delete_zone(self, zone_id: str) -> None:
        self._delete_entity("zones", zone_id)

    # -- Analysers ----------------------------------------------------

    def list_analysers(self) -> list[Analyser]:
        with _lock:
            return [Analyser.from_dict(a) for a in self._read()["analysers"]]

    def get_analyser(self, ac_id: str) -> Analyser | None:
        return next((a for a in self.list_analysers() if a.id == ac_id), None)

    def save_analyser(self, ac: Analyser) -> None:
        with _lock:
            data = self._read()
            data["analysers"] = [x for x in data["analysers"] if x["id"] != ac.id]
            data["analysers"].append(ac.to_dict())
            self._write(data)

    def delete_analyser(self, ac_id: str) -> None:
        self._delete_entity("analysers", ac_id)

    # -- Effects ------------------------------------------------------------

    def list_effects(self) -> list[Effect]:
        with _lock:
            return [Effect.from_dict(s) for s in self._read()["effects"]]

    def get_effect(self, effect_id: str) -> Effect | None:
        return next((e for e in self.list_effects() if e.id == effect_id), None)

    def save_effect(self, effect: Effect) -> None:
        with _lock:
            data = self._read()
            data["effects"] = [x for x in data["effects"] if x["id"] != effect.id]
            data["effects"].append(effect.to_dict())
            self._write(data)

    def delete_effect(self, effect_id: str) -> None:
        self._delete_entity("effects", effect_id)

    # -- EnergyProfiles ----------------------------------------------------

    def list_energy_profiles(self) -> list[EnergyProfile]:
        with _lock:
            return [EnergyProfile.from_dict(x) for x in self._read()["energy_profiles"]]

    def get_energy_profile(self, ep_id: str) -> EnergyProfile | None:
        return next((x for x in self.list_energy_profiles() if x.id == ep_id), None)

    def save_energy_profile(self, ep: EnergyProfile) -> None:
        with _lock:
            data = self._read()
            data["energy_profiles"] = [x for x in data["energy_profiles"] if x["id"] != ep.id]
            data["energy_profiles"].append(ep.to_dict())
            self._write(data)

    def delete_energy_profile(self, ep_id: str) -> None:
        self._delete_entity("energy_profiles", ep_id)

    # -- Couplings ----------------------------------------------------------

    def list_couplings(self) -> list[Coupling]:
        with _lock:
            return [Coupling.from_dict(c) for c in self._read()["couplings"]]

    def get_coupling(self, coupling_id: str) -> Coupling | None:
        return next((c for c in self.list_couplings() if c.id == coupling_id), None)

    def save_coupling(self, coupling: Coupling) -> None:
        with _lock:
            data = self._read()
            data["couplings"] = [c for c in data["couplings"] if c["id"] != coupling.id]
            data["couplings"].append(coupling.to_dict())
            self._write(data)

    def delete_coupling(self, coupling_id: str) -> None:
        with _lock:
            data = self._read()
            data["couplings"] = [c for c in data["couplings"] if c["id"] != coupling_id]
            if data.get("active_coupling_id") == coupling_id:
                data["active_coupling_id"] = None
            self._write(data)

    # -- Active coupling ----------------------------------------------------

    def get_active_coupling_id(self) -> str | None:
        with _lock:
            return self._read().get("active_coupling_id")

    def set_active_coupling_id(self, coupling_id: str | None) -> None:
        with _lock:
            data = self._read()
            data["active_coupling_id"] = coupling_id
            self._write(data)
