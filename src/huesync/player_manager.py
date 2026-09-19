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
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .hue_bridge import list_entertainment_areas
from .hue_output import ChannelInfo, HueDriver, HueOutputConfig, get_channel_infos
from .latency import FixedLatencyProbe, NoLatencyProbe
from .lms_discovery import discover_lms
from .lms_follower import LmsFollower, LmsSyncGroupObserver, TransportAction
from .lms_status import query_lms_status, query_lms_sync_peers, unsync_player
from .models import BridgeConfig, Controller, Coupling, Profile, VirtualPlayerType
from .pcm_source import (
    AirPlayPipeStereoSource,
    PcmSource,
    SqueezeliteShmSource,
    SqueezeliteShmStereoSource,
)
from .spectrum_engine import make_spectrum_engine as _make_spectrum_engine
from .storage import Storage
from .sync_engine import CanonicalAnalysisPipeline, SyncEngine
from .track_position import (
    AirPlayTrackPositionSource,
    LmsTrackPositionSource,
    TrackPosition,
    TrackPositionSource,
)
from .types import Colour, LatencyProbe
from .util import generate_locally_administered_mac

log = logging.getLogger(__name__)


def _make_canonical_pipeline(
    source: object,
    profile: Profile,
) -> CanonicalAnalysisPipeline:
    """Construct the canonical PCM analysis pipeline selected by profile.spectrum_backend.

    Uses the engine registry so adding a new engine requires no changes here.
    Raises RuntimeError if the selected engine is not available on this system.
    """
    engine = _make_spectrum_engine(
        profile.spectrum_backend,
        n_bars=profile.bars,
        lower_hz=float(profile.lower_cutoff_freq),
        upper_hz=float(profile.higher_cutoff_freq),
    )
    return CanonicalAnalysisPipeline(
        source=source,
        engine=engine,
        onset_method=profile.onset_method,
        onset_delta=profile.onset_delta,
        onset_alpha=profile.onset_alpha,
        superflux_mu=profile.superflux_mu,
        superflux_lag=profile.superflux_lag,
        bass_hz=profile.bass_hz,
        mid_hz=profile.mid_hz,
        band_normalise=profile.band_normalise,
        exertion_clip=profile.exertion_clip,
    )


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
    ac     = storage.get_analyser(coupling.analyser_id)
    cf     = storage.get_energy_profile(coupling.energy_profile_id)
    effect = storage.get_effect(cf.high_energy_effect_id) if cf else None
    if not all([player, zone, ac, cf, effect]):
        return None
    return Profile(
        id=coupling.id,
        name=coupling.name,
        lms_host=player.lms_host,
        lms_port=player.lms_port,
        player_name=player.player_name,
        display_name=player.display_name or player.player_name,
        player_mac=player.player_mac,
        alsa_device=player.alsa_device,
        bridge_id=zone.controller_id,
        entertainment_area_id=zone.entertainment_area_id,
        entertainment_area_name=zone.entertainment_area_name,
        light_count=zone.light_count,
        effect_type=effect.effect_type,
        effect_speed=effect.effect_speed,
        effect_decay=effect.effect_decay,
        blend_start=cf.blend_start,
        blend_end=cf.blend_end,
        blend_response=cf.blend_response,
        energy_source=cf.energy_source,
        lufs_floor=cf.lufs_floor,
        lufs_ceiling=cf.lufs_ceiling,
        adaptation_tau_s=cf.adaptation_tau_s,
        sensitivity=effect.sensitivity,
        brightness_floor=effect.brightness_floor,
        bass_hz=effect.bass_hz,
        mid_hz=effect.mid_hz,
        exertion_clip=effect.exertion_clip,
        onset_flash_intensity=effect.onset_flash_intensity,
        onset_method=ac.onset_method,
        onset_delta=ac.onset_delta,
        onset_alpha=ac.onset_alpha,
        superflux_mu=ac.superflux_mu,
        superflux_lag=ac.superflux_lag,
        use_hpss_separation=ac.use_hpss_separation,
        band_normalise=ac.band_normalise,
        bars_source=ac.bars_source,
        spectrum_backend=ac.spectrum_backend,
        bars=ac.bars,
        lower_cutoff_freq=ac.lower_cutoff_freq,
        higher_cutoff_freq=ac.higher_cutoff_freq,
        enabled=coupling.enabled,
    )


