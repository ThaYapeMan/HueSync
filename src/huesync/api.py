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
from .models import (
    AnalysisConfig,
    ColorMode,
    Controller,
    ControllerType,
    Coupling,
    LightProvider,
    Player,
    PlayerLatency,
    Profile,
    RenderConfig,
)
from .player_manager import PlayerManager, build_profile_from_coupling
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


class ControllerCreateBody(BaseModel):
    name: str = "Controller"
    type: str = "hue"
    host: str
    app_key: str = ""
    client_key: str = ""


class ControllerPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    host: str | None = None
    app_key: str | None = None
    client_key: str | None = None


class PlayerCreateBody(BaseModel):
    name: str = "HueSync Player"
    lms_host: str
    lms_port: int = 9000
    player_name: str = "HueSync"
    player_mac: str = ""
    alsa_device: str = ""


class PlayerPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    lms_host: str | None = None
    lms_port: int | None = None
    player_name: str | None = None
    alsa_device: str | None = None


class LightProviderCreateBody(BaseModel):
    name: str = "Light Provider"
    controller_id: str
    entertainment_area_id: str
    entertainment_area_name: str = ""
    light_count: int = 0


class LightProviderPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    entertainment_area_name: str | None = None
    light_count: int | None = None


class AnalysisConfigCreateBody(BaseModel):
    name: str = "Default Analysis"
    onset_method: str = "combined"
    onset_delta: float = 0.1
    onset_alpha: float = 0.9
    superflux_mu: int = 3
    superflux_lag: int = 2
    bars: int = 30
    lower_cutoff_freq: int = 50
    higher_cutoff_freq: int = 12000


class AnalysisConfigPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    onset_method: str | None = None
    onset_delta: float | None = None
    onset_alpha: float | None = None
    superflux_mu: int | None = None
    superflux_lag: int | None = None
    bars: int | None = None
    lower_cutoff_freq: int | None = None
    higher_cutoff_freq: int | None = None


class RenderConfigCreateBody(BaseModel):
    name: str = "Default Render"
    color_mode: str = "spectrum_rgb"
    sensitivity: float = 1.0
    brightness_floor: float = 0.15
    bass_hz: int = 250
    mid_hz: int = 2000
    exertion_clip: float = 3.0


class RenderConfigPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    color_mode: str | None = None
    sensitivity: float | None = None
    brightness_floor: float | None = None
    bass_hz: int | None = None
    mid_hz: int | None = None
    exertion_clip: float | None = None


class CouplingCreateBody(BaseModel):
    name: str
    player_id: str
    analysis_config_id: str
    light_provider_id: str
    render_config_id: str
    enabled: bool = True


class CouplingPatchBody(BaseModel):
    """Omnibus PATCH body; routes each field to the appropriate sub-entity.

    Priority categories (high to low):
      FK/player fields → deactivate
      cava fields      → restart_cava
      pcm fields       → update_onset_pipeline
      render fields    → update_render
    """

    model_config = ConfigDict(extra="forbid")
    # Coupling meta (no session action)
    name: str | None = None
    enabled: bool | None = None
    # FK rewiring (deactivate if active)
    player_id: str | None = None
    analysis_config_id: str | None = None
    light_provider_id: str | None = None
    render_config_id: str | None = None
    # Player inline (deactivate if active)
    lms_host: str | None = None
    lms_port: int | None = None
    player_name: str | None = None
    alsa_device: str | None = None
    # AnalysisConfig cava category (restart_cava)
    bars: int | None = None
    lower_cutoff_freq: int | None = None
    higher_cutoff_freq: int | None = None
    # AnalysisConfig pcm category (update_onset_pipeline)
    onset_method: str | None = None
    onset_delta: float | None = None
    onset_alpha: float | None = None
    superflux_mu: int | None = None
    superflux_lag: int | None = None
    # RenderConfig fields (update_render)
    color_mode: str | None = None
    sensitivity: float | None = None
    brightness_floor: float | None = None
    exertion_clip: float | None = None
    # bass_hz/mid_hz: stored in RenderConfig but pcm-category for restart
    bass_hz: int | None = None
    mid_hz: int | None = None
    # LightProvider render fields
    entertainment_area_name: str | None = None
    light_count: int | None = None


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Field categories for patch_profile routing
# ---------------------------------------------------------------------------
#
# Priority order (most to least disruptive):
#   _PLAYER_FIELDS > _CAVA_FIELDS > _PCM_FIELDS > _RENDER_FIELDS
#
# A field not listed in any set (e.g. "name") is treated as pure metadata:
# it is saved to storage but triggers no process-level action on an active
# session.

