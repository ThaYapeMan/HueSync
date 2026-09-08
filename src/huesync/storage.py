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
    Analyser,
    Controller,
    Coupling,
    Crossfader,
    PlayerLatency,
    Scene,
    VirtualPlayer,
    Zone,
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
                    "zones": [],
                    "analysers": [],
                    "scenes": [],
                    "crossfaders": [],
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
        data.setdefault("couplings", [])
        data.setdefault("active_coupling_id", None)
        data.setdefault("crossfaders", [])
        # Migrate old "players" key to "virtual_players" if present.
        if "players" in data and "virtual_players" not in data:
            data["virtual_players"] = data.pop("players")
        elif "players" in data:
            data.pop("players")
        data.setdefault("virtual_players", [])
        # Migrate old "light_providers" key to "zones" if present.
        if "light_providers" in data and "zones" not in data:
            data["zones"] = data.pop("light_providers")
        elif "light_providers" in data:
            data.pop("light_providers")
        data.setdefault("zones", [])
        # Migrate old "render_configs" key to "scenes" if present.
        if "render_configs" in data and "scenes" not in data:
            data["scenes"] = data.pop("render_configs")
        elif "render_configs" in data:
            data.pop("render_configs")
        data.setdefault("scenes", [])
        # Migrate old "analysis_configs" key to "analysers" if present.
        if "analysis_configs" in data and "analysers" not in data:
            data["analysers"] = data.pop("analysis_configs")
        elif "analysis_configs" in data:
            data.pop("analysis_configs")
        data.setdefault("analysers", [])
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
        with _lock:
            data = self._read()
            data["zones"] = [x for x in data["zones"] if x["id"] != zone_id]
            self._write(data)

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
        with _lock:
            data = self._read()
            data["analysers"] = [x for x in data["analysers"] if x["id"] != ac_id]
            self._write(data)

    # -- Scenes -------------------------------------------------------------

    def list_scenes(self) -> list[Scene]:
        with _lock:
            return [Scene.from_dict(s) for s in self._read()["scenes"]]

    def get_scene(self, scene_id: str) -> Scene | None:
        return next((s for s in self.list_scenes() if s.id == scene_id), None)

    def save_scene(self, scene: Scene) -> None:
        with _lock:
            data = self._read()
            data["scenes"] = [x for x in data["scenes"] if x["id"] != scene.id]
            data["scenes"].append(scene.to_dict())
            self._write(data)

    def delete_scene(self, scene_id: str) -> None:
        with _lock:
            data = self._read()
            data["scenes"] = [x for x in data["scenes"] if x["id"] != scene_id]
            self._write(data)

    # -- Crossfaders --------------------------------------------------------

    def list_crossfaders(self) -> list[Crossfader]:
        with _lock:
            return [Crossfader.from_dict(x) for x in self._read()["crossfaders"]]

    def get_crossfader(self, crossfader_id: str) -> Crossfader | None:
        return next((x for x in self.list_crossfaders() if x.id == crossfader_id), None)

    def save_crossfader(self, crossfader: Crossfader) -> None:
        with _lock:
            data = self._read()
            data["crossfaders"] = [x for x in data["crossfaders"] if x["id"] != crossfader.id]
            data["crossfaders"].append(crossfader.to_dict())
            self._write(data)

    def delete_crossfader(self, crossfader_id: str) -> None:
        with _lock:
            data = self._read()
            data["crossfaders"] = [x for x in data["crossfaders"] if x["id"] != crossfader_id]
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
        """Idempotent data migration: handles all format changes in one pass.

        Migrations applied (in order):
        1. mellow_colour_mode → separate Scene clone per coupling
        2. color_mode → effect rename in scene dicts
        3. render_configs key → scenes key
        4. light_providers key → zones key
        5. light_provider_id → zone_id on couplings
        6. render_config_id + mix fields → Crossfader entity per coupling
        7. analysis_config_id → analyser_id on couplings
        """
        import uuid as _uuid

        with _lock:
            data = self._read()
            changed = False

            # --- Step 1 & 2: legacy mellow_colour_mode + color_mode rename ---
            # Work on scenes list (already migrated from render_configs by _read).
            scene_by_id = {r["id"]: r for r in data.get("scenes", [])}

            for c in data.get("couplings", []):
                # Only run the old mellow migration if the coupling still has
                # render_config_id (not yet migrated to crossfader_id).
                if c.get("crossfader_id") or not c.get("render_config_id"):
                    continue
                if c.get("mellow_render_config_id"):
                    continue  # already done the mellow clone step

                rc_id = c.get("render_config_id", "")
                rc_raw = scene_by_id.get(rc_id, {})

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
                    mellow_rc["name"] = mellow_rc.get("name", "Scene") + " (Mellow)"
                    mellow_rc["color_mode"] = mellow_cm
                    for old_key in (
                        "mellow_colour_mode",
                        "mix_low_threshold",
                        "mix_high_threshold",
                        "mix_ema_alpha",
                    ):
                        mellow_rc.pop(old_key, None)
                    data["scenes"].append(mellow_rc)
                    scene_by_id[mellow_id] = mellow_rc
                    c["mellow_render_config_id"] = mellow_id
                else:
                    # No distinct mellow mode: reuse the same scene for both layers.
                    c["mellow_render_config_id"] = rc_id
                changed = True

            # Rename color_mode → effect in all scene dicts (after the
            # mellow-clone loop so newly created mellow scenes also get renamed).
            for scene in data.get("scenes", []):
                if "color_mode" in scene and "effect" not in scene:
                    scene["effect"] = scene.pop("color_mode")
                    changed = True
                elif "color_mode" in scene:
                    scene.pop("color_mode")
                    changed = True
                # Remove obsolete mellow_colour_mode if still present.
                if "mellow_colour_mode" in scene:
                    scene.pop("mellow_colour_mode")
                    changed = True

            # --- Step 3 & 4: key renames (handled by _read, normalise here) ---
            # _read() already moves the data; nothing extra to do here.

            # --- Step 5: light_provider_id → zone_id on couplings ---
            for c in data.get("couplings", []):
                if "light_provider_id" in c and "zone_id" not in c:
                    c["zone_id"] = c.pop("light_provider_id")
                    changed = True
                elif "light_provider_id" in c:
                    c.pop("light_provider_id")
                    changed = True

            # --- Step 6: render_config_id + mix fields → Crossfader per coupling ---
            # Create one Crossfader per coupling that has render_config_id but no crossfader_id.
            cf_by_id: dict = {cf["id"]: cf for cf in data.get("crossfaders", [])}

            for c in data.get("couplings", []):
                if c.get("crossfader_id"):
                    continue  # already migrated
                rc_id = c.pop("render_config_id", "")
                mellow_rc_id = c.pop("mellow_render_config_id", "")
                low = c.pop("mix_low_threshold", 0.3)
                high = c.pop("mix_high_threshold", 0.7)
                alpha = c.pop("mix_ema_alpha", 0.1)

                cf_id = str(_uuid.uuid4())
                cf = {
                    "id": cf_id,
                    "name": c.get("name", "Crossfader") + " Crossfader",
                    "active_scene_id": rc_id,
                    "mellow_scene_id": mellow_rc_id if mellow_rc_id != rc_id else "",
                    "low_threshold": low,
                    "high_threshold": high,
                    "fade_speed": alpha,
                }
                data.setdefault("crossfaders", []).append(cf)
                cf_by_id[cf_id] = cf
                c["crossfader_id"] = cf_id
                changed = True

            # --- Step 7: analysis_config_id → analyser_id on couplings ---
            for c in data.get("couplings", []):
                if "analysis_config_id" in c and "analyser_id" not in c:
                    c["analyser_id"] = c.pop("analysis_config_id")
                    changed = True
                elif "analysis_config_id" in c:
                    c.pop("analysis_config_id")
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
