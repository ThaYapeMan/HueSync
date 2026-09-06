"""JSON REST API for HueSync.

Mounted at /api via app.include_router(router).  State is injected through
request.app.state (storage and player_manager), following the same pattern
used by the HTML routes in app.py.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from . import __git_hash__, __version__, hue_bridge
from .lms_discovery import discover_lms
from .models import ColorMode, PlayerLatency, Profile
from .player_manager import PlayerManager
from .storage import Storage
from .util import generate_locally_administered_mac

_VERSION_STRING = f"{__version__}+{__git_hash__}"

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Request body models
# ---------------------------------------------------------------------------


class BridgePairBody(BaseModel):
    host: str
    name: str = "Hue Bridge"


class ProfileCreateBody(BaseModel):
    name: str
    lms_host: str
    lms_port: int = 3483
    player_name: str = "HueSync"
    bridge_id: str
    entertainment_area_id: str
    entertainment_area_name: str = ""
    color_mode: str = "spectrum_rgb"
    sensitivity: float = 1.0
    brightness_floor: float = 0.15
    bars: int = 30
    lower_cutoff_freq: int = 50
    higher_cutoff_freq: int = 12000
    bass_hz: int = 250
    mid_hz: int = 2000
    onset_delta: float = 0.1
    onset_alpha: float = 0.9
    exertion_clip: float = 3.0


class ProfilePatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    lms_host: str | None = None
    lms_port: int | None = None
    player_name: str | None = None
    alsa_device: str | None = None
    bridge_id: str | None = None
    entertainment_area_id: str | None = None
    entertainment_area_name: str | None = None
    light_count: int | None = None
    color_mode: str | None = None
    sensitivity: float | None = None
    brightness_floor: float | None = None
    bars: int | None = None
    lower_cutoff_freq: int | None = None
    higher_cutoff_freq: int | None = None
    bass_hz: int | None = None
    mid_hz: int | None = None
    onset_delta: float | None = None
    onset_alpha: float | None = None
    onset_method: str | None = None
    superflux_mu: int | None = None
    superflux_lag: int | None = None
    exertion_clip: float | None = None
    enabled: bool | None = None


class PlayerLatencyCreateBody(BaseModel):
    player_mac: str
    name: str | None = None
    strategy: str = "fixed"
    fixed_delay_ms: int = 2000
    speaker_ip: str | None = None


class PlayerLatencyPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    strategy: str | None = None
    fixed_delay_ms: int | None = None
    speaker_ip: str | None = None


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _storage(request: Request) -> Storage:
    return request.app.state.storage


def _manager(request: Request) -> PlayerManager:
    return request.app.state.player_manager


# ---------------------------------------------------------------------------
# Bridges
# ---------------------------------------------------------------------------


@router.get("/bridges")
async def list_bridges(request: Request):
    storage = _storage(request)
    return [b.to_dict() for b in storage.list_bridges()]


@router.post("/bridges/pair", status_code=201)
async def pair_bridge(request: Request, body: BridgePairBody):
    storage = _storage(request)
    try:
        bridge = await hue_bridge.pair(body.host, bridge_name=body.name)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "Pairing timed out. Press the physical link button on the bridge, "
                "then retry within ~30 seconds."
            ),
        ) from exc
    storage.save_bridge(bridge)
    return JSONResponse(content=bridge.to_dict(), status_code=201)


@router.delete("/bridges/{bridge_id}", status_code=204)
async def delete_bridge(bridge_id: str, request: Request):
    storage = _storage(request)
    storage.delete_bridge(bridge_id)


@router.get("/bridges/{bridge_id}/areas")
async def bridge_areas(bridge_id: str, request: Request):
    storage = _storage(request)
    bridge = storage.get_bridge(bridge_id)
    if bridge is None:
        raise HTTPException(status_code=404, detail="Bridge not found")
    areas = await hue_bridge.list_entertainment_areas(bridge)
    return [{"id": a.id, "name": a.name, "light_count": a.light_count} for a in areas]


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@router.get("/profiles")
async def list_profiles(request: Request):
    storage = _storage(request)
    return [p.to_dict() for p in storage.list_profiles()]


@router.post("/profiles", status_code=201)
async def create_profile(request: Request, body: ProfileCreateBody):
    storage = _storage(request)
    profile = Profile(
        name=body.name,
        lms_host=body.lms_host,
        lms_port=body.lms_port,
        player_name=body.player_name,
        bridge_id=body.bridge_id,
        entertainment_area_id=body.entertainment_area_id,
        entertainment_area_name=body.entertainment_area_name,
        color_mode=ColorMode(body.color_mode),
        sensitivity=body.sensitivity,
        brightness_floor=body.brightness_floor,
        bars=body.bars,
        lower_cutoff_freq=body.lower_cutoff_freq,
        higher_cutoff_freq=body.higher_cutoff_freq,
        bass_hz=body.bass_hz,
        mid_hz=body.mid_hz,
        onset_delta=body.onset_delta,
        onset_alpha=body.onset_alpha,
        exertion_clip=body.exertion_clip,
        player_mac=generate_locally_administered_mac(),
    )
    storage.save_profile(profile)
    return JSONResponse(content=profile.to_dict(), status_code=201)


# IMPORTANT: register /profiles/deactivate BEFORE /profiles/{profile_id} to
# prevent FastAPI routing the literal string "deactivate" as a profile ID.
@router.post("/profiles/deactivate")
async def deactivate_profile(request: Request):
    manager = _manager(request)
    await manager.deactivate()
    return {"active_id": None}


@router.get("/profiles/{profile_id}")
async def get_profile(profile_id: str, request: Request):
    storage = _storage(request)
    profile = storage.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile.to_dict()


@router.patch("/profiles/{profile_id}")
async def patch_profile(profile_id: str, request: Request, body: ProfilePatchBody):
    storage = _storage(request)
    manager = _manager(request)

    profile = storage.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    updates = body.model_dump(exclude_unset=True)

    was_active = manager.active_profile_id == profile_id

    if updates:
        for field, value in updates.items():
            if field == "color_mode":
                value = ColorMode(value)
            setattr(profile, field, value)

        if was_active:
            await manager.deactivate()

        storage.save_profile(profile)

    return profile.to_dict()


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: str, request: Request):
    storage = _storage(request)
    manager = _manager(request)
    if manager.active_profile_id == profile_id:
        await manager.deactivate()
    storage.delete_profile(profile_id)


class RestartCavaBody(BaseModel):
    """Optionally update frequency cutoffs and/or band boundaries while restarting cava.

    Saving these here (rather than via PATCH /profiles/{id}) avoids the
    patch_profile logic that deactivates the running session when any field
    changes on an active profile.  These changes only need a cava restart;
    squeezelite and the Hue session stay up.
    """

    lower_cutoff_freq: int | None = None
    higher_cutoff_freq: int | None = None
    bass_hz: int | None = None
    mid_hz: int | None = None


@router.post("/profiles/{profile_id}/restart-cava")
async def restart_cava(profile_id: str, request: Request, body: RestartCavaBody):
    manager = _manager(request)
    storage = _storage(request)

    if manager.active_profile_id != profile_id:
        raise HTTPException(status_code=400, detail="Profile is not active")

    has_updates = any(v is not None for v in (
        body.lower_cutoff_freq, body.higher_cutoff_freq, body.bass_hz, body.mid_hz
    ))
    if has_updates:
        profile = storage.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        if body.lower_cutoff_freq is not None:
            profile.lower_cutoff_freq = body.lower_cutoff_freq
        if body.higher_cutoff_freq is not None:
            profile.higher_cutoff_freq = body.higher_cutoff_freq
        if body.bass_hz is not None:
            profile.bass_hz = body.bass_hz
        if body.mid_hz is not None:
            profile.mid_hz = body.mid_hz
        storage.save_profile(profile)

    try:
        await manager.restart_cava()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/profiles/{profile_id}/activate")
async def activate_profile(profile_id: str, request: Request):
    storage = _storage(request)
    manager = _manager(request)

    profile = storage.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    try:
        await manager.activate(profile)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"active_id": profile_id, "warnings": []}


# ---------------------------------------------------------------------------
# Player latencies
# ---------------------------------------------------------------------------


@router.get("/player-latencies")
async def list_player_latencies(request: Request):
    storage = _storage(request)
    return [pl.to_dict() for pl in storage.list_player_latencies()]


@router.post("/player-latencies", status_code=201)
async def create_player_latency(request: Request, body: PlayerLatencyCreateBody):
    storage = _storage(request)
    manager = _manager(request)

    pl = PlayerLatency(
        player_mac=body.player_mac.strip().lower(),
        name=body.name,
        strategy=body.strategy,
        fixed_delay_ms=body.fixed_delay_ms,
        speaker_ip=body.speaker_ip,
    )
    storage.save_player_latency(pl)
    await manager.refresh_probe()
    return JSONResponse(content=pl.to_dict(), status_code=201)


@router.patch("/player-latencies/{player_mac}")
async def patch_player_latency(player_mac: str, request: Request, body: PlayerLatencyPatchBody):
    storage = _storage(request)
    manager = _manager(request)

    pl = storage.get_player_latency(player_mac)
    if pl is None:
        raise HTTPException(status_code=404, detail="Player latency config not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(pl, field, value)

    storage.save_player_latency(pl)
    await manager.refresh_probe()
    return pl.to_dict()


@router.delete("/player-latencies/{player_mac}", status_code=204)
async def delete_player_latency(player_mac: str, request: Request):
    storage = _storage(request)
    manager = _manager(request)
    storage.delete_player_latency(player_mac)
    await manager.refresh_probe()


# ---------------------------------------------------------------------------
# LMS discovery
# ---------------------------------------------------------------------------


@router.get("/lms/discover")
async def lms_discover():
    servers = await discover_lms(timeout=3.0)
    return [{"host": s.host, "name": s.name, "port": s.json_port} for s in servers]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_status(request: Request):
    manager = _manager(request)
    bars = manager.last_bars
    bars_stats = {
        "mean": round(sum(bars) / len(bars), 4) if bars else None,
        "max": round(max(bars), 4) if bars else None,
        "n": len(bars),
    }
    return {
        "version": _VERSION_STRING,
        "active_profile_id": manager.active_profile_id,
        "active_profile_name": manager.active_profile_name,
        "sync_master": manager.detected_sync_master,
        "sync_master_name": manager.detected_sync_master_name,
        "applied_delay_ms": manager.applied_delay_ms,
        "latency_warning": manager.latency_warning,
        "processes": manager.process_status,
        "bridge_connected": manager.bridge_connected,
        "bars_stats": bars_stats,
    }
