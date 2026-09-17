"""Portable current-schema backups: real storage, filesystem, API and CLI boundaries."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from huesync import __git_hash__, __version__
from huesync.api import router
from huesync.backup import (
    BACKUP_VERSION,
    FORMAT,
    MAX_BACKUP_BYTES,
    BackupError,
    configuration_lease,
    decode_backup,
    encode_backup,
    export_configuration,
    export_file,
    restore_configuration,
    validate_backup,
)
from huesync.models import (
    Analyser,
    Controller,
    Coupling,
    Effect,
    EnergyProfile,
    PlayerLatency,
    VirtualPlayer,
    VirtualPlayerType,
    Zone,
)
from huesync.player_manager import PlayerManager
from huesync.schema import COLLECTIONS, SCHEMA_VERSION, validate_current
from huesync.storage import Storage

ROOT = Path(__file__).resolve().parents[1]
SECRET_APP = "private-app-key-never-log"
SECRET_CLIENT = "private-client-key-never-log"


@pytest.fixture()
def configured(tmp_path):
    storage = Storage(tmp_path / "source.json")
    storage.save_controller(
        Controller(
            id="bridge",
            name="Living room",
            host="192.0.2.2",
            app_key=SECRET_APP,
            client_key=SECRET_CLIENT,
        )
    )
    storage.save_zone(Zone(id="zone", controller_id="bridge", entertainment_area_id="area"))
    for identity, kind in [("lms", VirtualPlayerType.LMS), ("airplay", VirtualPlayerType.AIRPLAY)]:
        storage.save_virtual_player(
            VirtualPlayer(
                id=identity,
                type=kind,
                lms_host="192.0.2.10",
                lms_port=3483,
                player_name=identity,
                display_name="Music " + identity,
                player_mac=identity + "-mac",
                alsa_device="hw:CARD=Dummy,DEV=0",
                follow_player_mac="speaker-mac",
            )
        )
    storage.save_analyser(
        Analyser(
            id="analysis",
            bars_source="pcm_pipeline",
            spectrum_backend="cavacore",
            onset_method="superflux",
        )
    )
    storage.save_effect(Effect(id="bright", effect_type="swirl", effect_speed=0.7))
    storage.save_effect(Effect(id="quiet", effect_type="wave", brightness_floor=0.1))
    storage.save_energy_profile(
        EnergyProfile(
            id="energy",
            high_energy_effect_id="bright",
            low_energy_effect_id="quiet",
            blend_start=0.2,
        )
    )
    for player in ("lms", "airplay"):
        storage.save_coupling(
            Coupling(
                id=player + "-coupling",
                player_id=player,
                zone_id="zone",
                analyser_id="analysis",
                energy_profile_id="energy",
            )
        )
    storage.save_player_latency(
        PlayerLatency(
            player_mac="speaker-mac", name="Speaker", fixed_delay_ms=1234, speaker_ip="192.0.2.11"
        )
    )
    return storage


def test_all_fields_credentials_and_round_trip(configured, tmp_path, caplog):
    original = configured.read_configuration()
    configured.set_active_coupling_id("lms-coupling")
    backup = export_configuration(configured)
    assert backup["format"] == FORMAT
    assert backup["backup_version"] == BACKUP_VERSION == 1
    assert backup["schema_version"] == SCHEMA_VERSION
    assert backup["huesync_version"] == __version__
    assert backup["huesync_commit"] == __git_hash__
    assert backup["created_at"].endswith("Z") and backup["contains_secrets"] is True
    assert backup["configuration"] == original  # only active-session state omitted
    for key, model in COLLECTIONS.items():
        rows = backup["configuration"][key]
        assert rows
        for row in rows:
            assert set(row) == set(
                model.from_dict(row).to_dict()
            )  # all fields, not a whitelist subset
    target = Storage(tmp_path / "target.json")
    raw_previous = target.path.read_bytes()
    safety = restore_configuration(target, decode_backup(encode_backup(backup)))
    assert safety.read_bytes() == raw_previous
    assert safety.stat().st_mode & 0o777 == 0o600
    assert target.path.stat().st_mode & 0o777 == 0o600
    assert target.read_configuration() == original
    assert target.get_controller("bridge").app_key == SECRET_APP
    assert target.get_controller("bridge").client_key == SECRET_CLIENT
    assert target.get_virtual_player("airplay").type == VirtualPlayerType.AIRPLAY
    validate_current(target.read_configuration(), references=True)
    restore_configuration(target, backup)
    assert target.read_configuration() == original
    assert SECRET_APP not in caplog.text and SECRET_CLIENT not in caplog.text


def test_export_rejects_invalid_references(configured):
    # Simulate an already-corrupt file; protected deletes now reject this.
    data = configured.read_configuration()
    data['controllers'] = []
    configured.path.write_text(json.dumps(data))
    with pytest.raises(BackupError, match="Cannot export"):
        export_configuration(configured)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b.update(format="wrong"),
        lambda b: b.update(backup_version=2),
        lambda b: b.update(backup_version=True),
        lambda b: b.update(schema_version=2),
        lambda b: b.update(contains_secrets=False),
        lambda b: b.update(created_at="not a timestamp"),
        lambda b: b.update(unexpected="private-app-key-never-log"),
        lambda b: b.pop("huesync_commit"),
        lambda b: b["configuration"].pop("effects"),
        lambda b: b["configuration"]["controllers"][0].pop("app_key"),
        lambda b: b["configuration"]["zones"][0].update(controller_id="missing"),
        lambda b: b["configuration"]["energy_profiles"][0].update(low_energy_effect_id="missing"),
        lambda b: b["configuration"]["couplings"][0].update(player_id="missing"),
        lambda b: b["configuration"].update(active_coupling_id="lms-coupling"),
    ],
)
def test_invalid_restore_leaves_bytes_unchanged(configured, tmp_path, mutation):
    backup = export_configuration(configured)
    mutation(backup)
    target = Storage(tmp_path / "target.json")
    before = target.path.read_bytes()
    with pytest.raises(BackupError) as error:
        restore_configuration(target, backup)
    assert SECRET_APP not in str(error.value)
    assert target.path.read_bytes() == before
    assert not list(tmp_path.glob("target.json.pre-restore.*"))


@pytest.mark.parametrize(
    "raw",
    [b"not-json", b'{"a":1,"a":2}', b"[]", b"\xff", b"[" * 2000, b"x" * (MAX_BACKUP_BYTES + 1)],
)
def test_malformed_json_rejected(raw):
    with pytest.raises(BackupError):
        decode_backup(raw)


def test_rename_failure_preserves_safety_and_original(configured, tmp_path):
    backup = export_configuration(configured)
    target = Storage(tmp_path / "target.json")
    before = target.path.read_bytes()

    def fail_replace(*args):
        (safety,) = list(tmp_path.glob("target.json.pre-restore.*"))
        assert safety.read_bytes() == before  # already created before attempted commit
        raise OSError("sensitive filesystem exception")

    with patch("huesync.backup.os.replace", side_effect=fail_replace):
        with pytest.raises(BackupError, match="could not commit"):
            restore_configuration(target, backup)
    assert target.path.read_bytes() == before
    assert len(list(tmp_path.glob("target.json.pre-restore.*"))) == 1
    assert not list(tmp_path.glob(".huesync-restore-*"))


def test_export_file_private_and_no_overwrite(configured, tmp_path):
    destination = tmp_path / "portable.json"
    old_umask = os.umask(0)
    try:
        export_file(configured, destination)
    finally:
        os.umask(old_umask)
    assert destination.stat().st_mode & 0o777 == 0o600
    before = destination.read_bytes()
    with pytest.raises(BackupError):
        export_file(configured, destination)
    assert destination.read_bytes() == before
    symlink = tmp_path / "link.json"
    symlink.symlink_to(destination)
    with pytest.raises(BackupError):
        export_file(configured, symlink)


def cli(*args):
    return subprocess.run(
        [sys.executable, "-B", "-m", "huesync.backup", *map(str, args)],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_cli_round_trip_check_and_runtime_lease(configured, tmp_path):
    portable = tmp_path / "portable.json"
    result = cli("--config", configured.path, "export", portable)
    assert result.returncode == 0, result.stderr
    assert SECRET_APP not in result.stdout + result.stderr
    assert portable.stat().st_mode & 0o777 == 0o600
    backup = decode_backup(portable.read_bytes())
    assert backup["configuration"] == export_configuration(configured)["configuration"]
    target = Storage(tmp_path / "target.json")
    before = target.path.read_bytes()
    files_before = set(tmp_path.iterdir())
    result = cli("--config", target.path, "import", "--check", portable)
    assert result.returncode == 0
    assert set(tmp_path.iterdir()) == files_before and target.path.read_bytes() == before
    with configuration_lease(target.path):
        result = cli("--config", target.path, "import", portable)
        assert result.returncode != 0 and "stop HueSync" in result.stderr
        assert target.path.read_bytes() == before
    result = cli("--config", target.path, "import", portable)
    assert result.returncode == 0, result.stderr
    assert target.read_configuration() == backup["configuration"]
    assert cli("--config", target.path, "import", portable).returncode == 0


def api(storage):
    app = FastAPI()
    app.include_router(router)
    app.state.storage = storage
    app.state.player_manager = PlayerManager(storage)
    return app


def test_api_headers_restore_and_secrets(configured, tmp_path, caplog):
    with TestClient(api(configured)) as source:
        response = source.get("/api/config/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="huesync-backup-'
    )
    assert SECRET_APP in response.text and SECRET_CLIENT in response.text
    target = Storage(tmp_path / "target.json")
    app = api(target)
    with TestClient(app) as client:
        result = client.post(
            "/api/config/import",
            content=response.content,
            headers={"Content-Type": "application/json"},
        )
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        assert result.json()["runtime"] == "inactive"
        assert result.json()["restart_required"] is False
        assert target.read_configuration() == response.json()["configuration"]
        # No stale in-memory entity store: the same process resolves imported objects.
        assert client.get("/api/zones").json()[0]["controller_id"] == "bridge"
        assert app.state.player_manager.storage.get_controller("bridge").app_key == SECRET_APP
        bad = copy.deepcopy(response.json())
        bad["configuration"]["zones"][0]["controller_id"] = SECRET_APP
        before = target.path.read_bytes()
        result = client.post("/api/config/import", json=bad)
        assert result.status_code == 422 and SECRET_APP not in result.text
        assert target.path.read_bytes() == before
    assert SECRET_APP not in caplog.text and SECRET_CLIENT not in caplog.text


def test_api_active_or_retiring_runtime_refused_without_reload(configured, tmp_path):
    backup = export_configuration(configured)
    target = Storage(tmp_path / "target.json")
    app = api(target)
    manager = app.state.player_manager
    # Real manager readiness checks ownership itself, including a stopping session.
    for stopping in (False, True):
        session = SimpleNamespace(stopping=stopping)
        manager._active = session
        before = target.path.read_bytes()
        with (
            TestClient(app) as client,
            patch.object(manager, "deactivate", side_effect=RuntimeError("must not reload")),
        ):
            result = client.post("/api/config/import", json=backup)
        assert result.status_code == 409
        assert manager._active is session and target.path.read_bytes() == before
    manager._active = None
    with TestClient(app) as client:
        assert client.post("/api/config/import", json=backup).status_code == 200


def test_api_rejects_size_and_media_type(configured):
    before = configured.path.read_bytes()
    with TestClient(api(configured)) as client:
        assert client.post("/api/config/import", content=b"{}").status_code == 415
        result = client.post(
            "/api/config/import",
            content=b" " * (MAX_BACKUP_BYTES + 1),
            headers={"Content-Type": "application/json"},
        )
        assert result.status_code == 413 and result.headers["cache-control"] == "no-store"
    assert configured.path.read_bytes() == before


def test_future_schema_and_history_not_accepted(configured):
    # Exact current keys only. Unknown collection aliases are rejected, not migrated.
    backup = export_configuration(configured)
    assert set(backup["configuration"]) == set(COLLECTIONS) | {
        "schema_version",
        "active_coupling_id",
    }
    backup["configuration"]["unknown_collection"] = []
    with pytest.raises(BackupError):
        validate_backup(backup)


def test_restore_waits_for_inflight_api_mutation(configured, tmp_path):
    import asyncio

    import httpx

    target = Storage(tmp_path / "target.json")
    app = api(target)
    backup = export_configuration(configured)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def pairing(*args, **kwargs):
            entered.set()
            await release.wait()
            return Controller(id="old-inflight", host="192.0.2.9")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            with patch("huesync.api.hue_bridge.pair", side_effect=pairing):
                pending = asyncio.create_task(
                    client.post("/api/controllers/pair", json={"host": "192.0.2.9"})
                )
                await asyncio.wait_for(entered.wait(), 5)
                exporting = asyncio.create_task(client.get("/api/config/export"))
                restoring = asyncio.create_task(client.post("/api/config/import", json=backup))
                await asyncio.sleep(0.02)
                assert not restoring.done() and not exporting.done()
                release.set()
                assert (await pending).status_code == 201
                exported = await exporting
                assert exported.status_code == 200
                assert exported.json()["configuration"]["controllers"][0]["id"] == "old-inflight"
                assert (await restoring).status_code == 200
        # The old in-flight operation completed BEFORE replacement, not after it.
        assert target.read_configuration() == backup["configuration"]

    asyncio.run(scenario())


def test_running_application_excludes_offline_restore(configured, tmp_path, monkeypatch):
    from huesync.app import app

    portable = tmp_path / "portable.json"
    export_file(configured, portable)
    manager = PlayerManager(configured)
    monkeypatch.setattr(manager, "cleanup_orphaned_shm", lambda: None)
    monkeypatch.setattr(app.state, "storage", configured)
    monkeypatch.setattr(app.state, "player_manager", manager)
    with TestClient(app):
        result = cli("--config", configured.path, "import", portable)
        assert result.returncode != 0 and "stop HueSync" in result.stderr
    # Successful shutdown releases the actual app-owned lease.
    assert cli("--config", configured.path, "import", portable).returncode == 0


def test_api_write_failure_leaves_idle_runtime_and_original(configured, tmp_path):
    target = Storage(tmp_path / "target.json")
    app = api(target)
    before = target.path.read_bytes()
    with TestClient(app) as client, patch("huesync.backup.os.replace", side_effect=OSError):
        result = client.post("/api/config/import", json=export_configuration(configured))
    assert result.status_code == 409
    assert result.headers["cache-control"] == "no-store"
    assert target.path.read_bytes() == before
    assert app.state.player_manager.configuration_restore_ready
    assert list(tmp_path.glob("target.json.pre-restore.*"))


def test_offline_restore_recovers_corrupt_current_file(configured, tmp_path):
    portable = tmp_path / "portable.json"
    export_file(configured, portable)
    target = tmp_path / "corrupt.json"
    original = b"broken current configuration"
    target.write_bytes(original)
    result = cli("--config", target, "import", portable)
    assert result.returncode == 0, result.stderr
    (safety,) = list(tmp_path.glob("corrupt.json.pre-restore.*"))
    assert safety.read_bytes() == original
    assert Storage(target).read_configuration() == export_configuration(configured)["configuration"]


def test_each_restore_retains_its_own_private_safety_copy(configured, tmp_path):
    import hashlib

    target = Storage(tmp_path / "target.json")
    before = target.path.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    # Simulate an inaccessible safety copy left by a different administrative user.
    old = tmp_path / f"target.json.pre-restore.{digest}.bak"
    old.write_bytes(before)
    old.chmod(0)
    backup = export_configuration(configured)
    first = restore_configuration(target, backup)
    second = restore_configuration(target, backup)
    assert first != second and first != old
    assert first.read_bytes() == before
    assert first.stat().st_mode & 0o777 == second.stat().st_mode & 0o777 == 0o600
    old.chmod(0o600)
    assert old.read_bytes() == before

def test_auto_follow_mode_and_manual_target_survive_backup(configured, tmp_path):
    player = configured.get_virtual_player("lms")
    player.follow_mode = "sync_group"
    configured.save_virtual_player(player)
    target = Storage(tmp_path / "restored.json")
    restore_configuration(target, export_configuration(configured))
    restored = target.get_virtual_player("lms")
    assert restored.follow_mode == "sync_group"
    assert restored.follow_player_mac == "speaker-mac"
