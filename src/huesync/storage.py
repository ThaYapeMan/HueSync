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
from pathlib import Path

from .models import (
    AnalysisConfig,
    Controller,
    Coupling,
    LightProvider,
    PlayerLatency,
    RenderConfig,
    VirtualPlayer,
)

_lock = threading.Lock()


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(
                {
                    "player_latencies": [],
                    # Five-entity model
                    "controllers": [],
                    "virtual_players": [],
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
        data.setdefault("light_providers", [])
        data.setdefault("analysis_configs", [])
        data.setdefault("render_configs", [])
        data.setdefault("couplings", [])
        data.setdefault("active_coupling_id", None)
        # Migrate old "players" key to "virtual_players" if present.
        if "players" in data and "virtual_players" not in data:
            data["virtual_players"] = data.pop("players")
        elif "players" in data:
            data.pop("players")
        data.setdefault("virtual_players", [])
        return data

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp.replace(self.path)

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
        with _lock:
            data = self._read()
            data["controllers"] = [c for c in data["controllers"] if c["id"] != controller_id]
            self._write(data)

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
        with _lock:
            data = self._read()
            data["virtual_players"] = [p for p in data["virtual_players"] if p["id"] != player_id]
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

    def migrate(self) -> None:
        """One-time data migration: create mellow RenderConfigs from mellow_colour_mode.

        Previous format stored mellow_colour_mode, mix_low_threshold,
        mix_high_threshold, and mix_ema_alpha on the RenderConfig entity.  The
        new format moves the mix params to the Coupling and gives each coupling
        its own mellow_render_config_id pointing to a separate RenderConfig.

        If a coupling already has mellow_render_config_id set it is skipped.
        """
        import uuid as _uuid

        with _lock:
            data = self._read()
            changed = False
            rc_by_id = {r["id"]: r for r in data.get("render_configs", [])}

            for c in data.get("couplings", []):
                if c.get("mellow_render_config_id"):
                    continue  # already migrated

                rc_id = c.get("render_config_id", "")
                rc_raw = rc_by_id.get(rc_id, {})

                # Move mix params from RC to coupling (if present in old RC data).
                for param, default in [
                    ("mix_low_threshold", 0.3),
                    ("mix_high_threshold", 0.7),
                    ("mix_ema_alpha", 0.1),
                ]:
                    if param not in c:
                        c[param] = rc_raw.get(param, default)

                mellow_cm = rc_raw.get("mellow_colour_mode", "")
                if mellow_cm and mellow_cm != rc_raw.get("color_mode", "spectrum_rgb"):
                    # Clone the RC with the mellow colour mode as a new entity.
                    mellow_id = str(_uuid.uuid4())
                    mellow_rc = dict(rc_raw)
                    mellow_rc["id"] = mellow_id
                    mellow_rc["name"] = mellow_rc.get("name", "Render Config") + " (Mellow)"
                    mellow_rc["color_mode"] = mellow_cm
                    for old_key in (
                        "mellow_colour_mode",
                        "mix_low_threshold",
                        "mix_high_threshold",
                        "mix_ema_alpha",
                    ):
                        mellow_rc.pop(old_key, None)
                    data["render_configs"].append(mellow_rc)
                    rc_by_id[mellow_id] = mellow_rc
                    c["mellow_render_config_id"] = mellow_id
                else:
                    # No distinct mellow mode: reuse the same RC for both layers.
                    c["mellow_render_config_id"] = rc_id
                changed = True

            if changed:
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
