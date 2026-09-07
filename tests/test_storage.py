import json
import tempfile
from pathlib import Path

from huesync.models import VirtualPlayer
from huesync.storage import Storage


def make_storage() -> Storage:
    d = tempfile.mkdtemp()
    return Storage(Path(d) / "config.json")


def test_save_and_get_virtual_player_roundtrip():
    storage = make_storage()
    player = VirtualPlayer(lms_host="192.168.0.10")
    storage.save_virtual_player(player)

    fetched = storage.get_virtual_player(player.id)
    assert fetched is not None
    assert fetched.lms_host == "192.168.0.10"
    assert fetched.id == player.id
    assert fetched.type.value == "LMS"


def test_delete_virtual_player():
    storage = make_storage()
    player = VirtualPlayer()
    storage.save_virtual_player(player)
    storage.delete_virtual_player(player.id)
    assert storage.get_virtual_player(player.id) is None


def test_migrate_creates_mellow_scene_from_old_format(tmp_path: Path):
    """migrate() converts old render_configs + mellow_colour_mode to a Scene clone + Crossfader."""
    config = tmp_path / "config.json"
    rc_id = "rc-1"
    coupling_id = "c-1"
    old_data = {
        "player_latencies": [],
        "virtual_players": [],
        "controllers": [],
        "light_providers": [],
        "analysis_configs": [],
        "render_configs": [
            {
                "id": rc_id,
                "name": "My RC",
                "color_mode": "spectrum_rgb",
                "mellow_colour_mode": "mono_pulse",  # different → should clone
                "mix_low_threshold": 0.2,
                "mix_high_threshold": 0.6,
                "mix_ema_alpha": 0.05,
                "sensitivity": 1.0,
                "brightness_floor": 0.15,
                "bass_hz": 250,
                "mid_hz": 2000,
                "exertion_clip": 3.0,
                "onset_flash_intensity": 0.0,
            }
        ],
        "couplings": [
            {
                "id": coupling_id,
                "name": "Test Coupling",
                "player_id": "p-1",
                "analysis_config_id": "ac-1",
                "light_provider_id": "lp-1",
                "render_config_id": rc_id,
                "enabled": True,
            }
        ],
        "active_coupling_id": None,
    }
    config.write_text(json.dumps(old_data))

    storage = Storage(config)
    storage.migrate()

    # Coupling now has crossfader_id set.
    couplings = storage.list_couplings()
    assert len(couplings) == 1
    c = couplings[0]
    assert c.crossfader_id != ""
    # Coupling no longer has zone_id from the old lp (just migrated).
    assert c.zone_id == "lp-1"

    # A crossfader was created with the mix params.
    crossfaders = storage.list_crossfaders()
    assert len(crossfaders) == 1
    cf = crossfaders[0]
    assert cf.low_threshold == 0.2
    assert cf.high_threshold == 0.6
    assert cf.fade_speed == 0.05
    assert cf.active_scene_id == rc_id

    # Mellow scene was created (different colour mode).
    assert cf.mellow_scene_id != ""
    assert cf.mellow_scene_id != rc_id

    # Two scenes exist: the original and the mellow clone.
    scenes = storage.list_scenes()
    mellow_scene = next((s for s in scenes if s.id == cf.mellow_scene_id), None)
    assert mellow_scene is not None
    assert mellow_scene.effect == "mono_pulse"
    assert "(Mellow)" in mellow_scene.name

    # Calling migrate() again is a no-op (idempotent).
    storage.migrate()
    assert len(storage.list_couplings()) == 1
    assert storage.list_couplings()[0].crossfader_id == c.crossfader_id


def test_migrate_same_colour_mode_reuses_scene(tmp_path: Path):
    """migrate() sets mellow_scene_id to empty when modes are the same."""
    config = tmp_path / "config.json"
    rc_id = "rc-same"
    old_data = {
        "player_latencies": [],
        "virtual_players": [],
        "controllers": [],
        "light_providers": [],
        "analysis_configs": [],
        "render_configs": [
            {
                "id": rc_id,
                "name": "Same RC",
                "color_mode": "spectrum_rgb",
                "mellow_colour_mode": "spectrum_rgb",  # same → reuse (no mellow scene)
                "sensitivity": 1.0,
                "brightness_floor": 0.15,
                "bass_hz": 250,
                "mid_hz": 2000,
                "exertion_clip": 3.0,
                "onset_flash_intensity": 0.0,
            }
        ],
        "couplings": [
            {
                "id": "c-same",
                "name": "Same Coupling",
                "player_id": "",
                "analysis_config_id": "",
                "light_provider_id": "",
                "render_config_id": rc_id,
                "enabled": True,
            }
        ],
        "active_coupling_id": None,
    }
    config.write_text(json.dumps(old_data))

    storage = Storage(config)
    storage.migrate()

    c = storage.list_couplings()[0]
    # Crossfader was created; mellow_scene_id is empty (same mode).
    assert c.crossfader_id != ""
    cf = storage.get_crossfader(c.crossfader_id)
    assert cf is not None
    assert cf.mellow_scene_id == ""
    # Only the original scene exists (no clone was created).
    assert len(storage.list_scenes()) == 1


