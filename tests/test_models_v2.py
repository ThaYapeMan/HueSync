"""Tests for the Phase-2 five-entity model: models, storage, and migration."""

import tempfile
from pathlib import Path

from huesync.migration import migrate_profiles_to_entities
from huesync.models import (
    AnalysisConfig,
    BridgeConfig,
    ColorMode,
    Controller,
    ControllerType,
    Coupling,
    LightProvider,
    Player,
    Profile,
    RenderConfig,
)
from huesync.storage import Storage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_storage() -> Storage:
    d = tempfile.mkdtemp()
    return Storage(Path(d) / "config.json")


def make_profile(**kwargs) -> Profile:
    defaults = dict(
        name="Living room",
        lms_host="192.168.1.10",
        lms_port=9000,
        player_name="HueSync",
        player_mac="aa:bb:cc:dd:ee:ff",
        alsa_device="hw:CARD=Dummy,DEV=0",
        bridge_id="bridge-001",
        entertainment_area_id="area-001",
        entertainment_area_name="Living Room AE",
        light_count=4,
        color_mode=ColorMode.SPECTRUM_RGB,
        sensitivity=1.5,
        brightness_floor=0.2,
        bars=24,
        lower_cutoff_freq=40,
        higher_cutoff_freq=10000,
        bass_hz=200,
        mid_hz=1800,
        onset_method="superflux",
        onset_delta=0.15,
        onset_alpha=0.85,
        superflux_mu=4,
        superflux_lag=3,
        exertion_clip=2.5,
        enabled=True,
    )
    defaults.update(kwargs)
    return Profile(**defaults)


def make_bridge(**kwargs) -> BridgeConfig:
    defaults = dict(
        id="bridge-001",
        name="Hue Bridge",
        host="192.168.1.50",
        app_key="app-key",
        client_key="client-key",
    )
    defaults.update(kwargs)
    return BridgeConfig(**defaults)


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
# Player round-trip
# ---------------------------------------------------------------------------


def test_player_roundtrip():
    p = Player(name="Zone A", lms_host="10.0.0.5", lms_port=9000,
               player_name="HueSync", player_mac="aa:bb:cc:dd:ee:01", alsa_device="hw:0")
    p2 = Player.from_dict(p.to_dict())
    assert p2.id == p.id
    assert p2.lms_host == p.lms_host
    assert p2.player_mac == p.player_mac
    assert p2.alsa_device == p.alsa_device


def test_player_from_dict_strips_unknown_keys():
    d = Player().to_dict()
    d["legacy"] = "gone"
    p = Player.from_dict(d)
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
# Storage CRUD — Player
# ---------------------------------------------------------------------------


def test_storage_save_and_get_player():
    s = make_storage()
    p = Player(name="Zone A", player_mac="aa:bb:cc:dd:ee:01")
    s.save_player(p)
    fetched = s.get_player(p.id)
    assert fetched is not None
    assert fetched.player_mac == "aa:bb:cc:dd:ee:01"


def test_storage_delete_player():
    s = make_storage()
    p = Player()
    s.save_player(p)
    s.delete_player(p.id)
    assert s.get_player(p.id) is None


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
        "bridges": [],
        "profiles": [],
        "player_latencies": [],
        "active_profile_id": None,
    }))
    s = Storage(path)
    # Must not raise; new collections must be empty lists / None.
    assert s.list_controllers() == []
    assert s.list_players() == []
    assert s.list_couplings() == []
    assert s.get_active_coupling_id() is None


# ---------------------------------------------------------------------------
# Migration — correctness
# ---------------------------------------------------------------------------


def test_migration_creates_one_set_of_entities_per_profile():
    s = make_storage()
    s.save_bridge(make_bridge())
    s.save_profile(make_profile())

    migrate_profiles_to_entities(s)

    assert len(s.list_controllers()) == 1
    assert len(s.list_players()) == 1
    assert len(s.list_light_providers()) == 1
    assert len(s.list_analysis_configs()) == 1
    assert len(s.list_render_configs()) == 1
    assert len(s.list_couplings()) == 1


