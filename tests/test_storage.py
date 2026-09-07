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
    player = VirtualPlayer(name="Living room")
    storage.save_virtual_player(player)

    fetched = storage.get_virtual_player(player.id)
    assert fetched is not None
    assert fetched.name == "Living room"
    assert fetched.id == player.id


def test_delete_virtual_player():
    storage = make_storage()
    player = VirtualPlayer(name="Test")
    storage.save_virtual_player(player)
    storage.delete_virtual_player(player.id)
    assert storage.get_virtual_player(player.id) is None


def test_migrate_creates_mellow_render_config(tmp_path: Path):
    """migrate() creates a cloned RC with the mellow colour mode when mellow differs from active."""
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

    # Coupling now has mellow_render_config_id set (different RC was cloned).
    couplings = storage.list_couplings()
    assert len(couplings) == 1
    c = couplings[0]
    assert c.mellow_render_config_id != ""
    assert c.mellow_render_config_id != rc_id  # new, separate RC

    # Mix params were moved from the old RC to the coupling.
    assert c.mix_low_threshold == 0.2
    assert c.mix_high_threshold == 0.6
    assert c.mix_ema_alpha == 0.05

    # A new RC was created with the mellow colour mode.
    rcs = storage.list_render_configs()
    mellow_rc = next((r for r in rcs if r.id == c.mellow_render_config_id), None)
    assert mellow_rc is not None
    assert mellow_rc.effect == "mono_pulse"
    assert "(Mellow)" in mellow_rc.name

    # Calling migrate() again is a no-op (idempotent).
    storage.migrate()
    assert len(storage.list_couplings()) == 1
    assert storage.list_couplings()[0].mellow_render_config_id == c.mellow_render_config_id


def test_migrate_same_colour_mode_reuses_rc(tmp_path: Path):
    """migrate() sets mellow_render_config_id = render_config_id when modes are the same."""
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
                "mellow_colour_mode": "spectrum_rgb",  # same → reuse
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
    # Same colour mode: mellow RC equals active RC.
    assert c.mellow_render_config_id == rc_id
    # Only the original RC exists (no clone was created).
    assert len(storage.list_render_configs()) == 1


def test_migrate_renames_color_mode_to_effect(tmp_path: Path):
    """migrate() renames color_mode → effect in render_config raw dicts."""
    import json

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

    rc = storage.get_render_config(rc_id)
    assert rc is not None
    assert rc.effect == "mono_pulse"
    # RenderConfig no longer has a color_mode attribute.
    assert not hasattr(rc, "color_mode")

    # Verify the raw JSON also has the renamed key.
    raw = json.loads(config.read_text())
    assert raw["render_configs"][0].get("effect") == "mono_pulse"
    assert "color_mode" not in raw["render_configs"][0]


def test_players_key_migrated_to_virtual_players(tmp_path: Path):
    """Old config files with 'players' key are transparently migrated to 'virtual_players'."""
    import json

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
    assert players[0].name == "Old Player"
