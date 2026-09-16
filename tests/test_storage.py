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
