"""Process lifecycle: squeezelite (virtual LMS player) + cava (spectrum
analysis) + a HueDriver Entertainment session, all tied to whichever Profile
is currently active.

Only one profile can be active at a time (a Hue Bridge only supports a
single Entertainment stream), which this class enforces directly rather
than letting a second `start()` collide with the bridge's own rejection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .hue_bridge import list_entertainment_areas
from .hue_output import HueDriver, HueOutputConfig, get_channel_infos
from .latency import FixedLatencyProbe, NoLatencyProbe
from .lms_discovery import discover_lms
from .lms_follower import LmsFollower
from .lms_status import query_lms_status, query_lms_sync_peers, unsync_player
from .models import BridgeConfig, Controller, Coupling, Profile
from .pcm_source import SqueezeliteShmSource
from .storage import Storage
from .sync_engine import SyncEngine
from .types import Colour, LatencyProbe
from .util import generate_locally_administered_mac

log = logging.getLogger(__name__)


def _controller_to_bridge(controller: Controller) -> BridgeConfig:
    """Convert a Controller entity to the BridgeConfig that Hue functions expect."""
    return BridgeConfig(
        id=controller.id,
        name=controller.name,
        host=controller.host,
        app_key=controller.app_key,
        client_key=controller.client_key,
    )


def _build_engine_profile(coupling: Coupling, storage: Storage) -> Profile | None:
    """Build a Profile from a Coupling's linked entities.

    Returns None if any referenced entity is missing (broken FK).  Used
    internally so that SyncEngine and cava keep receiving a Profile while the
    rest of the stack works with Coupling + entities.
    """
    player = storage.get_virtual_player(coupling.player_id)
    zone   = storage.get_zone(coupling.zone_id)
    ac     = storage.get_analysis_config(coupling.analysis_config_id)
    cf     = storage.get_crossfader(coupling.crossfader_id)
    scene  = storage.get_scene(cf.active_scene_id) if cf else None
    if not all([player, zone, ac, cf, scene]):
        return None
    return Profile(
        id=coupling.id,
        name=coupling.name,
        lms_host=player.lms_host,
        lms_port=player.lms_port,
        player_name=player.player_name,
        player_mac=player.player_mac,
        alsa_device=player.alsa_device,
        bridge_id=zone.controller_id,
        entertainment_area_id=zone.entertainment_area_id,
        entertainment_area_name=zone.entertainment_area_name,
        light_count=zone.light_count,
        effect=scene.effect,
        effect_speed=scene.effect_speed,
        effect_decay=scene.effect_decay,
        mix_low_threshold=cf.low_threshold,
        mix_high_threshold=cf.high_threshold,
        mix_ema_alpha=cf.fade_speed,
        sensitivity=scene.sensitivity,
        brightness_floor=scene.brightness_floor,
        bass_hz=scene.bass_hz,
        mid_hz=scene.mid_hz,
        exertion_clip=scene.exertion_clip,
        onset_flash_intensity=scene.onset_flash_intensity,
        onset_method=ac.onset_method,
        onset_delta=ac.onset_delta,
        onset_alpha=ac.onset_alpha,
        superflux_mu=ac.superflux_mu,
        superflux_lag=ac.superflux_lag,
        use_hpss_separation=ac.use_hpss_separation,
        bars=ac.bars,
        lower_cutoff_freq=ac.lower_cutoff_freq,
        higher_cutoff_freq=ac.higher_cutoff_freq,
        enabled=coupling.enabled,
    )


def _build_mellow_profile(coupling: Coupling, storage: Storage) -> Profile | None:
    """Build a Profile for the mellow (quiet-passage) layer from the Crossfader's mellow Scene.

    Returns None if the crossfader has no mellow_scene_id or the entity is
    missing — the caller (SyncEngine) then falls back to the active profile for
    both layers, which is a valid no-op state.
    """
    cf = storage.get_crossfader(coupling.crossfader_id)
    if not cf or not cf.mellow_scene_id:
        return None
    mellow_scene = storage.get_scene(cf.mellow_scene_id)
    if mellow_scene is None:
        return None

    # All non-Scene fields (player, LMS, analysis, zone) are the same as the
    # active profile; only the Scene-derived fields differ.
    player = storage.get_virtual_player(coupling.player_id)
    zone   = storage.get_zone(coupling.zone_id)
    ac     = storage.get_analysis_config(coupling.analysis_config_id)
    if not all([player, zone, ac]):
        return None
    return Profile(
        id=coupling.id,
        name=coupling.name,
        lms_host=player.lms_host,
        lms_port=player.lms_port,
        player_name=player.player_name,
        player_mac=player.player_mac,
        alsa_device=player.alsa_device,
        bridge_id=zone.controller_id,
        entertainment_area_id=zone.entertainment_area_id,
        entertainment_area_name=zone.entertainment_area_name,
        light_count=zone.light_count,
        effect=mellow_scene.effect,
        effect_speed=mellow_scene.effect_speed,
        effect_decay=mellow_scene.effect_decay,
        mix_low_threshold=cf.low_threshold,
        mix_high_threshold=cf.high_threshold,
        mix_ema_alpha=cf.fade_speed,
        sensitivity=mellow_scene.sensitivity,
        brightness_floor=mellow_scene.brightness_floor,
        bass_hz=mellow_scene.bass_hz,
        mid_hz=mellow_scene.mid_hz,
        exertion_clip=mellow_scene.exertion_clip,
        onset_flash_intensity=mellow_scene.onset_flash_intensity,
        onset_method=ac.onset_method,
        onset_delta=ac.onset_delta,
        onset_alpha=ac.onset_alpha,
        superflux_mu=ac.superflux_mu,
        superflux_lag=ac.superflux_lag,
        bars=ac.bars,
        lower_cutoff_freq=ac.lower_cutoff_freq,
        higher_cutoff_freq=ac.higher_cutoff_freq,
        enabled=coupling.enabled,
    )


def _log_task_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("Background task %r raised an exception", task.get_name(), exc_info=exc)

_RUN_DIR = Path(tempfile.gettempdir()) / "huesync"


class ActiveSession:
    def __init__(self, profile: Profile, coupling: Coupling | None = None):
        self.profile = profile
        self.coupling = coupling
        self.squeezelite: subprocess.Popen | None = None
        self.cava: subprocess.Popen | None = None
        self.fifo_path: Path = _RUN_DIR / f"{profile.id}.fifo"
        self.cava_conf_path: Path = _RUN_DIR / f"{profile.id}.conf"
        self.cava_log_path: Path = _RUN_DIR / f"{profile.id}.cava.log"
        self.sync_engine: SyncEngine | None = None
        self.hue_driver: HueDriver | None = None
        self.task: asyncio.Task | None = None
        self.probe: LatencyProbe = NoLatencyProbe()
        self.poller_task: asyncio.Task | None = None
        self.shm_source: SqueezeliteShmSource | None = None
        self.follower: LmsFollower | None = None
        self.follower_task: asyncio.Task | None = None
        self.unsync_task: asyncio.Task | None = None


class PlayerManager:
    def __init__(self, storage: Storage):
        self.storage = storage
        self._active: ActiveSession | None = None
        self.latency_warning: str | None = None
        self._detected_sync_master: str | None = None
        self._detected_sync_master_name: str | None = None
        _RUN_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def detected_sync_master(self) -> str | None:
        return self._detected_sync_master

    @property
    def detected_sync_master_name(self) -> str | None:
        return self._detected_sync_master_name

    @property
    def active_coupling_id(self) -> str | None:
        if self._active and self._active.coupling:
            return self._active.coupling.id
        return None

    @property
    def last_colours(self) -> list[Colour]:
        if self._active and self._active.hue_driver:
            return self._active.hue_driver.last_colours
        return []

    @property
    def last_onset(self) -> bool:
        """True if the most recently analysed frame contained a detected onset.

        Reflects the raw detection result without any output delay applied,
        so the GUI can show onset flashes in sync with the audio rather than
        with the delayed light output.
        """
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.last_onset
        return False

    @property
    def last_pcm_onset(self) -> bool:
        """True if the PCM-tap onset pipeline detected an onset this tick."""
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.last_pcm_onset
        return False

    @property
    def last_onset_bass(self) -> bool:
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.last_onset_bass
        return False

    @property
    def last_onset_mid(self) -> bool:
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.last_onset_mid
        return False

    @property
    def last_onset_treble(self) -> bool:
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.last_onset_treble
        return False

    @property
    def process_status(self) -> dict[str, bool]:
        if not self._active:
            return {"squeezelite": False, "cava": False}
        sl = self._active.squeezelite
        cava = self._active.cava
        return {
            "squeezelite": bool(sl and sl.poll() is None),
            "cava": bool(cava and cava.poll() is None),
        }

    @property
    def applied_delay_ms(self) -> int:
        if not self._active:
            return 0
        return self._active.probe.current_delay_ms()

    @property
    def bridge_connected(self) -> bool:
        return bool(self._active and self._active.hue_driver)

    @property
    def last_bars(self) -> list[float]:
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.last_bars
        return []

    @property
    def last_mix(self) -> float:
        """Current LayerMixer crossfade value (0.0 = mellow, 1.0 = active)."""
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.last_mix
        return 0.0

    @property
    def active_coupling_name(self) -> str | None:
        if self._active and self._active.coupling:
            return self._active.coupling.name
        return None

    @property
    def active_lower_cutoff_freq(self) -> int | None:
        return self._active.profile.lower_cutoff_freq if self._active else None

    @property
    def active_higher_cutoff_freq(self) -> int | None:
        return self._active.profile.higher_cutoff_freq if self._active else None

    @property
    def active_bass_hz(self) -> int | None:
        return self._active.profile.bass_hz if self._active is not None else None

    @property
    def active_mid_hz(self) -> int | None:
        return self._active.profile.mid_hz if self._active is not None else None

    @property
    def active_effect(self) -> str | None:
        """Return the currently active effect ID, or None if no session is active."""
        if self._active:
            return self._active.profile.effect
        return None

    @property
    def active_color_mode(self) -> str | None:
        """Deprecated alias for active_effect; kept for backward compatibility."""
        return self.active_effect

    @property
    def active_onset_method(self) -> str | None:
        return self._active.profile.onset_method if self._active else None

    @property
    def active_sensitivity(self) -> float | None:
        return self._active.profile.sensitivity if self._active else None

    async def activate_coupling(self, coupling: Coupling) -> None:
        """Activate a Coupling, resolving all linked entities natively.

        Controller is used directly for Hue calls instead of the old BridgeConfig
        lookup.  A Profile is built internally so that SyncEngine and cava keep
        receiving a Profile while the rest of the stack works with entities.
        """
        await self.deactivate()

        player = self.storage.get_virtual_player(coupling.player_id)
        zone = self.storage.get_zone(coupling.zone_id)
        ac = self.storage.get_analysis_config(coupling.analysis_config_id)
        cf = self.storage.get_crossfader(coupling.crossfader_id)
        scene = self.storage.get_scene(cf.active_scene_id) if cf else None

        if not player:
            raise ValueError(f"Coupling references missing VirtualPlayer {coupling.player_id!r}")
        if not zone:
            raise ValueError(
                f"Coupling references missing Zone {coupling.zone_id!r}"
            )
        if not ac:
            raise ValueError(
                f"Coupling references missing AnalysisConfig {coupling.analysis_config_id!r}"
            )
        if not cf:
            raise ValueError(
                f"Coupling references missing Crossfader {coupling.crossfader_id!r}"
            )
        if not scene:
            raise ValueError(
                f"Crossfader references missing Scene {cf.active_scene_id!r}"
            )

        controller = self.storage.get_controller(zone.controller_id)
        if not controller:
            raise ValueError(
                f"Zone references missing Controller {zone.controller_id!r}"
            )

        bridge = _controller_to_bridge(controller)
        areas = await list_entertainment_areas(bridge)
        area = next((a for a in areas if a.id == zone.entertainment_area_id), None)
        if area is None:
            raise ValueError("Configured Entertainment Area no longer exists on the controller")

        channels = await get_channel_infos(bridge, zone.entertainment_area_id)

        output_config = HueOutputConfig(
            bridge=bridge,
            area_id=zone.entertainment_area_id,
            area_name=area.name,
        )

        profile = Profile(
            id=coupling.id,
            name=coupling.name,
            lms_host=player.lms_host,
            lms_port=player.lms_port,
            player_name=player.player_name,
            player_mac=player.player_mac,
            alsa_device=player.alsa_device,
            bridge_id=zone.controller_id,
            entertainment_area_id=zone.entertainment_area_id,
            entertainment_area_name=zone.entertainment_area_name,
            light_count=zone.light_count,
            effect=scene.effect,
            effect_speed=scene.effect_speed,
            effect_decay=scene.effect_decay,
            mix_low_threshold=cf.low_threshold,
            mix_high_threshold=cf.high_threshold,
            mix_ema_alpha=cf.fade_speed,
            sensitivity=scene.sensitivity,
            brightness_floor=scene.brightness_floor,
            bass_hz=scene.bass_hz,
            mid_hz=scene.mid_hz,
            exertion_clip=scene.exertion_clip,
            onset_flash_intensity=scene.onset_flash_intensity,
            onset_method=ac.onset_method,
            onset_delta=ac.onset_delta,
            onset_alpha=ac.onset_alpha,
            superflux_mu=ac.superflux_mu,
            superflux_lag=ac.superflux_lag,
            use_hpss_separation=ac.use_hpss_separation,
            bars=ac.bars,
            lower_cutoff_freq=ac.lower_cutoff_freq,
            higher_cutoff_freq=ac.higher_cutoff_freq,
            enabled=coupling.enabled,
        )
        mellow_profile = _build_mellow_profile(coupling, self.storage)

        self.latency_warning = None
        self._detected_sync_master = None

        session = ActiveSession(profile, coupling=coupling)
        try:
            self._start_squeezelite(session, profile)

            engine = SyncEngine(
                str(session.fifo_path), profile, probe=session.probe,
                mellow_profile=mellow_profile,
            )
            session.sync_engine = engine

            self._create_fifo(session)
            engine.start()
            self._start_cava(session, profile)

            shm_source = SqueezeliteShmSource()
            try:
                shm_source.open(profile.player_mac)
                session.shm_source = shm_source
                engine.attach_shm_source(shm_source)
                log.debug("PCM tap SHM source opened for coupling %s", coupling.name)
            except Exception as exc:
                log.warning(
                    "Could not open squeezelite SHM for PCM onset tap "
                    "(cava-based onset still active): %s",
                    exc,
                )

            hue_driver = HueDriver(output_config, channels)
            await hue_driver.start()
            session.hue_driver = hue_driver

            session.task = asyncio.create_task(engine.run(hue_driver))
            session.task.add_done_callback(_log_task_failure)
            session.poller_task = asyncio.create_task(self._poll_sync_master(session))
            session.poller_task.add_done_callback(_log_task_failure)

            follow_mac = player.follow_player_mac
            if follow_mac:
                session.follower = LmsFollower(
                    lms_host=player.lms_host,
                    follow_mac=follow_mac,
                    huesync_mac=profile.player_mac,
                )
            else:
                log.warning(
                    "Coupling %r: follow_player_mac not configured — "
                    "track mirroring disabled. Set it in the Virtual Player editor.",
                    coupling.name,
                )
            session.unsync_task = asyncio.create_task(
                self._delayed_unsync_and_follow(session, player.lms_host, profile.player_mac),
                name="lms-unsync",
            )
            session.unsync_task.add_done_callback(_log_task_failure)
        except Exception:
            await self._teardown_session(session)
            raise

        self._active = session
        self.storage.set_active_coupling_id(coupling.id)
        log.info("Activated coupling %s (%s)", coupling.name, coupling.id)

    async def deactivate(self) -> None:
        if not self._active:
            return
        session = self._active
        self._active = None
        self.latency_warning = None
        self._detected_sync_master = None
        self._detected_sync_master_name = None
        await self._teardown_session(session)
        self.storage.set_active_coupling_id(None)
        if session.coupling:
            log.info("Deactivated coupling %s", session.coupling.name)
        else:
            log.info("Deactivated profile %s", session.profile.name)

    async def _teardown_session(self, session: ActiveSession) -> None:
        if session.unsync_task:
            session.unsync_task.cancel()
        if session.follower:
            session.follower.stop()
        if session.follower_task:
            session.follower_task.cancel()
        if session.poller_task:
            session.poller_task.cancel()
        if session.task:
            session.task.cancel()
        if session.sync_engine:
            session.sync_engine.stop()
        if session.shm_source is not None:
            session.shm_source.close()
            session.shm_source = None
        await session.probe.stop()
        if session.hue_driver:
            try:
                await session.hue_driver.stop()
                await session.hue_driver.aclose()
            except Exception:  # noqa: BLE001 - best-effort teardown
                log.exception("Error stopping Hue Entertainment session")

        for proc in (session.cava, session.squeezelite):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()

        for p in (session.fifo_path, session.cava_conf_path):
            p.unlink(missing_ok=True)

        # squeezelite creates /dev/shm/squeezelite-<mac> and never removes it.
        # Without this, every activate/deactivate cycle leaves an orphaned
        # segment behind — confirmed: 9 segments after a handful of test runs.
        if session.profile.player_mac:
            Path(f"/dev/shm/squeezelite-{session.profile.player_mac}").unlink(
                missing_ok=True
            )

    def cleanup_orphaned_shm(self) -> None:
        """Remove all squeezelite shm segments left by a previous crashed run.

        Safe to call at startup: squeezelite is only ever started by HueSync,
        and only after the service itself is running.  Any segment present
        when the service starts is therefore stale — including segments from
        pre-fix runs whose random MAC was never persisted to a profile.
        """
        shm_dir = Path("/dev/shm")
        if not shm_dir.is_dir():
            return
        for seg in shm_dir.glob("squeezelite-*"):
            try:
                seg.unlink()
                log.info("Removed orphaned squeezelite shm segment %s", seg.name)
            except OSError as exc:
                log.warning("Could not remove %s: %s", seg.name, exc)

    async def restart_cava(self) -> None:
        """Restart cava within the active session without touching squeezelite or Hue.

        Re-reads the profile from storage so that frequency-cutoff changes
        saved via PATCH /api/profiles/{id} take effect.  Squeezelite keeps
        running and the Hue Entertainment session stays open throughout.
        """
        if self._active is None:
            raise RuntimeError("No active session")
        session = self._active

        if session.coupling:
            # Reload coupling from storage so any FK changes (e.g. analysis_config_id
            # swapped via PATCH) are reflected when rebuilding the profile.
            fresh = self.storage.get_coupling(session.coupling.id)
            if fresh is None:
                raise RuntimeError("Active coupling has been deleted from storage")
            session.coupling = fresh
            profile = _build_engine_profile(fresh, self.storage)
            if profile is None:
                raise RuntimeError("Active coupling has broken FK references")
        else:
            raise RuntimeError("No active coupling — cannot restart cava")
        session.profile = profile
        if session.sync_engine is not None:
            session.sync_engine.update_profile(profile)

        if session.cava and session.cava.poll() is None:
            session.cava.terminate()
            try:
                session.cava.wait(timeout=3)
            except subprocess.TimeoutExpired:
                session.cava.kill()
        session.cava = None

        self._start_cava(session, profile)
        log.info("cava restarted for profile %s", profile.name)

    def update_onset_pipeline(self, profile: Profile) -> None:
        """Switch PCM onset method live on the active session."""
        if self._active and self._active.sync_engine:
            self._active.profile = profile
            self._active.sync_engine.update_onset_pipeline(profile)

    def update_render(self, profile: Profile, mellow_profile: Profile | None = None) -> None:
        """Apply render-only changes live on the active session."""
        if self._active and self._active.sync_engine:
            self._active.profile = profile
            self._active.sync_engine.update_render(profile, mellow_profile)

    async def refresh_probe(self) -> None:
        """Re-evaluate the latency probe for the current sync master.

        Call this after saving or deleting a PlayerLatency entry so that a
        running session picks up the change immediately, without requiring a
        deactivate/reactivate cycle.  Does nothing when no session is active.
        """
        if self._active is None:
            return
        await self._apply_probe_for_master(self._active, self._detected_sync_master)

    async def _apply_probe_for_master(
        self, session: ActiveSession, master: str | None
    ) -> None:
        """Build the correct probe for *master* and install it on *session*."""
        profile = session.profile
        if master is None or master == profile.player_mac:
            # Standalone player or HueSync is the sync master — no delay needed.
            new_probe: LatencyProbe = NoLatencyProbe()
            self.latency_warning = None
        else:
            pl = self.storage.get_player_latency(master)
            log.debug("PlayerLatency lookup for sync_master=%r -> %r", master, pl)
            if pl is None:
                self.latency_warning = (
                    f"Sync master {master} has no latency config — "
                    f"using 0 ms. Add it in the Player latency section."
                )
                new_probe = NoLatencyProbe()
            elif pl.strategy == "fixed":
                new_probe = FixedLatencyProbe(pl.fixed_delay_ms)
                self.latency_warning = None
            else:  # "none" or future strategies
                new_probe = NoLatencyProbe()
                self.latency_warning = None

        old_probe = session.probe
        await new_probe.start()
        session.probe = new_probe
        if session.sync_engine:
            session.sync_engine.update_probe(new_probe)
        await old_probe.stop()
        log.info(
            "Latency probe updated: sync_master=%s probe=%s delay_ms=%d",
            master,
            type(new_probe).__name__,
            new_probe.current_delay_ms(),
        )

    async def _poll_sync_master(self, session: ActiveSession) -> None:
        """Apply the latency probe for the followed player and refresh its display name.

        With LMS sync-group membership removed (Option B), the reference player
        is follow_player_mac on the VirtualPlayer — no sync-group discovery is
        needed.  The probe is applied immediately; the display name is refreshed
        every 60 s.  Use refresh_probe() to force an immediate re-evaluation.
        """
        profile = session.profile

        follow_mac: str | None = None
        if session.coupling:
            vp = self.storage.get_virtual_player(session.coupling.player_id)
            if vp:
                follow_mac = vp.follow_player_mac or None

        lms_host = profile.lms_host
        if not lms_host:
            log.warning(
                "profile %r has no lms_host configured; "
                "attempting UDP discovery for latency probe",
                profile.name,
            )
            try:
                servers = await discover_lms(timeout=3.0)
                if servers:
                    lms_host = servers[0].host
                    log.info(
                        "LMS discovered at %s (%s) — using for latency probe; "
                        "set lms_host in the Virtual Player to avoid this",
                        lms_host, servers[0].name,
                    )
                else:
                    log.warning("LMS discovery found nothing; latency probe disabled")
                    return
            except Exception as exc:
                log.warning("LMS discovery failed (%s); latency probe disabled", exc)
                return

        if not follow_mac:
            log.warning(
                "Coupling %r has no follow_player_mac — "
                "no latency compensation applied. "
                "Set it in the Virtual Player editor.",
                profile.name,
            )
            self._detected_sync_master = None
            await self._apply_probe_for_master(session, None)
            return

        self._detected_sync_master = follow_mac
        try:
            master_status = await asyncio.to_thread(
                query_lms_status, lms_host, follow_mac
            )
            self._detected_sync_master_name = master_status.player_name
        except Exception as exc:
            log.debug("Could not fetch name for follow player %s: %s", follow_mac, exc)
            self._detected_sync_master_name = None
        await self._apply_probe_for_master(session, follow_mac)

        while True:
            await asyncio.sleep(60)
            try:
                master_status = await asyncio.to_thread(
                    query_lms_status, lms_host, follow_mac
                )
                self._detected_sync_master_name = master_status.player_name
            except Exception as exc:
                log.debug(
                    "Could not refresh name for follow player %s: %s", follow_mac, exc
                )

    async def _delayed_unsync_and_follow(
        self, session: ActiveSession, lms_host: str, player_mac: str
    ) -> None:
        """Wait for squeezelite to register with LMS, then unsync and start the follower.

        The 5-second delay ensures LMS recognises the player before the unsync
        command is sent.  Without it, the command silently no-ops because LMS
        has not yet seen the player's slimproto connection.
        """
        if not lms_host:
            log.warning(
                "LMS host not configured; skipping unsync and follower start. "
                "Set lms_host in the Virtual Player editor."
            )
            return
        await asyncio.sleep(5)

        # --- Diagnostic: check sync state BEFORE unsync ---
        try:
            peers_before = await asyncio.to_thread(
                query_lms_sync_peers, lms_host, player_mac
            )
            log.info(
                "DIAG pre-unsync:  %s sync peers = %r", player_mac, peers_before
            )
        except Exception as exc:
            log.warning("DIAG pre-unsync query failed for %s: %s", player_mac, exc)

        # --- Send the unsync command ---
        log.info(
            "DIAG sending unsync: %s sync - (host=%s)", player_mac, lms_host
        )
        try:
            await asyncio.to_thread(unsync_player, lms_host, player_mac)
            log.info("DIAG unsync command sent OK for %s", player_mac)
        except Exception as exc:
            log.warning("DIAG unsync failed for %s: %s", player_mac, exc)

        # --- Wait 3 s, then verify the player is actually standalone ---
        await asyncio.sleep(3)
        try:
            peers_after = await asyncio.to_thread(
                query_lms_sync_peers, lms_host, player_mac
            )
            if peers_after:
                log.warning(
                    "DIAG post-unsync: %s is STILL in sync group with peers %r "
                    "— LMS did not honour the unsync command",
                    player_mac, peers_after,
                )
            else:
                log.info(
                    "DIAG post-unsync: %s is now STANDALONE (no sync peers)",
                    player_mac,
                )
        except Exception as exc:
            log.warning(
                "DIAG post-unsync verification failed for %s: %s", player_mac, exc
            )

        if session.follower is not None:
            follower_task = session.follower.start()
            follower_task.add_done_callback(_log_task_failure)
            session.follower_task = follower_task

    # ALSA output device for the virtual player. This is deliberately NOT
    # "null": ALSA's null plugin discards samples the instant they arrive,
    # with no clock to pace against, so squeezelite decodes as fast as the
    # CPU allows - pinning a core at 100% and hammering LMS with stream
    # requests (a single-threaded Perl server, which then stutters for
    # every other player too).
    #
    # snd-dummy is a real, timer-driven ALSA card, so squeezelite paces at
    # actual playback speed exactly as it would against a physical DAC.
    # Measured difference on the same setup: ~100% CPU with null, ~0.2%
    # with snd-dummy.
    #
    # Requires the snd-dummy kernel module on the host (LXCs share the
    # host kernel) and the resulting /dev/snd nodes passed into the
    # container - see README.
    DEFAULT_ALSA_DEVICE = "hw:CARD=Dummy,DEV=0"

    def _start_squeezelite(self, session: ActiveSession, profile: Profile) -> None:
        binary = shutil.which("squeezelite")
        if not binary:
            raise RuntimeError("squeezelite binary not found on PATH")

        if not profile.player_mac:
            profile.player_mac = generate_locally_administered_mac()
            if session.coupling:
                player = self.storage.get_virtual_player(session.coupling.player_id)
                if player:
                    player.player_mac = profile.player_mac
                    self.storage.save_virtual_player(player)
            log.info(
                "Generated missing player_mac %s for %s",
                profile.player_mac, profile.name,
            )

        cmd = [
            binary,
            "-n", profile.player_name,
            "-m", profile.player_mac,
            "-o", profile.alsa_device or self.DEFAULT_ALSA_DEVICE,
            "-v",
        ]
        # Pass only the host address, never a port.  squeezelite's -s flag
        # expects the slimproto port (3483); profile.lms_port is the LMS
        # web/JSON-RPC port (typically 9000) and must not be passed here.
        # Without an explicit port squeezelite connects to 3483 by default.
        # Without -s at all it falls back to UDP broadcast discovery.
        if profile.lms_host:
            cmd += ["-s", profile.lms_host]
        session.squeezelite = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _create_fifo(self, session: ActiveSession) -> None:
        """Create the FIFO cava will write to and the reader will read from.

        Split out from _start_cava so the reader can attach to the FIFO
        before cava starts writing - see the ordering note in activate().
        """
        if session.fifo_path.exists():
            session.fifo_path.unlink()
        os.mkfifo(session.fifo_path)

    def _wait_for_shm(self, mac: str, timeout: float = 10.0, interval: float = 0.1) -> None:
        """Wait until squeezelite's shared-memory segment appears in /dev/shm.

        squeezelite creates /dev/shm/squeezelite-<mac> a moment after it
        starts.  Starting cava before the segment exists causes it to bail out
        immediately with "Could not open source", which on a brand-new profile
        looked like cava just silently died.
        """
        path = Path(f"/dev/shm/squeezelite-{mac}")
        deadline = time.monotonic() + timeout
        while not path.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Timed out waiting for squeezelite shared-memory segment {path}"
                )
            time.sleep(interval)
        log.debug("squeezelite SHM segment ready: %s", path)

    def _start_cava(self, session: ActiveSession, profile: Profile) -> None:
        binary = shutil.which("cava")
        if not binary:
            raise RuntimeError("cava binary not found on PATH")

        mac = profile.player_mac
        self._wait_for_shm(mac)
        conf = f"""[general]
bars = {profile.bars}
lower_cutoff_freq = {profile.lower_cutoff_freq}
higher_cutoff_freq = {profile.higher_cutoff_freq}

[input]
method = shmem
source = /squeezelite-{mac}

[output]
method = raw
raw_target = {session.fifo_path}
data_format = binary
bit_format = 8bit
channels = mono
"""
        session.cava_conf_path.write_text(conf)
        # Keep cava's stderr instead of discarding it. Debugging why cava
        # kept dying was needlessly hard because its output went to
        # DEVNULL - the process just showed up as <defunct> with no clue
        # why. Its log is small and only written on errors.
        session.cava_log_path.write_text("")
        cava_log = session.cava_log_path.open("ab")
        session.cava = subprocess.Popen(
            [binary, "-p", str(session.cava_conf_path)],
            stdout=subprocess.DEVNULL,
            stderr=cava_log,
        )