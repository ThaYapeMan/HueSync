from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import math
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __git_hash__, __version__
from .api import router as api_router
from .backup import configuration_lease
from .player_manager import PlayerManager
from .storage import Storage

_LOG_LEVEL = getattr(logging, os.environ.get("HUESYNC_LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(level=_LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
CONFIG_PATH = os.environ.get("HUESYNC_CONFIG", "/etc/huesync/config.json")

app = FastAPI(title="HueSync")

storage = Storage(CONFIG_PATH)
player_manager = PlayerManager(storage)

app.state.storage = storage
app.state.player_manager = player_manager

# API routes first so /api/* paths are claimed before the SPA catch-all.
app.include_router(api_router)


@app.on_event("startup")
async def on_startup() -> None:
    # A previously "active" coupling from before a restart has no real
    # squeezelite/cava process behind it anymore — clear the stale state
    # rather than pretending it's still running.
    app.state.configuration_mutation_lock = asyncio.Lock()
    lease = configuration_lease(app.state.storage.path)
    lease.__enter__()
    app.state.configuration_lease = lease
    try:
        app.state.storage.set_active_coupling_id(None)
        # Remove owned segments left by a previous crash.
        app.state.player_manager.cleanup_orphaned_shm()
    except BaseException:
        lease.__exit__(None, None, None)
        app.state.configuration_lease = None
        raise


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await app.state.player_manager.close()
    # Incomplete teardown retains the lease until shutdown succeeds or OS exit.
    lease = getattr(app.state, 'configuration_lease', None)
    if lease is not None:
        lease.__exit__(None, None, None)
        app.state.configuration_lease = None


# -- Live preview ---------------------------------------------------------------


@app.websocket("/ws/preview")
async def ws_preview(websocket: WebSocket):
    player_manager = websocket.app.state.player_manager
    await websocket.accept()
    last_status_json: str | None = None
    tick = 0
    try:
        while True:
            colours = player_manager.last_colours
            onset = player_manager.last_onset
            if colours:
                r, g, b = colours[0].to_16bit()
            else:
                r = g = b = 0

            channel_colours = [
                {"r": r16, "g": g16, "b": b16}
                for c in player_manager.last_colours
                for r16, g16, b16 in (c.to_16bit(),)
            ]
            momentary, short_term = player_manager.last_loudness
            await websocket.send_json({
                "type": "frame",
                "colour": {"r": r, "g": g, "b": b},
                "channel_colours": channel_colours,
                "onset": onset,
                "pcm_onset": player_manager.last_pcm_onset,
                "onset_bass": player_manager.last_onset_bass,
                "onset_mid": player_manager.last_onset_mid,
                "onset_treble": player_manager.last_onset_treble,
                "mix": player_manager.last_mix,
                "energy": player_manager.last_energy,
                "last_energy_input": player_manager.last_energy_input,
                "sustained_energy": player_manager.last_sustained_energy,
                "relative_exertion": player_manager.last_energy,
                # JSON has no infinity. Silence (-inf) and warmup are null.
                "loudness_momentary_lufs": (
                    momentary if momentary is not None and math.isfinite(momentary) else None
                ),
                "loudness_short_term_lufs": (
                    short_term if short_term is not None and math.isfinite(short_term) else None
                ),
            })

            if tick % 3 == 0:
                raw_bars, normalised_bars = player_manager.preview_spectrum
                await websocket.send_json({
                    "type": "spectrum",
                    "bars": raw_bars,
                    **({"normalised_bars": normalised_bars} if normalised_bars is not None else {}),
                })

            track = player_manager.track_position
            status_dict = {
                "type": "status",
                "track": dataclasses.asdict(track) if track is not None else None,
                "version": f"{__version__}+{__git_hash__}",
                "active_coupling_id": player_manager.active_coupling_id,
                "active_coupling_name": player_manager.active_coupling_name,
                "active_zone_id": player_manager.active_zone_id,
                "active_energy_profile_id": player_manager.active_energy_profile_id,
                "active_bars_source": player_manager.active_bars_source,
                "active_player_type": player_manager.active_player_type,
                "follow_target_mac": player_manager.follow_target_mac,
                "follow_target_name": player_manager.follow_target_name,
                "sync_master": player_manager.detected_sync_master,
                "sync_master_name": player_manager.detected_sync_master_name,
                "applied_delay_ms": player_manager.applied_delay_ms,
                "latency_warning": player_manager.latency_warning,
                "processes": player_manager.process_status,
                "bridge_connected": player_manager.bridge_connected,
                "effect_type": player_manager.active_effect,
                "follower_warning": player_manager.follower_warning,
                "onset_method": player_manager.active_onset_method,
                "lower_cutoff_freq": player_manager.active_lower_cutoff_freq,
                "higher_cutoff_freq": player_manager.active_higher_cutoff_freq,
                "bass_hz": player_manager.active_bass_hz,
                "mid_hz": player_manager.active_mid_hz,
            }
            status_json = json.dumps(status_dict, sort_keys=True)
            if status_json != last_status_json:
                # Rebase only when delivering a changed status, including first connect.
                # The comparison uses the stable source anchor, not a ticking clock.
                status_dict["track"] = track.for_delivery() if track is not None else None
                await websocket.send_json(status_dict)
                last_status_json = status_json

            tick += 1
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    with contextlib.suppress(Exception):
        await websocket.close()


# -- React SPA ------------------------------------------------------------------
# Mount strategy: serve /assets/* as plain static files so that StaticFiles
# never sees a WebSocket scope (it asserts scope["type"] == "http" and would
# crash on the /ws/preview upgrade).  Root and every other path return
# index.html so client-side routing works.

_WEBUI_DIR = BASE_DIR / "webui"

if _WEBUI_DIR.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_WEBUI_DIR / "assets")),
        name="assets",
    )

    @app.get("/")
    async def serve_root() -> FileResponse:
        return FileResponse(str(_WEBUI_DIR / "index.html"))

    @app.get("/{path:path}")
    async def serve_spa(path: str) -> FileResponse:  # noqa: ARG001
        return FileResponse(str(_WEBUI_DIR / "index.html"))


def main() -> None:
    uvicorn.run("huesync.app:app", host="0.0.0.0", port=8420, reload=False)


if __name__ == "__main__":
    main()
