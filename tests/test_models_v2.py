"""Tests for the five-entity model: models, storage CRUD, and storage backfill."""

import tempfile
from pathlib import Path

import pytest

from huesync.models import (
    Analyser,
    Controller,
    ControllerType,
    Coupling,
    Effect,
    EnergyProfile,
    VirtualPlayer,
    Zone,
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


def test_controller_from_dict_rejects_unknown_keys():
    d = Controller(name="test").to_dict()
    d["future_field"] = "ignored"
    with pytest.raises(TypeError):
        Controller.from_dict(d)


# ---------------------------------------------------------------------------
# VirtualPlayer round-trip
# ---------------------------------------------------------------------------


def test_player_roundtrip():
    p = VirtualPlayer(lms_host="10.0.0.5", lms_port=9000,
                      player_name="HueSync", player_mac="aa:bb:cc:dd:ee:01",
                      alsa_device="hw:0")
    p2 = VirtualPlayer.from_dict(p.to_dict())
    assert p2.id == p.id
    assert p2.type == p.type
    assert p2.lms_host == p.lms_host
    assert p2.player_mac == p.player_mac
    assert p2.alsa_device == p.alsa_device


def test_player_from_dict_rejects_unknown_keys():
    d = VirtualPlayer().to_dict()
    d["legacy"] = "gone"
    with pytest.raises(TypeError):
        VirtualPlayer.from_dict(d)


# ---------------------------------------------------------------------------
# Zone round-trip
# ---------------------------------------------------------------------------


def test_zone_roundtrip():
    zone = Zone(name="Living Room AE", controller_id="ctrl-1",
                entertainment_area_id="ae-001",
                entertainment_area_name="Living Room", light_count=5)
    zone2 = Zone.from_dict(zone.to_dict())
    assert zone2.id == zone.id
    assert zone2.controller_id == zone.controller_id
    assert zone2.entertainment_area_id == zone.entertainment_area_id
    assert zone2.light_count == zone.light_count


# ---------------------------------------------------------------------------
# Analyser round-trip
# ---------------------------------------------------------------------------


def test_analyser_roundtrip():
    ac = Analyser(name="SuperFlux", onset_method="superflux", onset_delta=0.2,
                  onset_alpha=0.8, superflux_mu=5, superflux_lag=3,
                  bars=48, lower_cutoff_freq=30, higher_cutoff_freq=14000)
    ac2 = Analyser.from_dict(ac.to_dict())
    assert ac2.id == ac.id
    assert ac2.onset_method == "superflux"
    assert ac2.superflux_mu == 5
    assert ac2.bars == 48


def test_analyser_from_dict_rejects_unknown_keys():
    d = Analyser().to_dict()
    d["future_param"] = 42
    with pytest.raises(TypeError):
        Analyser.from_dict(d)


# ---------------------------------------------------------------------------
# Effect round-trip (was Scene / RenderConfig)
# ---------------------------------------------------------------------------


def test_scene_roundtrip():
    e = Effect(name="Vivid", effect_type="mono_pulse",
               sensitivity=2.0, brightness_floor=0.1,
               bass_hz=300, mid_hz=2500, exertion_clip=4.0)
    e2 = Effect.from_dict(e.to_dict())
    assert e2.id == e.id
    assert e2.effect_type == "mono_pulse"
    assert e2.sensitivity == 2.0
    assert e2.exertion_clip == 4.0




# ---------------------------------------------------------------------------
# EnergyProfile round-trip
# ---------------------------------------------------------------------------


def test_energy_profile_roundtrip():
    cf = EnergyProfile(name="My CF", high_energy_effect_id="sc-1",
                    low_energy_effect_id="sc-2", blend_start=0.2,
                    blend_end=0.8, blend_response=0.05)
    cf2 = EnergyProfile.from_dict(cf.to_dict())
    assert cf2.id == cf.id
    assert cf2.high_energy_effect_id == "sc-1"
    assert cf2.low_energy_effect_id == "sc-2"
    assert cf2.blend_start == 0.2
    assert cf2.blend_response == 0.05


# ---------------------------------------------------------------------------
# Coupling round-trip
# ---------------------------------------------------------------------------


def test_coupling_roundtrip():
    c = Coupling(name="Zone A → Hue", player_id="p1", analyser_id="ac1",
                 zone_id="z1", energy_profile_id="cf1", enabled=False)
    c2 = Coupling.from_dict(c.to_dict())
    assert c2.id == c.id
    assert c2.player_id == "p1"
    assert c2.analyser_id == "ac1"
    assert c2.zone_id == "z1"
    assert c2.energy_profile_id == "cf1"
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
    p = VirtualPlayer(player_mac="aa:bb:cc:dd:ee:01")
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
# Storage CRUD — Zone
# ---------------------------------------------------------------------------


def test_storage_save_and_get_zone():
    s = make_storage()
    zone = Zone(name="Living AE", controller_id="ctrl-1", light_count=3)
    s.save_zone(zone)
    fetched = s.get_zone(zone.id)
    assert fetched is not None
    assert fetched.light_count == 3


def test_storage_delete_zone():
    s = make_storage()
    zone = Zone()
    s.save_zone(zone)
    s.delete_zone(zone.id)
    assert s.get_zone(zone.id) is None


# ---------------------------------------------------------------------------
# Storage CRUD — Analyser
# ---------------------------------------------------------------------------


def test_storage_save_and_get_analyser():
    s = make_storage()
    ac = Analyser(name="Test", onset_method="multiband", bars=48)
    s.save_analyser(ac)
    fetched = s.get_analyser(ac.id)
    assert fetched is not None
    assert fetched.bars == 48
    assert fetched.onset_method == "multiband"


def test_storage_delete_analyser():
    s = make_storage()
    ac = Analyser()
    s.save_analyser(ac)
    s.delete_analyser(ac.id)
    assert s.get_analyser(ac.id) is None


# ---------------------------------------------------------------------------
# Storage CRUD — Effect (was Scene / RenderConfig)
# ---------------------------------------------------------------------------


def test_storage_save_and_get_scene():
    s = make_storage()
    e = Effect(name="Vivid", sensitivity=2.0, exertion_clip=4.0)
    s.save_effect(e)
    fetched = s.get_effect(e.id)
    assert fetched is not None
    assert fetched.sensitivity == 2.0
    assert fetched.exertion_clip == 4.0


def test_storage_delete_scene():
    s = make_storage()
    e = Effect()
    s.save_effect(e)
    s.delete_effect(e.id)
    assert s.get_effect(e.id) is None


# ---------------------------------------------------------------------------
# Storage CRUD — EnergyProfile
# ---------------------------------------------------------------------------


def test_storage_save_and_get_energy_profile():
    s = make_storage()
    cf = EnergyProfile(name="CF", high_energy_effect_id="sc-1", blend_start=0.2)
    s.save_energy_profile(cf)
    fetched = s.get_energy_profile(cf.id)
    assert fetched is not None
    assert fetched.high_energy_effect_id == "sc-1"
    assert fetched.blend_start == 0.2


def test_storage_delete_energy_profile():
    s = make_storage()
    cf = EnergyProfile()
    s.save_energy_profile(cf)
    s.delete_energy_profile(cf.id)
    assert s.get_energy_profile(cf.id) is None


# ---------------------------------------------------------------------------
# Storage CRUD — Coupling
# ---------------------------------------------------------------------------


def test_storage_save_and_get_coupling():
    s = make_storage()
    c = Coupling(name="Zone A → Hue", player_id="p1", analyser_id="ac1",
                 zone_id="z1", energy_profile_id="cf1")
    s.save_coupling(c)
    fetched = s.get_coupling(c.id)
    assert fetched is not None
    assert fetched.player_id == "p1"
    assert fetched.zone_id == "z1"
    assert fetched.energy_profile_id == "cf1"


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






# ---------------------------------------------------------------------------
# Analyser.spectrum_backend validation
# ---------------------------------------------------------------------------


def test_analyser_default_spectrum_backend_is_v2() -> None:
    a = Analyser()
    assert a.spectrum_backend == "v2"


def test_analyser_cavacore_backend_accepted() -> None:
    a = Analyser(bars_source="pcm_pipeline", spectrum_backend="cavacore")
    assert a.spectrum_backend == "cavacore"


def test_analyser_invalid_spectrum_backend_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="spectrum_backend"):
        Analyser(spectrum_backend="unknown_backend")


def test_analyser_from_dict_rejects_invalid_spectrum_backend() -> None:
    import pytest

    with pytest.raises(ValueError, match="spectrum_backend"):
        Analyser.from_dict({"spectrum_backend": "bogus"})


# ---------------------------------------------------------------------------
# Profile spectrum_backend validation
# ---------------------------------------------------------------------------


def test_profile_default_backend_is_v2() -> None:
    from huesync.models import Profile

    p = Profile()
    assert p.spectrum_backend == "v2"


def test_profile_cavacore_backend_accepted() -> None:
    from huesync.models import Profile

    p = Profile(bars_source="pcm_pipeline", spectrum_backend="cavacore")
    assert p.spectrum_backend == "cavacore"


def test_profile_invalid_backend_raises() -> None:
    import pytest

    from huesync.models import Profile

    with pytest.raises(ValueError, match="spectrum_backend"):
        Profile(spectrum_backend="unknown_backend")


def test_profile_from_dict_rejects_invalid_backend() -> None:
    import pytest

    from huesync.models import Profile

    with pytest.raises(ValueError, match="spectrum_backend"):
        Profile.from_dict({"spectrum_backend": "bogus"})
