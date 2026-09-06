from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __git_hash__, __version__
from .api import router as api_router
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
    storage.set_active_coupling_id(None)
    # Remove any /dev/shm/squeezelite-* segments left by a previous crash.
    player_manager.cleanup_orphaned_shm()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await player_manager.deactivate()


# -- Live preview ---------------------------------------------------------------


@app.websocket("/ws/preview")
async def ws_preview(websocket: WebSocket):
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

            await websocket.send_json({
                "type": "frame",
                "colour": {"r": r, "g": g, "b": b},
                "onset": onset,
                "pcm_onset": player_manager.last_pcm_onset,
                "onset_bass": player_manager.last_onset_bass,
                "onset_mid": player_manager.last_onset_mid,
                "onset_treble": player_manager.last_onset_treble,
            })

            if tick % 3 == 0:
                await websocket.send_json({
                    "type": "spectrum",
                    "bars": player_manager.last_bars,
                })

            status_dict = {
                "type": "status",
                "version": f"{__version__}+{__git_hash__}",
                "active_coupling_id": player_manager.active_coupling_id,
                "active_coupling_name": player_manager.active_coupling_name,
                "sync_master": player_manager.detected_sync_master,
                "sync_master_name": player_manager.detected_sync_master_name,
                "applied_delay_ms": player_manager.applied_delay_ms,
                "latency_warning": player_manager.latency_warning,
                "processes": player_manager.process_status,
                "bridge_connected": player_manager.bridge_connected,
                "color_mode": player_manager.active_color_mode,
                "onset_method": player_manager.active_onset_method,
                "lower_cutoff_freq": player_manager.active_lower_cutoff_freq,
                "higher_cutoff_freq": player_manager.active_higher_cutoff_freq,
                "bass_hz": player_manager.active_bass_hz,
                "mid_hz": player_manager.active_mid_hz,
            }
            status_json = json.dumps(status_dict, sort_keys=True)
            if status_json != last_status_json:
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