def test_migration_field_values_preserved():
    s = make_storage()
    s.save_bridge(make_bridge(id="bridge-001", host="192.168.1.50", app_key="ak",
                               client_key="ck"))
    profile = make_profile(
        name="Living room",
        lms_host="192.168.1.10",
        player_mac="aa:bb:cc:dd:ee:ff",
        sensitivity=1.5,
        bars=24,
        onset_method="superflux",
        superflux_mu=4,
        bass_hz=200,
        exertion_clip=2.5,
        enabled=True,
    )
    s.save_profile(profile)
    migrate_profiles_to_entities(s)

    player = s.list_players()[0]
    assert player.lms_host == "192.168.1.10"
    assert player.player_mac == "aa:bb:cc:dd:ee:ff"

    controller = s.get_controller("bridge-001")
    assert controller is not None
    assert controller.host == "192.168.1.50"
    assert controller.type == ControllerType.HUE

    lp = s.list_light_providers()[0]
    assert lp.controller_id == "bridge-001"
    assert lp.entertainment_area_id == "area-001"
    assert lp.light_count == 4

    ac = s.list_analysis_configs()[0]
    assert ac.onset_method == "superflux"
    assert ac.superflux_mu == 4
    assert ac.bars == 24

    rc = s.list_render_configs()[0]
    assert rc.sensitivity == 1.5
    assert rc.bass_hz == 200
    assert rc.exertion_clip == 2.5
    assert rc.color_mode == ColorMode.SPECTRUM_RGB

    coupling = s.list_couplings()[0]
    assert coupling.id == profile.id  # ID preserved for active_profile_id continuity
    assert coupling.name == "Living room"
    assert coupling.enabled is True
    assert coupling.player_id == player.id
    assert coupling.analysis_config_id == ac.id
    assert coupling.light_provider_id == lp.id
    assert coupling.render_config_id == rc.id


def test_migration_is_idempotent():
    s = make_storage()
    s.save_bridge(make_bridge())
    s.save_profile(make_profile())

    migrate_profiles_to_entities(s)
    migrate_profiles_to_entities(s)  # second call must be a no-op

    assert len(s.list_controllers()) == 1
    assert len(s.list_players()) == 1
    assert len(s.list_couplings()) == 1


def test_migration_two_profiles_two_couplings():
    s = make_storage()
    s.save_bridge(make_bridge())
    s.save_profile(make_profile(name="Zone A"))
    s.save_profile(make_profile(name="Zone B", player_mac="aa:bb:cc:dd:ee:02"))

    migrate_profiles_to_entities(s)

    assert len(s.list_players()) == 2
    assert len(s.list_couplings()) == 2
    # One bridge → one controller
    assert len(s.list_controllers()) == 1


def test_migration_bridge_controller_id_matches_bridge_id():
    """controller.id must equal bridge.id so LightProvider.controller_id resolves."""
    s = make_storage()
    bridge = make_bridge(id="test-bridge-id")
    s.save_bridge(bridge)
    s.save_profile(make_profile(bridge_id="test-bridge-id"))

    migrate_profiles_to_entities(s)

    controller = s.get_controller("test-bridge-id")
    assert controller is not None
    lp = s.list_light_providers()[0]
    assert lp.controller_id == "test-bridge-id"


def test_migration_no_bridge_no_controller():
    """If no bridges are saved, no controllers are created."""
    s = make_storage()
    s.save_profile(make_profile())

    migrate_profiles_to_entities(s)

    assert len(s.list_controllers()) == 0
    assert len(s.list_couplings()) == 1


def test_migration_profile_with_no_bridge_id_gets_empty_controller_id():
    s = make_storage()
    s.save_profile(make_profile(bridge_id=""))

    migrate_profiles_to_entities(s)

    lp = s.list_light_providers()[0]
    assert lp.controller_id == ""
