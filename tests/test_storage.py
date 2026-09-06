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
