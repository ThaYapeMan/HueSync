"""Persistence layer.

Deliberately a single JSON file rather than a database: HueSync manages at
most a handful of profiles and bridges, so a flat file is easier to inspect,
back up, and diff than a SQLite schema - and it's trivial to hand-edit if
something ever needs fixing outside the GUI.

A simple file lock avoids corruption if the API and a background task write
concurrently (unlikely at this scale, but cheap to guard against).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .models import (
    AnalysisConfig,
    BridgeConfig,
    Controller,
    Coupling,
    LightProvider,
    Player,
    PlayerLatency,
    Profile,
    RenderConfig,
)

_lock = threading.Lock()


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(
                {
                    "bridges": [],
                    "profiles": [],
                    "player_latencies": [],
                    "active_profile_id": None,
                    # Phase-2 collections
                    "controllers": [],
                    "players": [],
                    "light_providers": [],
                    "analysis_configs": [],
                    "render_configs": [],
                    "couplings": [],
                    "active_coupling_id": None,
                }
            )

    def _read(self) -> dict:
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Back-fill any top-level keys added after the initial file was written.
        data.setdefault("player_latencies", [])
        data.setdefault("controllers", [])
        data.setdefault("players", [])
        data.setdefault("light_providers", [])
        data.setdefault("analysis_configs", [])
        data.setdefault("render_configs", [])
        data.setdefault("couplings", [])
        data.setdefault("active_coupling_id", None)
        return data

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp.replace(self.path)

    # -- Bridges ----------------------------------------------------------

    def list_bridges(self) -> list[BridgeConfig]:
        with _lock:
            return [BridgeConfig.from_dict(b) for b in self._read()["bridges"]]

    def get_bridge(self, bridge_id: str) -> BridgeConfig | None:
        return next((b for b in self.list_bridges() if b.id == bridge_id), None)

    def save_bridge(self, bridge: BridgeConfig) -> None:
        with _lock:
            data = self._read()
            data["bridges"] = [b for b in data["bridges"] if b["id"] != bridge.id]
            data["bridges"].append(bridge.to_dict())
            self._write(data)

    def delete_bridge(self, bridge_id: str) -> None:
        with _lock:
            data = self._read()
            data["bridges"] = [b for b in data["bridges"] if b["id"] != bridge_id]
            self._write(data)

    # -- Profiles -----------------------------------------------------------

    def list_profiles(self) -> list[Profile]:
        with _lock:
            return [Profile.from_dict(p) for p in self._read()["profiles"]]

    def get_profile(self, profile_id: str) -> Profile | None:
        return next((p for p in self.list_profiles() if p.id == profile_id), None)

    def save_profile(self, profile: Profile) -> None:
        with _lock:
            data = self._read()
            data["profiles"] = [p for p in data["profiles"] if p["id"] != profile.id]
            data["profiles"].append(profile.to_dict())
            self._write(data)

    def delete_profile(self, profile_id: str) -> None:
        with _lock:
            data = self._read()
            data["profiles"] = [p for p in data["profiles"] if p["id"] != profile_id]
            if data.get("active_profile_id") == profile_id:
                data["active_profile_id"] = None
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

    # -- Active profile -----------------------------------------------------

    def get_active_profile_id(self) -> str | None:
        with _lock:
            return self._read().get("active_profile_id")

    def set_active_profile_id(self, profile_id: str | None) -> None:
        with _lock:
            data = self._read()
            data["active_profile_id"] = profile_id
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
        with _lock:
            data = self._read()
            data["controllers"] = [c for c in data["controllers"] if c["id"] != controller_id]
            self._write(data)

    # -- Players ------------------------------------------------------------

    def list_players(self) -> list[Player]:
        with _lock:
            return [Player.from_dict(p) for p in self._read()["players"]]

    def get_player(self, player_id: str) -> Player | None:
        return next((p for p in self.list_players() if p.id == player_id), None)

    def save_player(self, player: Player) -> None:
        with _lock:
            data = self._read()
            data["players"] = [p for p in data["players"] if p["id"] != player.id]
            data["players"].append(player.to_dict())
            self._write(data)

    def delete_player(self, player_id: str) -> None:
        with _lock:
            data = self._read()
            data["players"] = [p for p in data["players"] if p["id"] != player_id]
            self._write(data)

    # -- LightProviders -----------------------------------------------------

    def list_light_providers(self) -> list[LightProvider]:
        with _lock:
            return [LightProvider.from_dict(lp) for lp in self._read()["light_providers"]]

    def get_light_provider(self, lp_id: str) -> LightProvider | None:
        return next((lp for lp in self.list_light_providers() if lp.id == lp_id), None)

    def save_light_provider(self, lp: LightProvider) -> None:
        with _lock:
            data = self._read()
            data["light_providers"] = [x for x in data["light_providers"] if x["id"] != lp.id]
            data["light_providers"].append(lp.to_dict())
            self._write(data)

    def delete_light_provider(self, lp_id: str) -> None:
        with _lock:
            data = self._read()
            data["light_providers"] = [x for x in data["light_providers"] if x["id"] != lp_id]
            self._write(data)

    # -- AnalysisConfigs ----------------------------------------------------

    def list_analysis_configs(self) -> list[AnalysisConfig]:
        with _lock:
            return [AnalysisConfig.from_dict(a) for a in self._read()["analysis_configs"]]

    def get_analysis_config(self, ac_id: str) -> AnalysisConfig | None:
        return next((a for a in self.list_analysis_configs() if a.id == ac_id), None)

    def save_analysis_config(self, ac: AnalysisConfig) -> None:
        with _lock:
            data = self._read()
            data["analysis_configs"] = [x for x in data["analysis_configs"] if x["id"] != ac.id]
            data["analysis_configs"].append(ac.to_dict())
            self._write(data)

    def delete_analysis_config(self, ac_id: str) -> None:
        with _lock:
            data = self._read()
            data["analysis_configs"] = [x for x in data["analysis_configs"] if x["id"] != ac_id]
            self._write(data)

    # -- RenderConfigs ------------------------------------------------------

    def list_render_configs(self) -> list[RenderConfig]:
        with _lock:
            return [RenderConfig.from_dict(r) for r in self._read()["render_configs"]]

    def get_render_config(self, rc_id: str) -> RenderConfig | None:
        return next((r for r in self.list_render_configs() if r.id == rc_id), None)

    def save_render_config(self, rc: RenderConfig) -> None:
        with _lock:
            data = self._read()
            data["render_configs"] = [x for x in data["render_configs"] if x["id"] != rc.id]
            data["render_configs"].append(rc.to_dict())
            self._write(data)

    def delete_render_config(self, rc_id: str) -> None:
        with _lock:
            data = self._read()
            data["render_configs"] = [x for x in data["render_configs"] if x["id"] != rc_id]
            self._write(data)

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
