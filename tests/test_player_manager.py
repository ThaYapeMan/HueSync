"""Tests for PlayerManager shm lifecycle and squeezelite command assembly.

These tests create real files under /dev/shm to verify that teardown and
startup cleanup actually remove them — the same path the production code
uses, so there is no seam between test and production behaviour.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from huesync.lms_status import LmsPlayerStatus
from huesync.models import (
    Analyser,
    Controller,
    ControllerType,
    Coupling,
    Effect,
    EnergyProfile,
    Profile,
    VirtualPlayer,
    VirtualPlayerType,
    Zone,
)
from huesync.player_manager import ActiveSession, PlayerManager, _build_engine_profile
from huesync.storage import Storage


def _make_manager(tmp_path: Path) -> PlayerManager:
    return PlayerManager(Storage(tmp_path / "config.json"))


# ---------------------------------------------------------------------------
# Teardown removes the shm segment
# ---------------------------------------------------------------------------


def test_teardown_removes_shm_segment(tmp_path: Path) -> None:
    """_teardown_session() must unlink /dev/shm/squeezelite-<mac>."""
    mac = "02:ff:00:de:ad:01"
    shm_path = Path(f"/dev/shm/squeezelite-{mac}")
    shm_path.write_bytes(b"")  # create a fake segment

    manager = _make_manager(tmp_path)
    profile = Profile(player_mac=mac)
    session = ActiveSession(profile)

    asyncio.run(manager._teardown_session(session))

    assert not shm_path.exists(), (
        f"_teardown_session() should have removed {shm_path}"
    )


def test_teardown_tolerates_missing_shm(tmp_path: Path) -> None:
    """_teardown_session() must not raise if the shm file is already gone."""
    mac = "02:ff:00:de:ad:02"
    shm_path = Path(f"/dev/shm/squeezelite-{mac}")
    assert not shm_path.exists(), "precondition: file must not exist"

    manager = _make_manager(tmp_path)
    profile = Profile(player_mac=mac)
    session = ActiveSession(profile)

    # Should complete without raising FileNotFoundError.
    asyncio.run(manager._teardown_session(session))


def test_teardown_skips_shm_when_mac_is_empty(tmp_path: Path) -> None:
    """If player_mac is empty (pre-fix profile), teardown must not crash."""
    manager = _make_manager(tmp_path)
    profile = Profile(player_mac="")
    session = ActiveSession(profile)

    asyncio.run(manager._teardown_session(session))


# ---------------------------------------------------------------------------
# Startup orphan cleanup
# ---------------------------------------------------------------------------


def test_cleanup_orphaned_shm_removes_known_mac(tmp_path: Path) -> None:
    """cleanup_orphaned_shm() removes squeezelite shm segments at startup."""
    mac = "02:ff:00:de:ad:03"
    shm_path = Path(f"/dev/shm/squeezelite-{mac}")
    shm_path.write_bytes(b"")

    manager = _make_manager(tmp_path)
    manager.cleanup_orphaned_shm()

    assert not shm_path.exists()


def test_cleanup_orphaned_shm_removes_unknown_mac(tmp_path: Path) -> None:
    """cleanup_orphaned_shm() removes ALL squeezelite-* segments, even those
    whose MAC is not in any profile (pre-fix runs with random MACs)."""
    unknown_mac = "02:ff:00:de:ad:04"
    shm_path = Path(f"/dev/shm/squeezelite-{unknown_mac}")
    shm_path.write_bytes(b"")

    # Storage has NO profile with this MAC — simulates pre-fix orphan.
    manager = _make_manager(tmp_path)
    manager.cleanup_orphaned_shm()

    assert not shm_path.exists(), (
        "cleanup_orphaned_shm() should remove ALL squeezelite-* segments at startup, "
        "not just those matching a known profile MAC"
    )


# ---------------------------------------------------------------------------
# Squeezelite command assembly
# ---------------------------------------------------------------------------


def test_squeezelite_server_arg_omits_lms_port(tmp_path: Path) -> None:
    """_start_squeezelite must NOT pass lms_port to squeezelite's -s flag.

    profile.lms_port stores the LMS web/JSON-RPC port (typically 9000, as
    returned by the Discover button and UDP discovery's json_port field).
    squeezelite's -s expects the slimproto host; without an explicit port it
    connects to 3483 by default.  Passing ":9000" routes squeezelite to the
    web interface, where it can never register as a Slimproto player.

    This is a pre-existing design bug that surfaces whenever a user fills in
    lms_port via the Discover button (which sets it to the json_port = 9000).
    """
    manager = _make_manager(tmp_path)
    profile = Profile(
        player_mac="02:ff:00:de:ad:08",
        player_name="HueSync",
        lms_host="192.168.178.23",
        lms_port=9000,
        alsa_device="hw:CARD=Dummy,DEV=0",
    )
    session = ActiveSession(profile)

    with patch("shutil.which", return_value="/usr/bin/squeezelite"), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        manager._start_squeezelite(session, profile)

    cmd: list[str] = mock_popen.call_args[0][0]

    s_idx = cmd.index("-s")
    server_arg = cmd[s_idx + 1]

    assert ":9000" not in server_arg, (
        f"squeezelite -s must not contain the web port 9000; got {server_arg!r}. "
        "Pass only the host so squeezelite uses the slimproto port 3483."
    )
    assert server_arg == "192.168.178.23", (
        f"squeezelite -s should be just the host address; got {server_arg!r}"
    )


def test_squeezelite_omits_server_flag_when_host_empty(tmp_path: Path) -> None:
    """When lms_host is empty, -s is omitted so squeezelite uses UDP discovery."""
    manager = _make_manager(tmp_path)
    profile = Profile(player_mac="02:ff:00:de:ad:09", lms_host="")
    session = ActiveSession(profile)

    with patch("shutil.which", return_value="/usr/bin/squeezelite"), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        manager._start_squeezelite(session, profile)

    cmd: list[str] = mock_popen.call_args[0][0]
    assert "-s" not in cmd, (
        "When lms_host is empty, -s should be absent so squeezelite discovers "
        f"LMS via UDP broadcast. Got cmd: {cmd}"
    )


def test_cleanup_orphaned_shm_removes_multiple(tmp_path: Path) -> None:
    """cleanup_orphaned_shm() removes every squeezelite-* file it finds."""
    macs = ["02:ff:00:de:ad:05", "02:ff:00:de:ad:06", "02:ff:00:de:ad:07"]
    paths = [Path(f"/dev/shm/squeezelite-{m}") for m in macs]
    for p in paths:
        p.write_bytes(b"")

    manager = _make_manager(tmp_path)
    manager.cleanup_orphaned_shm()

    assert not any(p.exists() for p in paths), (
        "cleanup_orphaned_shm() should have removed all three segments"
    )


# ---------------------------------------------------------------------------
# Helpers for Phase-2c tests
# ---------------------------------------------------------------------------


def _make_full_storage(tmp_path: Path) -> tuple[Storage, Coupling]:
    """Create a Storage pre-populated with one complete set of linked entities."""
    storage = Storage(tmp_path / "config.json")

    controller = Controller(
        id="ctrl-1", name="Hue Bridge", type=ControllerType.HUE,
        host="192.168.1.50", app_key="app-key", client_key="client-key",
    )
    storage.save_controller(controller)

    player = VirtualPlayer(
        id="player-1", lms_host="192.168.1.10", lms_port=9000,
        player_name="HueSync", player_mac="aa:bb:cc:dd:ee:ff", alsa_device="",
    )
    storage.save_virtual_player(player)

    zone = Zone(
        id="zone-1", name="Living AE", controller_id="ctrl-1",
        entertainment_area_id="ae-001", entertainment_area_name="Living Room AE",
        light_count=4,
    )
    storage.save_zone(zone)

    ac = Analyser(
        id="ac-1", name="Default", onset_method="combined", onset_delta=0.1,
        onset_alpha=0.9, superflux_mu=3, superflux_lag=2, bars=30,
        lower_cutoff_freq=50, higher_cutoff_freq=12000,
    )
    storage.save_analyser(ac)

    effect = Effect(
        id="scene-1", name="Default", effect_type="spectrum_rgb",
        sensitivity=1.0, brightness_floor=0.15, bass_hz=250, mid_hz=2000,
        exertion_clip=3.0,
    )
    storage.save_effect(effect)

    crossfader = EnergyProfile(
        id="cf-1", name="Default CF",
        high_energy_effect_id="scene-1",
        blend_start=0.3, blend_end=0.7, blend_response=0.1,
    )
    storage.save_energy_profile(crossfader)

    coupling = Coupling(
        id="coupling-1", name="Living Room",
        player_id="player-1", analyser_id="ac-1",
        zone_id="zone-1", energy_profile_id="cf-1", enabled=True,
    )
    storage.save_coupling(coupling)

    return storage, coupling


# ---------------------------------------------------------------------------
# build_profile_from_coupling
# ---------------------------------------------------------------------------


def test_build_profile_from_coupling_maps_all_fields(tmp_path: Path) -> None:
    storage, coupling = _make_full_storage(tmp_path)
    profile = _build_engine_profile(coupling, storage)

    assert profile is not None
    assert profile.id == "coupling-1"
    assert profile.name == "Living Room"
    assert profile.lms_host == "192.168.1.10"
    assert profile.player_mac == "aa:bb:cc:dd:ee:ff"
    assert profile.bridge_id == "ctrl-1"
    assert profile.entertainment_area_id == "ae-001"
    assert profile.entertainment_area_name == "Living Room AE"
    assert profile.light_count == 4
    assert profile.effect_type == "spectrum_rgb"
    assert profile.sensitivity == 1.0
    assert profile.bass_hz == 250
    assert profile.onset_method == "combined"
    assert profile.bars == 30
    assert profile.lower_cutoff_freq == 50
    assert profile.enabled is True


def test_build_profile_from_coupling_returns_none_on_missing_player(tmp_path: Path) -> None:
    storage, coupling = _make_full_storage(tmp_path)
    storage.delete_virtual_player("player-1")
    assert _build_engine_profile(coupling, storage) is None


def test_build_profile_from_coupling_returns_none_on_missing_zone(tmp_path: Path) -> None:
    storage, coupling = _make_full_storage(tmp_path)
    storage.delete_zone("zone-1")
    assert _build_engine_profile(coupling, storage) is None


def test_build_profile_from_coupling_returns_none_on_missing_ac(tmp_path: Path) -> None:
    storage, coupling = _make_full_storage(tmp_path)
    storage.delete_analyser("ac-1")
    assert _build_engine_profile(coupling, storage) is None


def test_build_profile_from_coupling_returns_none_on_missing_crossfader(tmp_path: Path) -> None:
    storage, coupling = _make_full_storage(tmp_path)
    storage.delete_energy_profile("cf-1")
    assert _build_engine_profile(coupling, storage) is None


def test_build_profile_from_coupling_returns_none_on_missing_scene(tmp_path: Path) -> None:
    storage, coupling = _make_full_storage(tmp_path)
    storage.delete_effect("scene-1")
    assert _build_engine_profile(coupling, storage) is None


# ---------------------------------------------------------------------------
# activate_coupling — validation (before Hue calls)
# ---------------------------------------------------------------------------


def test_activate_coupling_raises_on_missing_player(tmp_path: Path) -> None:
    storage, coupling = _make_full_storage(tmp_path)
    storage.delete_virtual_player("player-1")
    manager = PlayerManager(storage)

    with pytest.raises(ValueError, match="missing VirtualPlayer"):
        asyncio.run(manager.activate_coupling(coupling))


def test_activate_coupling_raises_on_missing_zone(tmp_path: Path) -> None:
    storage, coupling = _make_full_storage(tmp_path)
    storage.delete_zone("zone-1")
    manager = PlayerManager(storage)

    with pytest.raises(ValueError, match="missing Zone"):
        asyncio.run(manager.activate_coupling(coupling))


def test_activate_coupling_raises_on_missing_analyser(tmp_path: Path) -> None:
    storage, coupling = _make_full_storage(tmp_path)
    storage.delete_analyser("ac-1")
    manager = PlayerManager(storage)

    with pytest.raises(ValueError, match="missing Analyser"):
        asyncio.run(manager.activate_coupling(coupling))


def test_activate_coupling_raises_on_missing_crossfader(tmp_path: Path) -> None:
    storage, coupling = _make_full_storage(tmp_path)
    storage.delete_energy_profile("cf-1")
    manager = PlayerManager(storage)

    with pytest.raises(ValueError, match="missing EnergyProfile"):
        asyncio.run(manager.activate_coupling(coupling))


def test_activate_coupling_raises_on_missing_scene(tmp_path: Path) -> None:
    storage, coupling = _make_full_storage(tmp_path)
    storage.delete_effect("scene-1")
    manager = PlayerManager(storage)

    with pytest.raises(ValueError, match="missing Effect"):
        asyncio.run(manager.activate_coupling(coupling))


def test_activate_coupling_raises_on_missing_controller(tmp_path: Path) -> None:
    storage, coupling = _make_full_storage(tmp_path)
    storage.delete_controller("ctrl-1")
    manager = PlayerManager(storage)

    with pytest.raises(ValueError, match="missing Controller"):
        asyncio.run(manager.activate_coupling(coupling))


# ---------------------------------------------------------------------------
# active_coupling_id property
# ---------------------------------------------------------------------------


def test_active_coupling_id_none_when_inactive(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    assert manager.active_coupling_id is None


def test_active_coupling_id_none_for_profile_mode(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    profile = Profile(player_mac="aa:bb:cc:dd:ee:ff")
    manager._active = ActiveSession(profile, coupling=None)
    assert manager.active_coupling_id is None


def test_active_coupling_id_set_for_coupling_mode(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    profile = Profile(id="coupling-1", player_mac="aa:bb:cc:dd:ee:ff")
    coupling = Coupling(id="coupling-1", name="Test")
    manager._active = ActiveSession(profile, coupling=coupling)
    assert manager.active_coupling_id == "coupling-1"


# ---------------------------------------------------------------------------
# deactivate clears both active IDs in storage
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _poll_sync_master — regression: no periodic status query to follow player
# ---------------------------------------------------------------------------


def test_poll_sync_master_queries_follow_player_exactly_once(tmp_path: Path) -> None:
    """_poll_sync_master must query the follow player exactly once, then return.

    Regression: a while-True loop previously re-queried every 60 s, which
    triggered false newsong events via the sonos-squeezebox LMS plugin and
    restarted the Sonos audio stream on every cycle.
    """
    async def _run() -> None:
        storage, coupling = _make_full_storage(tmp_path)

        player = storage.get_virtual_player("player-1")
        assert player is not None
        player.follow_player_mac = "48:a6:b8:20:39:64"
        storage.save_virtual_player(player)
        coupling = storage.get_coupling("coupling-1")

        manager = PlayerManager(storage)
        profile = Profile(
            id="coupling-1",
            player_mac="aa:bb:cc:dd:ee:ff",
            lms_host="192.168.1.10",
        )
        session = ActiveSession(profile, coupling=coupling)

        mock_query = MagicMock(return_value=LmsPlayerStatus(player_name="Sonos Port"))

        with patch("huesync.player_manager.query_lms_status", new=mock_query), \
             patch.object(manager, "_apply_probe_for_master", new=AsyncMock()):
            await asyncio.wait_for(manager._poll_sync_master(session), timeout=2.0)

        assert mock_query.call_count == 1, (
            f"query_lms_status must be called exactly once (no periodic loop); "
            f"got {mock_query.call_count} calls"
        )

    asyncio.run(_run())


def test_deactivate_clears_active_coupling_id(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "config.json")
    storage.set_active_coupling_id("coupling-1")

    manager = PlayerManager(storage)
    profile = Profile(id="coupling-1", player_mac="aa:bb:cc:dd:ee:ff")
    coupling = Coupling(id="coupling-1", name="Test")
    manager._active = ActiveSession(profile, coupling=coupling)

    asyncio.run(manager.deactivate())

    assert storage.get_active_coupling_id() is None
    assert manager.active_coupling_id is None


# ---------------------------------------------------------------------------
# active_color_mode / active_bass_hz / active_mid_hz property chain
#
# Regression guard: these properties read self._active.profile.{effect_type,bass_hz,
# mid_hz}.  Any future rename that breaks the chain (e.g. "effect_type" field moved,
# update_render not setting self._active.profile) will fail these tests.
# Uses real code paths — no mocking of the properties themselves.
# ---------------------------------------------------------------------------


def _make_session_from_storage(tmp_path: Path) -> tuple[PlayerManager, ActiveSession]:
    """Create a PlayerManager with a live ActiveSession built from real entities.

    The Effect has effect_type='spectrum_rgb', bass_hz=300, mid_hz=3000 so
    assertions can distinguish correct values from dataclass defaults.
    """
    storage, coupling = _make_full_storage(tmp_path)

    # Override Effect values to be distinct from defaults (250 / 2000).
    effect = storage.get_effect("scene-1")
    assert effect is not None
    effect.effect_type = "spectrum_rgb"
    effect.bass_hz = 300
    effect.mid_hz = 3000
    storage.save_effect(effect)

    profile = _build_engine_profile(coupling, storage)
    assert profile is not None, "_build_engine_profile must succeed with a full storage"
    session = ActiveSession(profile, coupling=coupling)
    manager = PlayerManager(storage)
    manager._active = session
    return manager, session


def test_active_color_mode_reads_scene_effect(tmp_path: Path) -> None:
    """active_color_mode must reflect the Effect effect_type stored in the active profile.

    Regression: any rename that breaks self._active.profile.effect_type causes all
    spectrum bars to render in accent colour (purple) instead of R/G/B.
    """
    manager, _ = _make_session_from_storage(tmp_path)
    assert manager.active_color_mode == "spectrum_rgb", (
        f"active_color_mode must return the Effect effect_type ('spectrum_rgb'); "
        f"got {manager.active_color_mode!r} — check profile.effect_type property chain"
    )


def test_active_band_hz_reads_scene_values(tmp_path: Path) -> None:
    """active_bass_hz / active_mid_hz must return Effect values, not dataclass defaults."""
    manager, _ = _make_session_from_storage(tmp_path)
    assert manager.active_bass_hz == 300, (
        f"active_bass_hz must return Scene.bass_hz (300); got {manager.active_bass_hz}"
    )
    assert manager.active_mid_hz == 3000, (
        f"active_mid_hz must return Scene.mid_hz (3000); got {manager.active_mid_hz}"
    )


def test_update_render_propagates_effect_to_active_profile(tmp_path: Path) -> None:
    """update_render must set self._active.profile so active_color_mode reflects the change.

    Regression: ba643f3 fixed a missing 'self._active.profile = profile' in
    update_render.  This test ensures that assignment is never removed.
    """
    manager, session = _make_session_from_storage(tmp_path)
    session.sync_engine = MagicMock()

    new_profile = Profile(
        id="coupling-1", player_mac="aa:bb:cc:dd:ee:ff",
        effect_type="mono_pulse", bass_hz=400, mid_hz=4000,
    )
    manager.update_render(new_profile)

    assert manager.active_color_mode == "mono_pulse", (
        "update_render must assign self._active.profile; "
        "active_color_mode still reads the old value — profile assignment missing"
    )
    assert manager.active_bass_hz == 400
    assert manager.active_mid_hz == 4000


def test_update_onset_pipeline_propagates_profile(tmp_path: Path) -> None:
    """update_onset_pipeline must set self._active.profile so band-hz properties update.

    Regression: same fix as update_render (ba643f3); both methods needed the
    assignment.  Verifying both prevents one from regressing independently.
    """
    manager, session = _make_session_from_storage(tmp_path)
    session.sync_engine = MagicMock()

    new_profile = Profile(
        id="coupling-1", player_mac="aa:bb:cc:dd:ee:ff",
        effect_type="spectrum_rgb", bass_hz=500, mid_hz=5000,
    )
    manager.update_onset_pipeline(new_profile)

    assert manager.active_bass_hz == 500, (
        "update_onset_pipeline must assign self._active.profile; "
        "active_bass_hz still reads the old value — profile assignment missing"
    )
    assert manager.active_mid_hz == 5000


# ---------------------------------------------------------------------------
# AirPlay activation branch
# ---------------------------------------------------------------------------


def _make_airplay_storage(tmp_path: Path) -> tuple[Storage, Coupling]:
    """Storage with an AirPlay VirtualPlayer linked through a full coupling."""
    storage = Storage(tmp_path / "config.json")

    controller = Controller(
        id="ctrl-ap", name="Hue Bridge", type=ControllerType.HUE,
        host="192.168.1.50", app_key="app-key", client_key="client-key",
    )
    storage.save_controller(controller)

    player = VirtualPlayer(
        id="player-ap", type=VirtualPlayerType.AIRPLAY,
        player_name="HueSync-AP", player_mac="aa:bb:cc:dd:ee:01",
    )
    storage.save_virtual_player(player)

    zone = Zone(
        id="zone-ap", name="Living AE", controller_id="ctrl-ap",
        entertainment_area_id="ae-ap", entertainment_area_name="AP Room AE",
        light_count=2,
    )
    storage.save_zone(zone)

    ac = Analyser(
        id="ac-ap", name="Default", onset_method="combined", onset_delta=0.1,
        onset_alpha=0.9, superflux_mu=3, superflux_lag=2, bars=30,
        lower_cutoff_freq=50, higher_cutoff_freq=12000,
    )
    storage.save_analyser(ac)

    effect = Effect(id="scene-ap", name="Default", effect_type="spectrum_rgb")
    storage.save_effect(effect)

    crossfader = EnergyProfile(id="cf-ap", name="Default CF", high_energy_effect_id="scene-ap")
    storage.save_energy_profile(crossfader)

    coupling = Coupling(
        id="coupling-ap", name="AirPlay Room",
        player_id="player-ap", analyser_id="ac-ap",
        zone_id="zone-ap", energy_profile_id="cf-ap", enabled=True,
    )
    storage.save_coupling(coupling)

    return storage, coupling


def test_airplay_activation_skips_squeezelite(tmp_path: Path) -> None:
    """_activate_airplay must not spawn squeezelite; session.squeezelite stays None."""
    from unittest.mock import AsyncMock, MagicMock, patch

    storage, coupling = _make_airplay_storage(tmp_path)
    manager = PlayerManager(storage)

    fake_area = MagicMock()
    fake_area.id = "ae-ap"
    fake_area.name = "AP Room AE"

    _pm = "huesync.player_manager"
    with (
        patch(f"{_pm}.list_entertainment_areas", new=AsyncMock(return_value=[fake_area])),
        patch(f"{_pm}.get_channel_infos", new=AsyncMock(return_value=[])),
        patch(f"{_pm}.AirPlayPipeSource") as mock_src_cls,
        patch(f"{_pm}.PcmAudioPipeline") as mock_analyser_cls,
        patch(f"{_pm}.SyncEngine") as mock_engine_cls,
        patch(f"{_pm}.HueDriver") as mock_driver_cls,
    ):
        mock_src = MagicMock()
        mock_src_cls.return_value = mock_src

        mock_analyser = MagicMock()
        mock_analyser_cls.return_value = mock_analyser

        mock_engine = MagicMock()
        mock_engine.run = AsyncMock()
        mock_engine_cls.return_value = mock_engine

        mock_driver = MagicMock()
        mock_driver.start = AsyncMock()
        mock_driver_cls.return_value = mock_driver

        asyncio.run(manager.activate_coupling(coupling))

    assert manager._active is not None
    assert manager._active.squeezelite is None, (
        "_activate_airplay must not spawn squeezelite"
    )
    assert manager._active.shm_source is mock_src, (
        "_activate_airplay must assign AirPlayPipeSource to session.shm_source"
    )
    mock_src.open.assert_called_once()


def test_airplay_activation_player_type_recorded(tmp_path: Path) -> None:
    """active_player_type must return 'AirPlay' after activating an AirPlay coupling."""
    from unittest.mock import AsyncMock, MagicMock, patch

    storage, coupling = _make_airplay_storage(tmp_path)
    manager = PlayerManager(storage)

    fake_area = MagicMock()
    fake_area.id = "ae-ap"
    fake_area.name = "AP Room AE"

    _pm = "huesync.player_manager"
    with (
        patch(f"{_pm}.list_entertainment_areas", new=AsyncMock(return_value=[fake_area])),
        patch(f"{_pm}.get_channel_infos", new=AsyncMock(return_value=[])),
        patch(f"{_pm}.AirPlayPipeSource", return_value=MagicMock()),
        patch(f"{_pm}.PcmAudioPipeline", return_value=MagicMock()),
        patch(f"{_pm}.SyncEngine") as mock_engine_cls,
        patch(f"{_pm}.HueDriver") as mock_driver_cls,
    ):
        mock_engine = MagicMock()
        mock_engine.run = AsyncMock()
        mock_engine_cls.return_value = mock_engine

        mock_driver = MagicMock()
        mock_driver.start = AsyncMock()
        mock_driver_cls.return_value = mock_driver

        asyncio.run(manager.activate_coupling(coupling))

    assert manager.active_player_type == "AirPlay"


def test_airplay_activation_airplay_receiving_property(tmp_path: Path) -> None:
    """airplay_receiving reflects pipe_source.running when an AirPlay session is active."""
    from unittest.mock import AsyncMock, MagicMock, patch

    storage, coupling = _make_airplay_storage(tmp_path)
    manager = PlayerManager(storage)

    fake_area = MagicMock()
    fake_area.id = "ae-ap"
    fake_area.name = "AP Room AE"

    mock_src = MagicMock()
    mock_src.running = True

    _pm = "huesync.player_manager"
    with (
        patch(f"{_pm}.list_entertainment_areas", new=AsyncMock(return_value=[fake_area])),
        patch(f"{_pm}.get_channel_infos", new=AsyncMock(return_value=[])),
        patch(f"{_pm}.AirPlayPipeSource", return_value=mock_src),
        patch(f"{_pm}.PcmAudioPipeline", return_value=MagicMock()),
        patch(f"{_pm}.SyncEngine") as mock_engine_cls,
        patch(f"{_pm}.HueDriver") as mock_driver_cls,
    ):
        mock_engine = MagicMock()
        mock_engine.run = AsyncMock()
        mock_engine_cls.return_value = mock_engine

        mock_driver = MagicMock()
        mock_driver.start = AsyncMock()
        mock_driver_cls.return_value = mock_driver

        asyncio.run(manager.activate_coupling(coupling))

    assert manager.airplay_receiving is True

    mock_src.running = False
    assert manager.airplay_receiving is False