def _build_mellow_profile(coupling: Coupling, storage: Storage) -> Profile | None:
    """Build a Profile for the low-energy (quiet-passage) layer from the EnergyProfile's low Effect.

    Returns None if the energy profile has no low_energy_effect_id or the entity is
    missing — the caller (SyncEngine) then falls back to the active profile for
    both layers, which is a valid no-op state.
    """
    cf = storage.get_energy_profile(coupling.energy_profile_id)
    if not cf or not cf.low_energy_effect_id:
        return None
    mellow_effect = storage.get_effect(cf.low_energy_effect_id)
    if mellow_effect is None:
        return None

    # All non-Effect fields (player, LMS, analysis, zone) are the same as the
    # active profile; only the Effect-derived fields differ.
    player = storage.get_virtual_player(coupling.player_id)
    zone   = storage.get_zone(coupling.zone_id)
    ac     = storage.get_analyser(coupling.analyser_id)
    if not all([player, zone, ac]):
        return None
    return Profile(
        id=coupling.id,
        name=coupling.name,
        lms_host=player.lms_host,
        lms_port=player.lms_port,
        player_name=player.player_name,
        display_name=player.display_name or player.player_name,
        player_mac=player.player_mac,
        alsa_device=player.alsa_device,
        bridge_id=zone.controller_id,
        entertainment_area_id=zone.entertainment_area_id,
        entertainment_area_name=zone.entertainment_area_name,
        light_count=zone.light_count,
        effect_type=mellow_effect.effect_type,
        effect_speed=mellow_effect.effect_speed,
        effect_decay=mellow_effect.effect_decay,
        blend_start=cf.blend_start,
        blend_end=cf.blend_end,
        blend_response=cf.blend_response,
        energy_source=cf.energy_source,
        lufs_floor=cf.lufs_floor,
        lufs_ceiling=cf.lufs_ceiling,
        adaptation_tau_s=cf.adaptation_tau_s,
        sensitivity=mellow_effect.sensitivity,
        brightness_floor=mellow_effect.brightness_floor,
        bass_hz=mellow_effect.bass_hz,
        mid_hz=mellow_effect.mid_hz,
        exertion_clip=mellow_effect.exertion_clip,
        onset_flash_intensity=mellow_effect.onset_flash_intensity,
        onset_method=ac.onset_method,
        onset_delta=ac.onset_delta,
        onset_alpha=ac.onset_alpha,
        superflux_mu=ac.superflux_mu,
        superflux_lag=ac.superflux_lag,
        use_hpss_separation=ac.use_hpss_separation,
        band_normalise=ac.band_normalise,
        bars_source=ac.bars_source,
        spectrum_backend=ac.spectrum_backend,
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
    def __init__(
        self,
        profile: Profile,
        coupling: Coupling | None = None,
        player_type: VirtualPlayerType = VirtualPlayerType.LMS,
    ):
        self.profile = profile
        self.stopping = False  # source still owned until teardown completes
        self.coupling = coupling
        self.player_type = player_type
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
        self.shm_source: PcmSource | AirPlayPipeStereoSource | None = None
        self.follower: LmsFollower | None = None
        self.follower_task: asyncio.Task | None = None
        self.unsync_task: asyncio.Task | None = None
        self.track_source: TrackPositionSource | None = None


class PlayerManager:
    def __init__(self, storage: Storage):
        self.storage = storage
        self._active: ActiveSession | None = None
        # Shairport's FIFO is an event stream, not a replayable snapshot. Keep
        # one reader across Stop/Go so events during inactive sessions aren't lost.
        self._airplay_tracks: AirPlayTrackPositionSource | None = None
        self.latency_warning: str | None = None
        self._detected_sync_master: str | None = None
        self._detected_sync_master_name: str | None = None
        _RUN_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def track_position(self) -> TrackPosition | None:
        session = self._active
        if session and not session.stopping and session.track_source:
            return session.track_source.read()
        return None

    @property
    def configuration_restore_ready(self) -> bool:
        """Only a fully released session permits configuration replacement."""
        return self._active is None

    @property
    def detected_sync_master(self) -> str | None:
        return self._detected_sync_master

    @property
    def detected_sync_master_name(self) -> str | None:
        return self._detected_sync_master_name

    @property
    def follow_target_mac(self) -> str | None:
        session = self._active
        if (not session or session.stopping or session.player_type != VirtualPlayerType.LMS
                or not session.follower):
            return None
        target = session.follower.target_mac
        managed = {p.player_mac.lower() for p in self.storage.list_virtual_players()}
        managed.add(session.profile.player_mac.lower())
        if (not target or not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", target)
                or target.lower() in managed):
            return None
        return target

    @property
    def follow_target_name(self) -> str | None:
        target = self.follow_target_mac
        if target and target == self._detected_sync_master:
            return self._detected_sync_master_name or target
        return target

    async def control_followed_player(self, coupling_id: str, action: TransportAction) -> str:
        session = self._active
        if not session or session.stopping or self.active_coupling_id != coupling_id:
            raise RuntimeError("Coupling is not active")
        # AirPlay transport needs Shairport's DACP/MPRIS remote-control path;
        # it is deliberately outside this LMS-only endpoint.
        target = self.follow_target_mac
        if target is None or session.follower is None:
            raise RuntimeError("No controllable followed LMS player")
        task = asyncio.create_task(
            asyncio.to_thread(session.follower.control_target, action, target)
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Keep the API mutation guard until the bounded CLI write finishes,
            # so teardown/replacement cannot overtake an abandoned command.
            await task
            raise
        return target

    @property
    def follower_warning(self) -> str | None:
        """Human-readable warning when the LMS follower is disconnected, else None."""
        if self._active and self._active.follower:
            return self._active.follower.warning
        return None

    @property
    def analysis_stopping(self) -> bool:
        """An owned session is stopping; activation must complete its teardown."""
        return bool(self._active and self._active.stopping)

    @property
    def active_coupling_id(self) -> str | None:
        if self._active and self._active.coupling:
            return self._active.coupling.id
        return None

    @property
    def active_zone_id(self) -> str | None:
        if self._active and self._active.coupling:
            return self._active.coupling.zone_id
        return None

    @property
    def last_channel_infos(self) -> list[ChannelInfo]:
        if self._active and self._active.hue_driver:
            return self._active.hue_driver.channels
        return []

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
        if self._active.player_type == VirtualPlayerType.AIRPLAY:
            return {"squeezelite": False, "cava": False}
        sl = self._active.squeezelite
        cava = self._active.cava
        return {
            "squeezelite": bool(sl and sl.poll() is None),
            "cava": bool(cava and cava.poll() is None),
        }

    @property
    def active_player_type(self) -> str | None:
        """The type string of the active player, or None if no session is active."""
        if self._active:
            return self._active.player_type.value
        return None

    @property
    def airplay_receiving(self) -> bool | None:
        """True/False when an AirPlay player is active; None otherwise."""
        if not self._active or self._active.player_type != VirtualPlayerType.AIRPLAY:
            return None
        src = self._active.shm_source
        return src.running if src is not None else False

    @property
    def applied_delay_ms(self) -> int:
        if not self._active:
            return 0
        return self._active.probe.current_delay_ms()

    @property
    def bridge_connected(self) -> bool:
        return bool(self._active and self._active.hue_driver)

    @property
    def preview_spectrum(self) -> tuple[list[float], list[float] | None]:
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.preview_spectrum
        return [], None

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
    def last_energy(self) -> float:
        """Raw full-band energy of the last frame (0.0–1.0)."""
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.last_energy
        return 0.0

    @property
    def last_energy_input(self) -> float:
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.last_energy_input
        return 0.0

    @property
    def last_loudness(self) -> tuple[float | None, float | None]:
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.last_loudness
        return None, None

    @property
    def last_sustained_energy(self) -> float | None:
        """Section-level sustained energy from SustainedEnergyTracker (None if unavailable)."""
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.last_sustained_energy
        return None

    @property
    def active_energy_profile_id(self) -> str | None:
        if self._active and self._active.coupling:
            return self._active.coupling.energy_profile_id
        return None

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
            return self._active.profile.effect_type
        return None

    @property
    def active_onset_method(self) -> str | None:
        return self._active.profile.onset_method if self._active else None

    @property
    def active_bars_source(self) -> str | None:
        """The bars_source currently in effect on the active session, or None.

        Used by the coupling PATCH router to decide whether an ``analyser_id``
        swap crosses a mode boundary (canonical ↔ FIFO) and therefore needs a
        full deactivate/reactivate rather than a live pipeline swap.
        """
        return self._active.profile.bars_source if self._active else None

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
        ac = self.storage.get_analyser(coupling.analyser_id)
        cf = self.storage.get_energy_profile(coupling.energy_profile_id)
        effect = self.storage.get_effect(cf.high_energy_effect_id) if cf else None

        if not player:
            raise ValueError(f"Coupling references missing VirtualPlayer {coupling.player_id!r}")
        if not zone:
            raise ValueError(
                f"Coupling references missing Zone {coupling.zone_id!r}"
            )
        if not ac:
            raise ValueError(
                f"Coupling references missing Analyser {coupling.analyser_id!r}"
            )
        if not cf:
            raise ValueError(
                f"Coupling references missing EnergyProfile {coupling.energy_profile_id!r}"
            )
        if not effect:
            raise ValueError(
                f"EnergyProfile references missing Effect {cf.high_energy_effect_id!r}"
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
            display_name=player.display_name or player.player_name,
            player_mac=player.player_mac,
            alsa_device=player.alsa_device,
            bridge_id=zone.controller_id,
            entertainment_area_id=zone.entertainment_area_id,
            entertainment_area_name=zone.entertainment_area_name,
            light_count=zone.light_count,
            effect_type=effect.effect_type,
            effect_speed=effect.effect_speed,
            effect_decay=effect.effect_decay,
            blend_start=cf.blend_start,
            blend_end=cf.blend_end,
            blend_response=cf.blend_response,
            energy_source=cf.energy_source,
            lufs_floor=cf.lufs_floor,
            lufs_ceiling=cf.lufs_ceiling,
            adaptation_tau_s=cf.adaptation_tau_s,
            sensitivity=effect.sensitivity,
            brightness_floor=effect.brightness_floor,
            bass_hz=effect.bass_hz,
            mid_hz=effect.mid_hz,
            exertion_clip=effect.exertion_clip,
            onset_flash_intensity=effect.onset_flash_intensity,
            onset_method=ac.onset_method,
            onset_delta=ac.onset_delta,
            onset_alpha=ac.onset_alpha,
            superflux_mu=ac.superflux_mu,
            superflux_lag=ac.superflux_lag,
            use_hpss_separation=ac.use_hpss_separation,
            band_normalise=ac.band_normalise,
            bars_source=ac.bars_source,
            spectrum_backend=ac.spectrum_backend,
            bars=ac.bars,
            lower_cutoff_freq=ac.lower_cutoff_freq,
            higher_cutoff_freq=ac.higher_cutoff_freq,
            enabled=coupling.enabled,
        )
        mellow_profile = _build_mellow_profile(coupling, self.storage)

        self.latency_warning = None
        self._detected_sync_master = None

        session = ActiveSession(profile, coupling=coupling, player_type=player.type)
        try:
            if player.type == VirtualPlayerType.AIRPLAY:
                await self._activate_airplay(
                    session, profile, mellow_profile, output_config, channels
                )
            else:
                await self._activate_lms(
                    session, profile, player, mellow_profile, output_config, channels
                )
        except Exception:
            # Keep ownership even if cleanup itself times out. A subsequent
            # deactivate/activate retries teardown before opening a new source.
            self._active = session
            await self._teardown_session(session)
            self._active = None
            raise

        self._active = session
        self.storage.set_active_coupling_id(coupling.id)
        log.info("Activated coupling %s (%s)", coupling.name, coupling.id)

    async def _activate_lms(
        self,
        session: ActiveSession,
        profile: Profile,
        player,
        mellow_profile: Profile | None,
        output_config: HueOutputConfig,
        channels: list[ChannelInfo],
    ) -> None:
        """LMS path: squeezelite + (external CAVA or canonical analysis) + Hue."""
        self._start_squeezelite(session, profile)

        if profile.bars_source == "pcm_pipeline":
            await self._activate_lms_pcm(session, profile, mellow_profile, output_config, channels)
        else:
            await self._activate_lms_cava(session, profile, mellow_profile, output_config, channels)

        # Adapter selection is ingress/session responsibility. Downstream sees only
        # TrackPositionSource. Resolve the follower's actual selected MAC dynamically.
        session.track_source = LmsTrackPositionSource(
            player.lms_host,
            lambda: (session.follower.target_mac if session.follower else
                     (None if player.follow_mode == "sync_group" else profile.player_mac)),
        )
        session.track_source.open()

        if player.follow_mode == "sync_group":
            async def target_changed(mac: str | None) -> None:
                if session.stopping:
                    return
                await self._apply_probe_for_master(session, mac)
                self._detected_sync_master = mac
                self._detected_sync_master_name = None
                if mac:
                    try:
                        observed_at = time.monotonic()
                        status = await asyncio.to_thread(query_lms_status, player.lms_host, mac)
                        self._detected_sync_master_name = status.player_name
                        if isinstance(session.track_source, LmsTrackPositionSource):
                            session.track_source.seed(mac, status, observed_at)
                    except OSError:
                        pass  # MAC remains a usable display identity.

            session.follower = LmsSyncGroupObserver(
                player.lms_host, profile.player_mac,
                managed_macs=lambda: [
                    vp.player_mac for vp in self.storage.list_virtual_players()
                ],
                on_target_changed=target_changed,
            )
            session.follower_task = session.follower.start()
            session.follower_task.add_done_callback(_log_task_failure)
            return  # Auto mode must never schedule the manual unsync path.

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
                session.coupling and session.coupling.name,
            )
        session.unsync_task = asyncio.create_task(
            self._delayed_unsync_and_follow(session, player.lms_host, profile.player_mac),
            name="lms-unsync",
        )
        session.unsync_task.add_done_callback(_log_task_failure)

    async def _activate_lms_cava(
        self,
        session: ActiveSession,
        profile: Profile,
        mellow_profile: Profile | None,
        output_config: HueOutputConfig,
        channels: list[ChannelInfo],
    ) -> None:
        """LMS cava sub-path: squeezelite + cava/FIFO + optional SHM PCM tap.

        bars_source='cava': spectrum bars come from the external CAVA process via FIFO.
        spectrum_backend is only meaningful when bars_source='pcm_pipeline'.
        The invalid combination bars_source='cava' + spectrum_backend='cavacore' is
        rejected at the model layer before this path is reached.
        """
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
            log.debug(
                "PCM tap SHM source opened for coupling %s",
                session.coupling and session.coupling.name,
            )
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

    async def _activate_lms_pcm(
        self,
        session: ActiveSession,
        profile: Profile,
        mellow_profile: Profile | None,
        output_config: HueOutputConfig,
        channels: list[ChannelInfo],
    ) -> None:
        """LMS pcm_pipeline sub-path: squeezelite + canonical PCM pipeline, no cava/FIFO.

        Uses SqueezeliteShmStereoSource (stereo, SourceReadResult protocol) so the
        shared _make_canonical_pipeline() factory can select v2 or cavacore, identical
        to the AirPlay path.  Both ingresses feed into the same factory.
        """
        await asyncio.to_thread(self._wait_for_shm, profile.player_mac)

        shm_source = SqueezeliteShmStereoSource()
        shm_source.open(profile.player_mac)
        session.shm_source = shm_source

        pcm_analyser = _make_canonical_pipeline(shm_source, profile)

        engine = SyncEngine(
            None, profile, probe=session.probe,
            mellow_profile=mellow_profile, analyser=pcm_analyser,
        )
        session.sync_engine = engine
        engine.start()

        hue_driver = HueDriver(output_config, channels)
        await hue_driver.start()
        session.hue_driver = hue_driver

        session.task = asyncio.create_task(engine.run(hue_driver))
        session.task.add_done_callback(_log_task_failure)
        session.poller_task = asyncio.create_task(self._poll_sync_master(session))
        session.poller_task.add_done_callback(_log_task_failure)
        log.info(
            "LMS pcm_pipeline coupling %s active — backend=%r SHM: /dev/shm/squeezelite-%s",
            session.coupling and session.coupling.name,
            profile.spectrum_backend,
            profile.player_mac,
        )

    async def _activate_airplay(
        self,
        session: ActiveSession,
        profile: Profile,
        mellow_profile: Profile | None,
        output_config: HueOutputConfig,
        channels: list[ChannelInfo],
    ) -> None:
        """AirPlay path: stereo pipe source + AudioCanonicalizer + CanonicalAnalysisPipeline + Hue.

        Phase 3 native AirPlay path.  Uses the stereo decoded-source adapter
        (AirPlayPipeStereoSource) and the canonical analysis pipeline
        (CanonicalAnalysisPipeline) which performs phase-safe stereo STFT analysis.

        No squeezelite, no cava, no FIFO, no LMS follower.  Exactly one ingress
        reader owns the production AirPlay FIFO — AirPlayPipeStereoSource.
        """
        if self._airplay_tracks is None:
            self._airplay_tracks = AirPlayTrackPositionSource()
        session.track_source = self._airplay_tracks
        session.track_source.open()
        if self._configure_shairport_name(profile.display_name or profile.player_name):
            self._airplay_tracks.invalidate()

        pipe_source = AirPlayPipeStereoSource()
        pipe_source.open()
        session.shm_source = pipe_source

        pcm_analyser = _make_canonical_pipeline(pipe_source, profile)

        engine = SyncEngine(
            None, profile, probe=session.probe,
            mellow_profile=mellow_profile, analyser=pcm_analyser,
        )
        session.sync_engine = engine
        engine.start()

        hue_driver = HueDriver(output_config, channels)
        await hue_driver.start()
        session.hue_driver = hue_driver

        session.task = asyncio.create_task(engine.run(hue_driver))
        session.task.add_done_callback(_log_task_failure)
        log.info(
            "AirPlay coupling %s active — backend=%r pipe: /run/huesync/airplay.pcm",
            session.coupling and session.coupling.name,
            profile.spectrum_backend,
        )

    async def close(self) -> None:
        """Application shutdown, including the persistent receiver metadata reader."""
        try:
            await self.deactivate()
        finally:
            if self._airplay_tracks is not None:
                await self._airplay_tracks.close()
                self._airplay_tracks = None

    async def deactivate(self) -> None:
        if not self._active:
            return
        session = self._active
        self.latency_warning = None
        self._detected_sync_master = None
        self._detected_sync_master_name = None
        await self._teardown_session(session)
        self._active = None
        self.storage.set_active_coupling_id(None)
        if session.coupling:
            log.info("Deactivated coupling %s", session.coupling.name)
        else:
            log.info("Deactivated profile %s", session.profile.name)

    async def _teardown_session(self, session: ActiveSession) -> None:
        session.stopping = True
        if session.track_source is not None:
            if session.track_source is not self._airplay_tracks:
                await session.track_source.close()
            session.track_source = None
        if session.unsync_task:
            session.unsync_task.cancel()
        if session.follower:
            session.follower.stop()
            session.follower = None
        if session.follower_task:
            task = session.follower_task
            task.cancel()
            try:
                # Own completion of the follower's connection cleanup before
                # a retry can cancel it again or start a replacement follower.
                await task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise  # do not swallow cancellation of teardown itself
            session.follower_task = None
        if session.poller_task:
            session.poller_task.cancel()
        if session.task:
            session.task.cancel()
        if session.sync_engine:
            stopped = session.sync_engine.stop()
            if stopped is False or session.sync_engine.retirement_pending is True:
                raise RuntimeError(
                    "Analysis worker still retiring; source retained. Retry deactivation "
                    "after the worker exits. No replacement session has been started."
                )
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

        Transactional contract (BLOCKER 4): a failed ``_start_cava`` MUST
        NOT leave the runtime with a mutated ``session.profile`` and no
        cava process.  On failure we restore ``session.profile`` /
        ``session.coupling`` / the sync engine's profile to the pre-call
        snapshot and attempt to relaunch cava with the old profile; if
        that relaunch also fails, ``session.cava`` is left ``None`` as an
        explicit inactive-cava state (the caller then rolls back persisted
        storage so the three views — storage, session, runtime — agree).
        """
        if self._active is None:
            raise RuntimeError("No active session")
        session = self._active

        if not session.coupling:
            raise RuntimeError("No active coupling — cannot restart cava")

        # Reload coupling from storage so any FK changes (e.g. analyser_id
        # swapped via PATCH) are reflected when rebuilding the profile.
        fresh = self.storage.get_coupling(session.coupling.id)
        if fresh is None:
            raise RuntimeError("Active coupling has been deleted from storage")
        profile = _build_engine_profile(fresh, self.storage)
        if profile is None:
            raise RuntimeError("Active coupling has broken FK references")

        # Snapshot the pre-call runtime state.  These references stay live
        # until we either commit the new profile (success path) or roll
        # back (failure path).
        old_profile = session.profile
        old_coupling = session.coupling

        # Tear down the old cava process now — we need the FIFO free
        # before starting a new writer.  session.cava is set to None
        # immediately so a partial failure below never leaves us pointing
        # at a terminated process.
        if session.cava and session.cava.poll() is None:
            session.cava.terminate()
            try:
                session.cava.wait(timeout=3)
            except subprocess.TimeoutExpired:
                session.cava.kill()
        session.cava = None

        # Tentatively commit the new profile.
        session.coupling = fresh
        session.profile = profile
        if session.sync_engine is not None:
            session.sync_engine.update_profile(profile)

        try:
            self._start_cava(session, profile)
        except Exception:
            log.exception(
                "restart_cava: _start_cava(new profile=%s) failed — rolling back",
                profile.name,
            )
            # BLOCKER 4: restore the pre-call snapshot so session/runtime
            # match what the caller is about to roll storage back to.
            session.coupling = old_coupling
            session.profile = old_profile
            if session.sync_engine is not None:
                try:
                    session.sync_engine.update_profile(old_profile)
                except Exception:  # noqa: BLE001 — best-effort rollback
                    log.exception("restart_cava: rollback update_profile raised")
            # Best-effort restore of a usable cava process.  If this also
            # fails, session.cava stays None — an EXPLICIT inactive-cava
            # state (session.cava is None; downstream visibility via
            # normal cava.poll() checks).  The caller (api.py) rolls
            # persisted storage back so all three views agree.
            try:
                self._start_cava(session, old_profile)
            except Exception:  # noqa: BLE001 — best-effort restore
                log.exception(
                    "restart_cava: rollback _start_cava(old profile) failed — "
                    "session has no cava process, runtime is in explicit "
                    "inactive-cava state"
                )
                session.cava = None
            raise

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

    def replace_pcm_analyser(self, profile: Profile) -> None:
        """Transactionally swap the active spectrum engine.

        Semantics:
        1. Snapshot the current profile so we can roll back on failure.
        2. Build a candidate CanonicalAnalysisPipeline WITHOUT touching
           ``self._active``.  If construction itself raises (invalid config,
           unavailable engine) neither the old pipeline nor the session
           profile are touched.
        3. Delegate the atomic stop-old / start-new dance to
           ``SyncEngine.replace_analyser``, supplying a rebuild callable so a
           failed candidate does not leave the runtime without an analyser.
        4. On success: commit ``self._active.profile = profile``.
        5. On failure: restore the previous profile on the session so
           storage, session profile, and runtime stay in sync.  The caller
           (api.py) is responsible for rolling back persisted Analyser
           configuration on the returned exception.
        """
        if self._active is None or self._active.sync_engine is None:
            return
        if self._active.stopping:
            raise RuntimeError(
                "Session is stopping; complete deactivation before replacing analysis"
            )
        source = self._active.shm_source
        if source is None:
            log.warning(
                "replace_pcm_analyser: active session has no shm_source "
                "(bars_source='cava'?); ignoring spectrum_backend swap"
            )
            return

        old_profile = self._active.profile

        # Step 1: build candidate first.  If this raises we have not yet
        # stopped the old pipeline, so no rollback is required.
        candidate = _make_canonical_pipeline(source, profile)

        # Rebuild closure — used by SyncEngine.replace_analyser if the
        # candidate fails to start.  It re-uses the exact same source and
        # the pre-swap profile so the analyser matches what storage still
        # (or will) contain after the caller rolls back.
        def _rebuild_old() -> CanonicalAnalysisPipeline:
            return _make_canonical_pipeline(source, old_profile)

        try:
            self._active.sync_engine.replace_analyser(
                candidate, rebuild_old=_rebuild_old
            )
        except Exception:
            # Restore session profile so the runtime matches what the caller
            # will roll storage back to.  The exception propagates so api.py
            # can restore persisted Analyser configuration.
            self._active.profile = old_profile
            if self._active.sync_engine.retirement_pending:
                self._active.stopping = True
            raise

        # SUCCESS: commit new profile only after start succeeded.
        self._active.profile = profile
        self._active.stopping = False
        log.info(
            "replace_pcm_analyser: new spectrum_backend=%r active",
            profile.spectrum_backend,
        )

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
        """Apply the latency probe for the followed player and fetch its display name.

        Runs once at Coupling activation — no periodic repeat.  Sending repeated
        status queries to the followed player (e.g. the Sonos Port via the
        sonos-squeezebox plugin) caused false newsong events every ~60 s, which
        restarted the Sonos audio stream.
        """
        profile = session.profile

        follow_mac: str | None = None
        if session.coupling:
            vp = self.storage.get_virtual_player(session.coupling.player_id)
            if vp:
                if vp.follow_mode == "sync_group":
                    return  # The session observer owns dynamic target/probe updates.
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
            if isinstance(session.track_source, LmsTrackPositionSource):
                try:
                    observed_at = time.monotonic()
                    status = await asyncio.to_thread(query_lms_status, lms_host, profile.player_mac)
                    session.track_source.seed(profile.player_mac, status, observed_at)
                except (OSError, ValueError):
                    pass  # subscription reconnects independently of initial availability
            return

        self._detected_sync_master = follow_mac
        try:
            observed_at = time.monotonic()
            master_status = await asyncio.to_thread(
                query_lms_status, lms_host, follow_mac
            )
            self._detected_sync_master_name = master_status.player_name
            if isinstance(session.track_source, LmsTrackPositionSource):
                session.track_source.seed(follow_mac, master_status, observed_at)
        except Exception as exc:
            log.debug("Could not fetch name for follow player %s: %s", follow_mac, exc)
            self._detected_sync_master_name = None
        await self._apply_probe_for_master(session, follow_mac)

    async def _diag_sync_both(
        self, label: str, lms_host: str, huesync_mac: str, follow_mac: str | None
    ) -> None:
        """Log sync ? state for both players at a named checkpoint."""
        for name, mac in (("HueSync", huesync_mac), ("follow ", follow_mac)):
            if not mac:
                continue
            try:
                peers = await asyncio.to_thread(query_lms_sync_peers, lms_host, mac)
                log.info("DIAG [%s] %s (%s) sync? -> peers=%r", label, name, mac, peers)
            except Exception as exc:
                log.warning(
                    "DIAG [%s] %s (%s) sync? query failed: %s", label, name, mac, exc
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

        follow_mac: str | None = None
        if session.coupling:
            vp = self.storage.get_virtual_player(session.coupling.player_id)
            if vp:
                follow_mac = vp.follow_player_mac or None

        await asyncio.sleep(5)

        # --- Diagnostic: check sync state BEFORE unsync on BOTH players ---
        await self._diag_sync_both("pre-unsync ", lms_host, player_mac, follow_mac)

        # --- Send the unsync command to HueSync ---
        log.info("DIAG sending: %s sync -  (host=%s)", player_mac, lms_host)
        try:
            await asyncio.to_thread(unsync_player, lms_host, player_mac)
            log.info("DIAG unsync sent OK for %s", player_mac)
        except Exception as exc:
            log.warning("DIAG unsync failed for %s: %s", player_mac, exc)

        # --- Wait 3 s, then verify BOTH players are standalone ---
        await asyncio.sleep(3)
        await self._diag_sync_both("post-unsync", lms_host, player_mac, follow_mac)

        # --- Follower about to start — snapshot state right before ---
        await self._diag_sync_both("pre-follow ", lms_host, player_mac, follow_mac)

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
    _SHAIRPORT_CONF = Path("/usr/local/etc/shairport-sync.conf")

    def _configure_shairport_name(self, name: str) -> bool:
        """Ensure shairport-sync is configured with the given advertised name.

        Return True if a receiver restart was attempted, invalidating metadata.

        Computes the desired managed config deterministically and compares it to
        the existing file.  If identical, returns immediately — no write, no
        restart.  If different (name changed, file absent, or content differs),
        writes the file and restarts the service exactly once.

        shairport-sync reads its name only at startup, so SIGHUP is not enough —
        a full service restart is required when the config changes.  Failures are
        logged as warnings so that activation can proceed even if systemctl is
        unavailable (e.g. tests).

        TECHNICAL DEBT: HueSync currently owns and manages the global
        shairport-sync configuration file as a single managed unit tied to the
        active VirtualPlayer's advertised name.  Reconsidering the semantics of
        one global shairport-sync instance versus per-VirtualPlayer AirPlay
        receivers is deferred to a later phase.
        """
        conf = (
            'general = {\n'
            f'  name = "{name}";\n'
            '  output_backend = "pipe";\n'
            '  // Analysis-only receiver: full-scale PCM regardless of source volume.\n'
            '  ignore_volume_control = "yes";\n'
            '}\n'
            'pipe = {\n'
            '  name = "/run/huesync/airplay.pcm";\n'
            '  output_rate = 44100;\n'
            '  output_format = "S16_LE";\n'
            '  output_channels = 2;\n'
            '}\n'
            'metadata = {\n'
            '  enabled = "yes";\n'
            '  include_cover_art = "no";\n'
            '  pipe_name = "/run/huesync/airplay.metadata";\n'
            '  progress_interval = 10.0;\n'
            '}\n'
        )
        try:
            existing = self._SHAIRPORT_CONF.read_text()
        except OSError:
            existing = None
        if existing == conf:
            log.debug("shairport-sync config unchanged for name %r — skipping restart", name)
            return False
        try:
            self._SHAIRPORT_CONF.write_text(conf)
        except OSError as exc:
            log.warning("Could not write shairport-sync config: %s", exc)
            return False
        try:
            subprocess.run(
                ["systemctl", "restart", "shairport-sync"],
                check=True, timeout=10, capture_output=True,
            )
            log.info("shairport-sync restarted with name %r", name)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not restart shairport-sync: %s", exc)
        return True

    def _start_squeezelite(self, session: ActiveSession, profile: Profile) -> None:
        # External CAVA consumes upstream SHM ABI v0; canonical analysis requires
        # our v1 producer. Both binaries are built by the repository installer.
        name = "huesync-squeezelite-fifo" if profile.bars_source == "cava" else "squeezelite"
        binary = shutil.which(name)
        if not binary:
            raise RuntimeError(f"{name} binary not found; run scripts/install-huesync.sh")

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
            "-n", profile.display_name or profile.player_name,
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
