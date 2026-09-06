"""Tests for PlayerManager shm lifecycle and squeezelite command assembly.

These tests create real files under /dev/shm to verify that teardown and
startup cleanup actually remove them — the same path the production code
uses, so there is no seam between test and production behaviour.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from huesync.models import Profile
from huesync.player_manager import ActiveSession, PlayerManager
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
    """cleanup_orphaned_shm() removes segments whose MAC matches a stored profile."""
    mac = "02:ff:00:de:ad:03"
    shm_path = Path(f"/dev/shm/squeezelite-{mac}")
    shm_path.write_bytes(b"")

    storage = Storage(tmp_path / "config.json")
    storage.save_profile(Profile(player_mac=mac))
    manager = PlayerManager(storage)

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