_PLAYER_FIELDS: frozenset[str] = frozenset({
    "lms_host", "lms_port", "player_name", "player_mac", "alsa_device",
    "bridge_id", "entertainment_area_id",
})
_CAVA_FIELDS: frozenset[str] = frozenset({
    "bars", "lower_cutoff_freq", "higher_cutoff_freq",
})
_PCM_FIELDS: frozenset[str] = frozenset({
    "onset_method", "onset_delta", "onset_alpha", "superflux_mu", "superflux_lag",
    "bass_hz", "mid_hz",  # also affect MultibandStftPipeline band boundaries
})
_RENDER_FIELDS: frozenset[str] = frozenset({
    "color_mode", "sensitivity", "brightness_floor", "exertion_clip", "enabled",
    "entertainment_area_name", "light_count",
})
_ALL_ACTIVE_FIELDS: frozenset[str] = (
    _PLAYER_FIELDS | _CAVA_FIELDS | _PCM_FIELDS | _RENDER_FIELDS
)


# ---------------------------------------------------------------------------
# Phase-2 field categories for coupling PATCH routing
# ---------------------------------------------------------------------------
# These parallel the Profile-level _PLAYER_FIELDS etc. but map to sub-entities.
# Priority: deactivate > cava > pcm > render.

_C_DEACTIVATE_FIELDS: frozenset[str] = frozenset({
    "player_id", "analysis_config_id", "light_provider_id", "render_config_id",
    "lms_host", "lms_port", "player_name", "alsa_device",
})
_C_CAVA_FIELDS: frozenset[str] = frozenset({
    "bars", "lower_cutoff_freq", "higher_cutoff_freq",
})
_C_PCM_FIELDS: frozenset[str] = frozenset({
    "onset_method", "onset_delta", "onset_alpha", "superflux_mu", "superflux_lag",
    "bass_hz", "mid_hz",
})
_C_RENDER_FIELDS: frozenset[str] = frozenset({
    "color_mode", "sensitivity", "brightness_floor", "exertion_clip",
    "entertainment_area_name", "light_count",
})

# Which sub-entity owns each inline field in CouplingPatchBody
_C_PLAYER_INLINE: frozenset[str] = frozenset({
    "lms_host", "lms_port", "player_name", "alsa_device",
})
_C_AC_INLINE: frozenset[str] = frozenset({
    "bars", "lower_cutoff_freq", "higher_cutoff_freq",
    "onset_method", "onset_delta", "onset_alpha", "superflux_mu", "superflux_lag",
})
_C_RC_INLINE: frozenset[str] = frozenset({
    "color_mode", "sensitivity", "brightness_floor", "exertion_clip",
    "bass_hz", "mid_hz",
})
_C_LP_INLINE: frozenset[str] = frozenset({"entertainment_area_name", "light_count"})
_C_FK_FIELDS: frozenset[str] = frozenset({
    "player_id", "analysis_config_id", "light_provider_id", "render_config_id",
})


async def _apply_coupling_action(
    coupling: Coupling,
    storage: Storage,
    manager: PlayerManager,
    changed: set[str],
) -> None:
    """Rebuild Profile from entities and call the right PlayerManager action.

    Called after entity saves so the Profile reflects the updated values.
    Uses build_profile_from_coupling from player_manager (single source of truth).
    """
    profile = build_profile_from_coupling(coupling, storage)
    if not profile:
        return
    storage.save_profile(profile)

    if changed & _C_DEACTIVATE_FIELDS:
        await manager.deactivate()
        return
    if changed & _C_CAVA_FIELDS:
        await manager.restart_cava()
    if changed & _C_PCM_FIELDS:
        manager.update_onset_pipeline(profile)
    manager.update_render(profile)


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

        # Save before taking any action so that restart_cava() — which reads
        # the profile from storage — sees the updated values.
        storage.save_profile(profile)

        if was_active:
            changed = set(updates.keys())
            if changed & _ALL_ACTIVE_FIELDS:
                if changed & _PLAYER_FIELDS:
                    # Full teardown: squeezelite, cava, and the Hue DTLS
                    # session must all be rebuilt with the new player params.
                    await manager.deactivate()
                else:
                    # Non-deactivating updates: apply the appropriate restart
                    # for the most disruptive changed field, then always sync
                    # render settings.  update_render() is always called so
                    # that exertion_clip reaches BandNormaliser and so that
                    # bass_hz/mid_hz (which affect both the PCM pipeline and
                    # ColourModeEffect) are applied to both layers.
                    if changed & _CAVA_FIELDS:
                        await manager.restart_cava()
                    if changed & _PCM_FIELDS:
                        manager.update_onset_pipeline(profile)
                    manager.update_render(profile)

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


