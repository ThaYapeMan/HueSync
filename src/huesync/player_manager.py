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
from .lms_status import query_lms_status, query_lms_sync_peers
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


def build_profile_from_coupling(coupling: Coupling, storage: Storage) -> Profile | None:
    """Build a Profile from a Coupling's linked entities.

    Returns None if any referenced entity is missing (broken FK).  Used as a
    bridge in Phase 2b/2c so that SyncEngine and cava keep speaking Profile
    while the rest of the stack transitions to Coupling + entities.  Removed
    in the cutover commit after Phase 2d.
    """
    player = storage.get_player(coupling.player_id)
    lp = storage.get_light_provider(coupling.light_provider_id)
    ac = storage.get_analysis_config(coupling.analysis_config_id)
    rc = storage.get_render_config(coupling.render_config_id)
    if not all([player, lp, ac, rc]):
        return None
    return Profile(
        id=coupling.id,
        name=coupling.name,
        lms_host=player.lms_host,
        lms_port=player.lms_port,
        player_name=player.player_name,
        player_mac=player.player_mac,
        alsa_device=player.alsa_device,
        bridge_id=lp.controller_id,
        entertainment_area_id=lp.entertainment_area_id,
        entertainment_area_name=lp.entertainment_area_name,
        light_count=lp.light_count,
        color_mode=rc.color_mode,
        sensitivity=rc.sensitivity,
        brightness_floor=rc.brightness_floor,
        bass_hz=rc.bass_hz,
        mid_hz=rc.mid_hz,
        exertion_clip=rc.exertion_clip,
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


class _Unset:
    """Sentinel for "never polled yet" in _poll_sync_master.

    Distinct from None (standalone player) and produces a readable repr
    rather than <object object at 0x...> in log lines.
    """
    def __repr__(self) -> str:
        return "(unset)"


_UNSET: str | None = _Unset()  # type: ignore[assignment]


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
    def active_profile_id(self) -> str | None:
        return self._active.profile.id if self._active else None

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
    def active_profile_name(self) -> str | None:
        return self._active.profile.name if self._active else None

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
    def active_color_mode(self) -> str | None:
        if self._active:
            return self._active.profile.color_mode.value
        return None

    @property
    def active_onset_method(self) -> str | None:
        return self._active.profile.onset_method if self._active else None

    @property
    def active_sensitivity(self) -> float | None:
        return self._active.profile.sensitivity if self._active else None

    async def activate(self, profile: Profile) -> None:
        """Stop whatever is currently active, then start this profile.

        Any failure partway through is cleaned up before re-raising, so a
        failed activation never leaves a leaked squeezelite/cava process
        behind (see _teardown_session).
        """
        await self.deactivate()

        bridge = self.storage.get_bridge(profile.bridge_id)
        if bridge is None:
            raise ValueError("Profile has no valid bridge configured")

        areas = await list_entertainment_areas(bridge)
        area = next((a for a in areas if a.id == profile.entertainment_area_id), None)
        if area is None:
            raise ValueError("Configured Entertainment Area no longer exists on the bridge")

        # Fetch the per-light channel IDs and their spatial positions.
        # get_channel_infos() supersedes the old get_area_channel_ids(); it
        # also captures each light's (x, y, z) position so spatial effects
        # (waves, fireworks, splotches) can use them later.
        channels = await get_channel_infos(bridge, profile.entertainment_area_id)

        output_config = HueOutputConfig(
            bridge=bridge,
            area_id=profile.entertainment_area_id,
            area_name=area.name,
        )

        self.latency_warning = None
        self._detected_sync_master = None

        session = ActiveSession(profile)
        try:
            self._start_squeezelite(session, profile)

            engine = SyncEngine(str(session.fifo_path), profile, probe=session.probe)
            session.sync_engine = engine

            # Order matters: the FIFO reader must be attached BEFORE cava
            # starts writing. A FIFO with a writer but no reader delivers
            # SIGPIPE to the writer, and cava dies immediately - silently,
            # since we send its output to DEVNULL. Starting the Hue session
            # first (an 8+ second DTLS handshake) was long enough for cava
            # to die every single time, which then looked like "the lights
            # stop after 10 seconds" (the Entertainment idle timeout with
            # zero frames ever arriving).
            #
            # _start_cava() creates the FIFO, so it has to run first, but
            # the reader thread opens it (blocking until a writer appears)
            # before cava is spawned.
            self._create_fifo(session)
            engine.start()
            self._start_cava(session, profile)

            # _start_cava() waits until the SHM segment exists, so it is safe
            # to open it here.  Failure is non-fatal: cava still drives colour.
            shm_source = SqueezeliteShmSource()
            try:
                shm_source.open(profile.player_mac)
                session.shm_source = shm_source
                engine.attach_shm_source(shm_source)
                log.debug("PCM tap SHM source opened for profile %s", profile.name)
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
        except Exception:
            # Whatever got started before the failure - squeezelite, cava,
            # the FIFO, a partially-opened Hue session - gets torn down
            # here instead of leaking.
            await self._teardown_session(session)
            raise

        self._active = session
        self.storage.set_active_profile_id(profile.id)
        log.info("Activated profile %s (%s)", profile.name, profile.id)

    async def activate_coupling(self, coupling: Coupling) -> None:
        """Activate a Coupling, resolving all linked entities natively.

        Controller is used directly for Hue calls instead of the old BridgeConfig
        lookup.  A Profile is still built internally and persisted so that
        SyncEngine, cava config, and restart_cava() keep working unchanged.
        Both active_profile_id and active_coupling_id are set in storage.
        """
        await self.deactivate()

        player = self.storage.get_player(coupling.player_id)
        lp = self.storage.get_light_provider(coupling.light_provider_id)
        ac = self.storage.get_analysis_config(coupling.analysis_config_id)
        rc = self.storage.get_render_config(coupling.render_config_id)

        if not player:
            raise ValueError(f"Coupling references missing Player {coupling.player_id!r}")
        if not lp:
            raise ValueError(
                f"Coupling references missing LightProvider {coupling.light_provider_id!r}"
            )
        if not ac:
            raise ValueError(
                f"Coupling references missing AnalysisConfig {coupling.analysis_config_id!r}"
            )
        if not rc:
            raise ValueError(
                f"Coupling references missing RenderConfig {coupling.render_config_id!r}"
            )

        controller = self.storage.get_controller(lp.controller_id)
        if not controller:
            raise ValueError(
                f"LightProvider references missing Controller {lp.controller_id!r}"
            )

        bridge = _controller_to_bridge(controller)
        areas = await list_entertainment_areas(bridge)
        area = next((a for a in areas if a.id == lp.entertainment_area_id), None)
        if area is None:
            raise ValueError("Configured Entertainment Area no longer exists on the controller")

        channels = await get_channel_infos(bridge, lp.entertainment_area_id)

        output_config = HueOutputConfig(
            bridge=bridge,
            area_id=lp.entertainment_area_id,
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
            bridge_id=lp.controller_id,
            entertainment_area_id=lp.entertainment_area_id,
            entertainment_area_name=lp.entertainment_area_name,
            light_count=lp.light_count,
            color_mode=rc.color_mode,
            sensitivity=rc.sensitivity,
            brightness_floor=rc.brightness_floor,
            bass_hz=rc.bass_hz,
            mid_hz=rc.mid_hz,
            exertion_clip=rc.exertion_clip,
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
        # Persist so restart_cava() (which reloads from storage) sees correct values.
        self.storage.save_profile(profile)

        self.latency_warning = None
        self._detected_sync_master = None

        session = ActiveSession(profile, coupling=coupling)
        try:
            self._start_squeezelite(session, profile)

            engine = SyncEngine(str(session.fifo_path), profile, probe=session.probe)
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
        except Exception:
            await self._teardown_session(session)
            raise

        self._active = session
        self.storage.set_active_profile_id(coupling.id)
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
        self.storage.set_active_profile_id(None)
        self.storage.set_active_coupling_id(None)
        if session.coupling:
            log.info("Deactivated coupling %s", session.coupling.name)
        else:
            log.info("Deactivated profile %s", session.profile.name)

    async def _teardown_session(self, session: ActiveSession) -> None:
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
            # Coupling mode: reload entities from storage and rebuild profile.
            profile = build_profile_from_coupling(session.coupling, self.storage)
            if profile is None:
                raise RuntimeError("Active coupling has broken FK references")
            self.storage.save_profile(profile)
        else:
            profile = self.storage.get_profile(session.profile.id)
            if profile is None:
                raise RuntimeError("Active profile no longer in storage")
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
            self._active.sync_engine.update_onset_pipeline(profile)

    def update_render(self, profile: Profile) -> None:
        """Apply render-only changes live on the active session."""
        if self._active and self._active.sync_engine:
            self._active.sync_engine.update_render(profile)

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
        """Periodically query LMS for the sync master and update the probe.

        Runs as a background task for the lifetime of the active session.
        The first poll happens after a short delay; subsequent polls every 15 s.
        A failed or timed-out query is logged and skipped — it never affects
        the running session.  The probe is only swapped when the sync master
        MAC actually changes, so routine steady-state polls are a no-op.
        Use refresh_probe() to force an immediate re-evaluation (e.g. after
        the user saves a PlayerLatency config for the current sync master).

        Two-stage detection:
          1. Primary:  'status' response — includes sync_master only when the
             player is actively playing.  Gives the definitive LMS sync master.
          2. Fallback: 'sync ?' command — always returns configured sync peers
             regardless of play state.  Used when the status gives no master.
        """
        profile = session.profile
        current_master: str | None = _UNSET
        first = True
        poll_n = 0

        # If lms_host is empty (profile saved without filling in the LMS host
        # field), squeezelite still works via its own UDP discovery, but the
        # poll loop needs a real host to open a TCP CLI connection.  Attempt
        # one-shot discovery here so the poller can still function.
        lms_host = profile.lms_host
        if not lms_host:
            log.warning(
                "profile %r has no lms_host configured; "
                "attempting UDP discovery to find LMS for sync-master polling",
                profile.name,
            )
            try:
                servers = await discover_lms(timeout=3.0)
                if servers:
                    lms_host = servers[0].host
                    log.info(
                        "LMS discovered at %s (%s) — using for sync-master poll; "
                        "set lms_host in the profile to avoid this",
                        lms_host, servers[0].name,
                    )
                else:
                    log.warning(
                        "LMS discovery found nothing; sync-master polling disabled "
                        "until lms_host is configured in the profile"
                    )
                    return
            except Exception as exc:
                log.warning("LMS discovery failed (%s); sync-master polling disabled", exc)
                return

        while True:
            await asyncio.sleep(2 if first else 15)
            first = False
            poll_n += 1

            # --- Stage 1: status query ---
            try:
                status = await asyncio.to_thread(
                    query_lms_status, lms_host, profile.player_mac
                )
                new_master = status.sync_master
                log.debug(
                    "Poll #%d player=%s player_name=%r sync_master=%r sync_slaves=%r",
                    poll_n, profile.player_mac,
                    status.player_name, status.sync_master, status.sync_slaves,
                )
            except Exception as exc:
                log.info("LMS status poll #%d failed for %s: %s", poll_n, profile.player_mac, exc)
                continue

            # --- Stage 2: sync? fallback ---
            # LMS omits sync_master from the status response when the player is
            # stopped or idle (the sync group persists in LMS config but is not
            # reflected in runtime status without active playback).
            if new_master is None:
                try:
                    peers = await asyncio.to_thread(
                        query_lms_sync_peers, lms_host, profile.player_mac
                    )
                    log.debug("Poll #%d sync? peers=%r", poll_n, peers)
                    if peers:
                        # Prefer a peer that already has a PlayerLatency entry,
                        # otherwise take the first peer (the probable Sonos master).
                        new_master = next(
                            (p for p in peers if self.storage.get_player_latency(p) is not None),
                            peers[0],
                        )
                        log.debug(
                            "Poll #%d sync? fallback -> master=%r", poll_n, new_master
                        )
                except Exception as exc:
                    log.debug("Poll #%d sync? fallback failed for %s: %s",
                              poll_n, profile.player_mac, exc)

            if new_master == current_master:
                continue

            log.info("Sync master changed: %r -> %r", current_master, new_master)
            current_master = new_master
            self._detected_sync_master = new_master

            # Fetch the sync master's display name for the status UI.
            # Only query when there is an external master (not standalone and
            # not HueSync itself leading the sync group).
            if new_master and new_master != profile.player_mac:
                try:
                    master_status = await asyncio.to_thread(
                        query_lms_status, lms_host, new_master
                    )
                    self._detected_sync_master_name = master_status.player_name
                except Exception as exc:
                    log.debug("Could not fetch name for sync master %s: %s", new_master, exc)
                    self._detected_sync_master_name = None
            else:
                self._detected_sync_master_name = None

            await self._apply_probe_for_master(session, new_master)

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
            self.storage.save_profile(profile)
            if session.coupling:
                player = self.storage.get_player(session.coupling.player_id)
                if player:
                    player.player_mac = profile.player_mac
                    self.storage.save_player(player)
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