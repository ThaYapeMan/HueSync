"""Tests for the five-entity model: models, storage CRUD, and storage backfill."""

import tempfile
from pathlib import Path

from huesync.models import (
    AnalysisConfig,
    ColorMode,
    Controller,
    ControllerType,
    Coupling,
    LightProvider,
    RenderConfig,
    VirtualPlayer,
)
from huesync.storage import Storage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_storage() -> Storage:
    d = tempfile.mkdtemp()
    return Storage(Path(d) / "config.json")


# ---------------------------------------------------------------------------
# Controller round-trip
# ---------------------------------------------------------------------------


def test_controller_roundtrip():
    c = Controller(name="My Bridge", type=ControllerType.HUE, host="10.0.0.1",
                   app_key="ak", client_key="ck")
    c2 = Controller.from_dict(c.to_dict())
    assert c2.id == c.id
    assert c2.name == c.name
    assert c2.type == ControllerType.HUE
    assert c2.host == c.host
    assert c2.app_key == c.app_key
    assert c2.client_key == c.client_key


def test_controller_from_dict_defaults_type_to_hue():
    d = {"id": "x", "name": "n", "host": "h", "app_key": "a", "client_key": "ck"}
    c = Controller.from_dict(d)
    assert c.type == ControllerType.HUE


def test_controller_from_dict_strips_unknown_keys():
    d = Controller(name="test").to_dict()
    d["future_field"] = "ignored"
    c = Controller.from_dict(d)
    assert not hasattr(c, "future_field")


# ---------------------------------------------------------------------------
# VirtualPlayer round-trip
# ---------------------------------------------------------------------------


def test_player_roundtrip():
    p = VirtualPlayer(name="Zone A", lms_host="10.0.0.5", lms_port=9000,
                      player_name="HueSync", player_mac="aa:bb:cc:dd:ee:01",
                      alsa_device="hw:0")
    p2 = VirtualPlayer.from_dict(p.to_dict())
    assert p2.id == p.id
    assert p2.lms_host == p.lms_host
    assert p2.player_mac == p.player_mac
    assert p2.alsa_device == p.alsa_device


def test_player_from_dict_strips_unknown_keys():
    d = VirtualPlayer().to_dict()
    d["legacy"] = "gone"
    p = VirtualPlayer.from_dict(d)
    assert not hasattr(p, "legacy")


# ---------------------------------------------------------------------------
# LightProvider round-trip
# ---------------------------------------------------------------------------


def test_light_provider_roundtrip():
    lp = LightProvider(name="Living Room AE", controller_id="ctrl-1",
                       entertainment_area_id="ae-001",
                       entertainment_area_name="Living Room", light_count=5)
    lp2 = LightProvider.from_dict(lp.to_dict())
    assert lp2.id == lp.id
    assert lp2.controller_id == lp.controller_id
    assert lp2.entertainment_area_id == lp.entertainment_area_id
    assert lp2.light_count == lp.light_count


# ---------------------------------------------------------------------------
# AnalysisConfig round-trip
# ---------------------------------------------------------------------------


def test_analysis_config_roundtrip():
    ac = AnalysisConfig(name="SuperFlux", onset_method="superflux", onset_delta=0.2,
                        onset_alpha=0.8, superflux_mu=5, superflux_lag=3,
                        bars=48, lower_cutoff_freq=30, higher_cutoff_freq=14000)
    ac2 = AnalysisConfig.from_dict(ac.to_dict())
    assert ac2.id == ac.id
    assert ac2.onset_method == "superflux"
    assert ac2.superflux_mu == 5
    assert ac2.bars == 48


def test_analysis_config_from_dict_strips_unknown_keys():
    d = AnalysisConfig().to_dict()
    d["future_param"] = 42
    ac = AnalysisConfig.from_dict(d)
    assert not hasattr(ac, "future_param")


# ---------------------------------------------------------------------------
# RenderConfig round-trip
# ---------------------------------------------------------------------------