# ---------------------------------------------------------------------------
# Controllers
# ---------------------------------------------------------------------------


@router.get("/controllers")
async def list_controllers(request: Request):
    return [c.to_dict() for c in _storage(request).list_controllers()]


@router.post("/controllers", status_code=201)
async def create_controller(request: Request, body: ControllerCreateBody):
    storage = _storage(request)
    try:
        ct = ControllerType(body.type)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Unknown controller type: {body.type!r}"
        ) from exc
    controller = Controller(
        name=body.name,
        type=ct,
        host=body.host,
        app_key=body.app_key,
        client_key=body.client_key,
    )
    storage.save_controller(controller)
    return JSONResponse(content=controller.to_dict(), status_code=201)


@router.get("/controllers/{controller_id}")
async def get_controller(controller_id: str, request: Request):
    c = _storage(request).get_controller(controller_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Controller not found")
    return c.to_dict()


@router.patch("/controllers/{controller_id}")
async def patch_controller(controller_id: str, request: Request, body: ControllerPatchBody):
    storage = _storage(request)
    c = storage.get_controller(controller_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Controller not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(c, field, value)
    storage.save_controller(c)
    return c.to_dict()


@router.delete("/controllers/{controller_id}", status_code=204)
async def delete_controller(controller_id: str, request: Request):
    _storage(request).delete_controller(controller_id)


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


@router.get("/players")
async def list_players(request: Request):
    return [p.to_dict() for p in _storage(request).list_players()]


@router.post("/players", status_code=201)
async def create_player(request: Request, body: PlayerCreateBody):
    storage = _storage(request)
    mac = body.player_mac or generate_locally_administered_mac()
    player = Player(
        name=body.name,
        lms_host=body.lms_host,
        lms_port=body.lms_port,
        player_name=body.player_name,
        player_mac=mac,
        alsa_device=body.alsa_device,
    )
    storage.save_player(player)
    return JSONResponse(content=player.to_dict(), status_code=201)


@router.get("/players/{player_id}")
async def get_player(player_id: str, request: Request):
    p = _storage(request).get_player(player_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return p.to_dict()


@router.patch("/players/{player_id}")
async def patch_player(player_id: str, request: Request, body: PlayerPatchBody):
    storage = _storage(request)
    manager = _manager(request)
    player = storage.get_player(player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(player, field, value)
    storage.save_player(player)
    # If active coupling uses this player and active fields changed, deactivate.
    active_id = storage.get_active_coupling_id()
    if updates and active_id:
        coupling = storage.get_coupling(active_id)
        active_fields = set(updates.keys()) - {"name"}
        if coupling and coupling.player_id == player_id and active_fields:
            await _apply_coupling_action(coupling, storage, manager, active_fields)
    return player.to_dict()


@router.delete("/players/{player_id}", status_code=204)
async def delete_player(player_id: str, request: Request):
    _storage(request).delete_player(player_id)


# ---------------------------------------------------------------------------
# LightProviders
# ---------------------------------------------------------------------------


@router.get("/light-providers")
async def list_light_providers(request: Request):
    return [lp.to_dict() for lp in _storage(request).list_light_providers()]


@router.post("/light-providers", status_code=201)
async def create_light_provider(request: Request, body: LightProviderCreateBody):
    storage = _storage(request)
    lp = LightProvider(
        name=body.name,
        controller_id=body.controller_id,
        entertainment_area_id=body.entertainment_area_id,
        entertainment_area_name=body.entertainment_area_name,
        light_count=body.light_count,
    )
    storage.save_light_provider(lp)
    return JSONResponse(content=lp.to_dict(), status_code=201)


@router.get("/light-providers/{lp_id}")
async def get_light_provider(lp_id: str, request: Request):
    lp = _storage(request).get_light_provider(lp_id)
    if lp is None:
        raise HTTPException(status_code=404, detail="LightProvider not found")
    return lp.to_dict()


@router.patch("/light-providers/{lp_id}")
async def patch_light_provider(lp_id: str, request: Request, body: LightProviderPatchBody):
    storage = _storage(request)
    manager = _manager(request)
    lp = storage.get_light_provider(lp_id)
    if lp is None:
        raise HTTPException(status_code=404, detail="LightProvider not found")
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(lp, field, value)
    storage.save_light_provider(lp)
    # entertainment_area_name and light_count are render-category changes.
    active_id = storage.get_active_coupling_id()
    render_changed = set(updates.keys()) & _C_RENDER_FIELDS
    if render_changed and active_id:
        coupling = storage.get_coupling(active_id)
        if coupling and coupling.light_provider_id == lp_id:
            await _apply_coupling_action(coupling, storage, manager, render_changed)
    return lp.to_dict()


@router.delete("/light-providers/{lp_id}", status_code=204)
async def delete_light_provider(lp_id: str, request: Request):
    _storage(request).delete_light_provider(lp_id)


# ---------------------------------------------------------------------------
# AnalysisConfigs
# ---------------------------------------------------------------------------


@router.get("/analysis-configs")
async def list_analysis_configs(request: Request):
    return [ac.to_dict() for ac in _storage(request).list_analysis_configs()]


@router.post("/analysis-configs", status_code=201)
async def create_analysis_config(request: Request, body: AnalysisConfigCreateBody):
    storage = _storage(request)
    ac = AnalysisConfig(
        name=body.name,
        onset_method=body.onset_method,
        onset_delta=body.onset_delta,
        onset_alpha=body.onset_alpha,
        superflux_mu=body.superflux_mu,
        superflux_lag=body.superflux_lag,
        bars=body.bars,
        lower_cutoff_freq=body.lower_cutoff_freq,
        higher_cutoff_freq=body.higher_cutoff_freq,
    )
    storage.save_analysis_config(ac)
    return JSONResponse(content=ac.to_dict(), status_code=201)


@router.get("/analysis-configs/{ac_id}")
async def get_analysis_config(ac_id: str, request: Request):
    ac = _storage(request).get_analysis_config(ac_id)
    if ac is None:
        raise HTTPException(status_code=404, detail="AnalysisConfig not found")
    return ac.to_dict()


@router.patch("/analysis-configs/{ac_id}")
async def patch_analysis_config(ac_id: str, request: Request, body: AnalysisConfigPatchBody):
    storage = _storage(request)
    manager = _manager(request)
    ac = storage.get_analysis_config(ac_id)
    if ac is None:
        raise HTTPException(status_code=404, detail="AnalysisConfig not found")
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(ac, field, value)
    storage.save_analysis_config(ac)
    # Trigger cava/pcm restart if the active coupling uses this AnalysisConfig.
    active_id = storage.get_active_coupling_id()
    active_fields = set(updates.keys()) - {"name"}
    if active_fields and active_id:
        coupling = storage.get_coupling(active_id)
        if coupling and coupling.analysis_config_id == ac_id:
            await _apply_coupling_action(coupling, storage, manager, active_fields)
    return ac.to_dict()


@router.delete("/analysis-configs/{ac_id}", status_code=204)
async def delete_analysis_config(ac_id: str, request: Request):
    _storage(request).delete_analysis_config(ac_id)


# ---------------------------------------------------------------------------
# RenderConfigs
# ---------------------------------------------------------------------------


@router.get("/render-configs")
async def list_render_configs(request: Request):
    return [rc.to_dict() for rc in _storage(request).list_render_configs()]


@router.post("/render-configs", status_code=201)
async def create_render_config(request: Request, body: RenderConfigCreateBody):
    storage = _storage(request)
    try:
        cm = ColorMode(body.color_mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Unknown color_mode: {body.color_mode!r}"
        ) from exc
    rc = RenderConfig(
        name=body.name,
        color_mode=cm,
        sensitivity=body.sensitivity,
        brightness_floor=body.brightness_floor,
        bass_hz=body.bass_hz,
        mid_hz=body.mid_hz,
        exertion_clip=body.exertion_clip,
    )
    storage.save_render_config(rc)
    return JSONResponse(content=rc.to_dict(), status_code=201)


@router.get("/render-configs/{rc_id}")
async def get_render_config(rc_id: str, request: Request):
    rc = _storage(request).get_render_config(rc_id)
    if rc is None:
        raise HTTPException(status_code=404, detail="RenderConfig not found")
    return rc.to_dict()


@router.patch("/render-configs/{rc_id}")
async def patch_render_config(rc_id: str, request: Request, body: RenderConfigPatchBody):
    storage = _storage(request)
    manager = _manager(request)
    rc = storage.get_render_config(rc_id)
    if rc is None:
        raise HTTPException(status_code=404, detail="RenderConfig not found")
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if field == "color_mode":
            value = ColorMode(value)
        setattr(rc, field, value)
    storage.save_render_config(rc)
    # Trigger render/pcm update if the active coupling uses this RenderConfig.
    active_id = storage.get_active_coupling_id()
    active_fields = set(updates.keys()) - {"name"}
    if active_fields and active_id:
        coupling = storage.get_coupling(active_id)
        if coupling and coupling.render_config_id == rc_id:
            await _apply_coupling_action(coupling, storage, manager, active_fields)
    return rc.to_dict()


@router.delete("/render-configs/{rc_id}", status_code=204)
async def delete_render_config(rc_id: str, request: Request):
    _storage(request).delete_render_config(rc_id)


# ---------------------------------------------------------------------------
# Couplings
# ---------------------------------------------------------------------------


@router.get("/couplings")
async def list_couplings(request: Request):
    return [c.to_dict() for c in _storage(request).list_couplings()]


@router.post("/couplings", status_code=201)
async def create_coupling(request: Request, body: CouplingCreateBody):
    storage = _storage(request)
    coupling = Coupling(
        name=body.name,
        player_id=body.player_id,
        analysis_config_id=body.analysis_config_id,
        light_provider_id=body.light_provider_id,
        render_config_id=body.render_config_id,
        enabled=body.enabled,
    )
    storage.save_coupling(coupling)
    return JSONResponse(content=coupling.to_dict(), status_code=201)


# Register /couplings/deactivate BEFORE /couplings/{coupling_id} so FastAPI
# does not route the literal string "deactivate" as a coupling ID.
@router.post("/couplings/deactivate")
async def deactivate_coupling(request: Request):
    manager = _manager(request)
    storage = _storage(request)
    await manager.deactivate()
    storage.set_active_coupling_id(None)
    return {"active_id": None}


@router.get("/couplings/{coupling_id}")
async def get_coupling(coupling_id: str, request: Request):
    c = _storage(request).get_coupling(coupling_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Coupling not found")
    return c.to_dict()


@router.patch("/couplings/{coupling_id}")
async def patch_coupling(coupling_id: str, request: Request, body: CouplingPatchBody):
    """Omnibus PATCH: routes each field to the appropriate sub-entity and
    triggers the minimum necessary session action on the active coupling."""
    storage = _storage(request)
    manager = _manager(request)

    coupling = storage.get_coupling(coupling_id)
    if coupling is None:
        raise HTTPException(status_code=404, detail="Coupling not found")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return coupling.to_dict()

    was_active = storage.get_active_coupling_id() == coupling_id

    # Resolve sub-entities for inline field updates
    player = storage.get_player(coupling.player_id) if coupling.player_id else None
    ac = (
        storage.get_analysis_config(coupling.analysis_config_id)
        if coupling.analysis_config_id
        else None
    )
    rc = (
        storage.get_render_config(coupling.render_config_id)
        if coupling.render_config_id
        else None
    )
    lp = (
        storage.get_light_provider(coupling.light_provider_id)
        if coupling.light_provider_id
        else None
    )

    player_changed = ac_changed = rc_changed = lp_changed = False

    for field, value in updates.items():
        if field in {"name", "enabled"} | _C_FK_FIELDS:
            setattr(coupling, field, value)
        elif field in _C_PLAYER_INLINE and player:
            setattr(player, field, value)
            player_changed = True
        elif field in _C_AC_INLINE and ac:
            setattr(ac, field, value)
            ac_changed = True
        elif field in _C_RC_INLINE and rc:
            if field == "color_mode":
                value = ColorMode(value)
            setattr(rc, field, value)
            rc_changed = True
        elif field in _C_LP_INLINE and lp:
            setattr(lp, field, value)
            lp_changed = True

    storage.save_coupling(coupling)
    if player_changed and player:
        storage.save_player(player)
    if ac_changed and ac:
        storage.save_analysis_config(ac)
    if rc_changed and rc:
        storage.save_render_config(rc)
    if lp_changed and lp:
        storage.save_light_provider(lp)

    if was_active:
        changed = set(updates.keys())
        if changed & (_C_DEACTIVATE_FIELDS | _C_CAVA_FIELDS | _C_PCM_FIELDS | _C_RENDER_FIELDS):
            await _apply_coupling_action(coupling, storage, manager, changed)

    return coupling.to_dict()


@router.delete("/couplings/{coupling_id}", status_code=204)
async def delete_coupling(coupling_id: str, request: Request):
    storage = _storage(request)
    manager = _manager(request)
    if storage.get_active_coupling_id() == coupling_id:
        await manager.deactivate()
    storage.delete_coupling(coupling_id)


@router.post("/couplings/{coupling_id}/activate")
async def activate_coupling(coupling_id: str, request: Request):
    """Activate a Coupling via PlayerManager.activate_coupling()."""
    storage = _storage(request)
    manager = _manager(request)

    coupling = storage.get_coupling(coupling_id)
    if coupling is None:
        raise HTTPException(status_code=404, detail="Coupling not found")

    try:
        await manager.activate_coupling(coupling)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"active_id": coupling_id, "warnings": []}