def test_migrate_renames_color_mode_to_effect(tmp_path: Path):
    """migrate() renames color_mode → effect in scene raw dicts."""
    config = tmp_path / "config.json"
    rc_id = "rc-migrate"
    old_data = {
        "player_latencies": [],
        "virtual_players": [],
        "controllers": [],
        "light_providers": [],
        "analysis_configs": [],
        "render_configs": [
            {
                "id": rc_id,
                "name": "Old RC",
                "color_mode": "mono_pulse",
                "sensitivity": 1.0,
                "brightness_floor": 0.15,
                "bass_hz": 250,
                "mid_hz": 2000,
                "exertion_clip": 3.0,
                "onset_flash_intensity": 0.0,
            }
        ],
        "couplings": [
            {
                "id": "c-1",
                "name": "Test",
                "player_id": "p-1",
                "analysis_config_id": "ac-1",
                "light_provider_id": "lp-1",
                "render_config_id": rc_id,
                "mellow_render_config_id": rc_id,
                "enabled": True,
            }
        ],
        "active_coupling_id": None,
    }
    config.write_text(json.dumps(old_data))

    storage = Storage(config)
    storage.migrate()

    scene = storage.get_scene(rc_id)
    assert scene is not None
    assert scene.effect == "mono_pulse"
    # Scene no longer has a color_mode attribute.
    assert not hasattr(scene, "color_mode")

    # Verify the raw JSON also has the renamed key.
    raw = json.loads(config.read_text())
    assert raw["scenes"][0].get("effect") == "mono_pulse"
    assert "color_mode" not in raw["scenes"][0]


def test_players_key_migrated_to_virtual_players(tmp_path: Path):
    """Old config files with 'players' key are transparently migrated to 'virtual_players'."""
    config = tmp_path / "config.json"
    old_data = {
        "player_latencies": [],
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
        "controllers": [],
        "light_providers": [],
        "analysis_configs": [],
        "render_configs": [],
        "couplings": [],
        "active_coupling_id": None,
    }
    config.write_text(json.dumps(old_data))

    storage = Storage(config)
    players = storage.list_virtual_players()
    assert len(players) == 1
    assert players[0].id == "p-1"
    assert players[0].lms_host == "10.0.0.1"
    assert players[0].player_mac == "aa:bb:cc:dd:ee:ff"
    assert not hasattr(players[0], "name")


def test_light_providers_key_migrated_to_zones(tmp_path: Path):
    """Old config files with 'light_providers' key are migrated to 'zones'."""
    config = tmp_path / "config.json"
    old_data = {
        "player_latencies": [],
        "virtual_players": [],
        "controllers": [],
        "light_providers": [
            {
                "id": "lp-1",
                "name": "My LP",
                "controller_id": "ctrl-1",
                "entertainment_area_id": "ea-1",
                "entertainment_area_name": "Living Room",
                "light_count": 3,
            }
        ],
        "analysis_configs": [],
        "render_configs": [],
        "couplings": [],
        "active_coupling_id": None,
    }
    config.write_text(json.dumps(old_data))

    storage = Storage(config)
    zones = storage.list_zones()
    assert len(zones) == 1
    assert zones[0].name == "My LP"
    assert zones[0].light_count == 3


def test_render_configs_key_migrated_to_scenes(tmp_path: Path):
    """Old config files with 'render_configs' key are migrated to 'scenes'."""
    config = tmp_path / "config.json"
    old_data = {
        "player_latencies": [],
        "virtual_players": [],
        "controllers": [],
        "light_providers": [],
        "analysis_configs": [],
        "render_configs": [
            {
                "id": "rc-1",
                "name": "My RC",
                "effect": "spectrum_rgb",
                "sensitivity": 1.0,
                "brightness_floor": 0.15,
                "bass_hz": 250,
                "mid_hz": 2000,
                "exertion_clip": 3.0,
                "onset_flash_intensity": 0.0,
                "effect_speed": 1.0,
                "effect_decay": 0.3,
            }
        ],
        "couplings": [],
        "active_coupling_id": None,
    }
    config.write_text(json.dumps(old_data))

    storage = Storage(config)
    scenes = storage.list_scenes()
    assert len(scenes) == 1
    assert scenes[0].name == "My RC"
    assert scenes[0].effect == "spectrum_rgb"


def test_migrate_light_provider_id_to_zone_id_on_coupling(tmp_path: Path):
    """Couplings with light_provider_id are migrated to zone_id."""
    config = tmp_path / "config.json"
    zone_entry = {
        "id": "lp-1", "name": "LP", "controller_id": "c",
        "entertainment_area_id": "ea", "entertainment_area_name": "",
        "light_count": 0,
    }
    scene_entry = {
        "id": "sc-1", "name": "SC", "effect": "spectrum_rgb",
        "effect_speed": 1.0, "effect_decay": 0.3, "sensitivity": 1.0,
        "brightness_floor": 0.15, "bass_hz": 250, "mid_hz": 2000,
        "exertion_clip": 3.0, "onset_flash_intensity": 0.0,
    }
    old_data = {
        "player_latencies": [],
        "virtual_players": [],
        "controllers": [],
        "zones": [zone_entry],
        "analysis_configs": [],
        "scenes": [scene_entry],
        "crossfaders": [],
        "couplings": [
            {
                "id": "c-1",
                "name": "Test",
                "player_id": "p-1",
                "analysis_config_id": "ac-1",
                "light_provider_id": "lp-1",
                "render_config_id": "sc-1",
                "enabled": True,
            }
        ],
        "active_coupling_id": None,
    }
    config.write_text(json.dumps(old_data))

    storage = Storage(config)
    storage.migrate()

    c = storage.list_couplings()[0]
    assert c.zone_id == "lp-1"
    assert c.crossfader_id != ""