def test_render_config_roundtrip():
    rc = RenderConfig(name="Vivid", color_mode=ColorMode.MONO_PULSE,
                      sensitivity=2.0, brightness_floor=0.1,
                      bass_hz=300, mid_hz=2500, exertion_clip=4.0)
    rc2 = RenderConfig.from_dict(rc.to_dict())
    assert rc2.id == rc.id
    assert rc2.color_mode == ColorMode.MONO_PULSE
    assert rc2.sensitivity == 2.0
    assert rc2.exertion_clip == 4.0


def test_render_config_bad_color_mode_falls_back():
    d = RenderConfig().to_dict()
    d["color_mode"] = "bass_brightness"  # legacy value
    rc = RenderConfig.from_dict(d)
    assert rc.color_mode == ColorMode.SPECTRUM_RGB


# ---------------------------------------------------------------------------
# Coupling round-trip
# ---------------------------------------------------------------------------


def test_coupling_roundtrip():
    c = Coupling(name="Zone A → Hue", player_id="p1", analysis_config_id="ac1",
                 light_provider_id="lp1", render_config_id="rc1", enabled=False)
    c2 = Coupling.from_dict(c.to_dict())
    assert c2.id == c.id
    assert c2.player_id == "p1"
    assert c2.analysis_config_id == "ac1"
    assert c2.enabled is False


# ---------------------------------------------------------------------------
# Storage CRUD — Controller
# ---------------------------------------------------------------------------


def test_storage_save_and_get_controller():
    s = make_storage()
    c = Controller(name="Bridge", host="10.0.0.1")
    s.save_controller(c)
    fetched = s.get_controller(c.id)
    assert fetched is not None
    assert fetched.host == "10.0.0.1"


def test_storage_list_controllers():
    s = make_storage()
    s.save_controller(Controller(name="A"))
    s.save_controller(Controller(name="B"))
    assert len(s.list_controllers()) == 2


def test_storage_delete_controller():
    s = make_storage()
    c = Controller(name="X")
    s.save_controller(c)
    s.delete_controller(c.id)
    assert s.get_controller(c.id) is None


def test_storage_save_controller_is_upsert():
    s = make_storage()
    c = Controller(name="Old")
    s.save_controller(c)
    c.name = "New"
    s.save_controller(c)
    assert len(s.list_controllers()) == 1
    assert s.get_controller(c.id).name == "New"


# ---------------------------------------------------------------------------
# Storage CRUD — VirtualPlayer
# ---------------------------------------------------------------------------


def test_storage_save_and_get_player():
    s = make_storage()
    p = VirtualPlayer(name="Zone A", player_mac="aa:bb:cc:dd:ee:01")
    s.save_virtual_player(p)
    fetched = s.get_virtual_player(p.id)
    assert fetched is not None
    assert fetched.player_mac == "aa:bb:cc:dd:ee:01"


def test_storage_delete_player():
    s = make_storage()
    p = VirtualPlayer()
    s.save_virtual_player(p)
    s.delete_virtual_player(p.id)
    assert s.get_virtual_player(p.id) is None


# ---------------------------------------------------------------------------
# Storage CRUD — LightProvider
# ---------------------------------------------------------------------------


def test_storage_save_and_get_light_provider():
    s = make_storage()
    lp = LightProvider(name="Living AE", controller_id="ctrl-1", light_count=3)
    s.save_light_provider(lp)
    fetched = s.get_light_provider(lp.id)
    assert fetched is not None
    assert fetched.light_count == 3


def test_storage_delete_light_provider():
    s = make_storage()
    lp = LightProvider()
    s.save_light_provider(lp)
    s.delete_light_provider(lp.id)
    assert s.get_light_provider(lp.id) is None


# ---------------------------------------------------------------------------
# Storage CRUD — AnalysisConfig
# ---------------------------------------------------------------------------


def test_storage_save_and_get_analysis_config():
    s = make_storage()
    ac = AnalysisConfig(name="Test", onset_method="multiband", bars=48)
    s.save_analysis_config(ac)
    fetched = s.get_analysis_config(ac.id)
    assert fetched is not None
    assert fetched.bars == 48
    assert fetched.onset_method == "multiband"


def test_storage_delete_analysis_config():
    s = make_storage()
    ac = AnalysisConfig()
    s.save_analysis_config(ac)
    s.delete_analysis_config(ac.id)
    assert s.get_analysis_config(ac.id) is None


# ---------------------------------------------------------------------------
# Storage CRUD — RenderConfig
# ---------------------------------------------------------------------------


def test_storage_save_and_get_render_config():
    s = make_storage()
    rc = RenderConfig(name="Vivid", sensitivity=2.0, exertion_clip=4.0)
    s.save_render_config(rc)
    fetched = s.get_render_config(rc.id)
    assert fetched is not None
    assert fetched.sensitivity == 2.0
    assert fetched.exertion_clip == 4.0


def test_storage_delete_render_config():
    s = make_storage()
    rc = RenderConfig()
    s.save_render_config(rc)
    s.delete_render_config(rc.id)
    assert s.get_render_config(rc.id) is None


# ---------------------------------------------------------------------------
# Storage CRUD — Coupling
# ---------------------------------------------------------------------------


def test_storage_save_and_get_coupling():
    s = make_storage()
    c = Coupling(name="Zone A → Hue", player_id="p1", analysis_config_id="ac1",
                 light_provider_id="lp1", render_config_id="rc1")
    s.save_coupling(c)
    fetched = s.get_coupling(c.id)
    assert fetched is not None
    assert fetched.player_id == "p1"


def test_storage_delete_coupling_clears_active_id():
    s = make_storage()
    c = Coupling(name="Active")
    s.save_coupling(c)
    s.set_active_coupling_id(c.id)
    s.delete_coupling(c.id)
    assert s.get_coupling(c.id) is None
    assert s.get_active_coupling_id() is None


def test_storage_active_coupling_id_roundtrip():
    s = make_storage()
    s.set_active_coupling_id("some-uuid")
    assert s.get_active_coupling_id() == "some-uuid"
    s.set_active_coupling_id(None)
    assert s.get_active_coupling_id() is None


# ---------------------------------------------------------------------------
# Storage back-fill — existing JSON without new keys loads cleanly
# ---------------------------------------------------------------------------


def test_storage_backfills_new_collections_on_old_file():
    """A config.json written before Phase 2 gains the new keys on read."""
    import json

    d = tempfile.mkdtemp()
    path = Path(d) / "config.json"
    # Write an old-style config with no Phase-2 keys.
    path.write_text(json.dumps({
        "player_latencies": [],
        "active_profile_id": None,
    }))
    s = Storage(path)
    # Must not raise; new collections must be empty lists / None.
    assert s.list_controllers() == []
    assert s.list_virtual_players() == []
    assert s.list_couplings() == []
    assert s.get_active_coupling_id() is None


def test_storage_backfill_migrates_players_key_to_virtual_players():
    """Old config with 'players' key is transparently migrated to 'virtual_players'."""
    import json

    d = tempfile.mkdtemp()
    path = Path(d) / "config.json"
    path.write_text(json.dumps({
        "players": [
            {
                "id": "p-1",
                "name": "Old Player",
                "lms_host": "10.0.0.1",
                "lms_port": 9000,
                "player_name": "HueSync",
                "player_mac": "aa:bb:cc:dd:ee:ff",
                "alsa_device": "",
            }
        ],
        "player_latencies": [],
        "active_profile_id": None,
    }))
    s = Storage(path)
    players = s.list_virtual_players()
    assert len(players) == 1
    assert players[0].name == "Old Player"
    assert players[0].player_mac == "aa:bb:cc:dd:ee:ff"
