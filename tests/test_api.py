"""Tests for the JSON REST API (src/huesync/api.py).

Uses FastAPI's TestClient so no real network connections are made.
PlayerManager is mocked to avoid needing actual processes or bridges.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from fastapi.testclient import TestClient

from huesync.app import app
from huesync.models import (
    Analyser,
    Coupling,
    Crossfader,
    Scene,
    VirtualPlayer,
    Zone,
)
from huesync.storage import Storage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "config.json")


def _make_mock_manager() -> MagicMock:
    manager = MagicMock()
    manager.detected_sync_master = None
    manager.latency_warning = None
    type(manager).applied_delay_ms = PropertyMock(return_value=0)
    type(manager).bridge_connected = PropertyMock(return_value=False)
    type(manager).process_status = PropertyMock(return_value={"squeezelite": False, "cava": False})
    # WebSocket frame properties
    type(manager).last_colours = PropertyMock(return_value=[])
    type(manager).last_bars = PropertyMock(return_value=[])
    type(manager).last_onset = PropertyMock(return_value=False)
    type(manager).last_pcm_onset = PropertyMock(return_value=False)
    type(manager).last_onset_bass = PropertyMock(return_value=False)
    type(manager).last_onset_mid = PropertyMock(return_value=False)
    type(manager).last_onset_treble = PropertyMock(return_value=False)
    type(manager).last_mix = PropertyMock(return_value=0.0)
    # WebSocket status properties
    type(manager).active_coupling_id = PropertyMock(return_value=None)
    type(manager).active_coupling_name = PropertyMock(return_value=None)
    type(manager).detected_sync_master_name = PropertyMock(return_value=None)
    type(manager).active_color_mode = PropertyMock(return_value=None)
    type(manager).active_effect = PropertyMock(return_value=None)
    type(manager).follower_warning = PropertyMock(return_value=None)
    type(manager).active_bass_hz = PropertyMock(return_value=None)
    type(manager).active_mid_hz = PropertyMock(return_value=None)
    type(manager).active_onset_method = PropertyMock(return_value=None)
    type(manager).active_lower_cutoff_freq = PropertyMock(return_value=None)
    type(manager).active_higher_cutoff_freq = PropertyMock(return_value=None)
    # Async methods
    manager.activate_coupling = AsyncMock()
    manager.deactivate = AsyncMock()
    manager.restart_cava = AsyncMock()
    manager.refresh_probe = AsyncMock()
    # Sync live-update methods
    manager.update_onset_pipeline = MagicMock()
    manager.update_render = MagicMock()
    return manager


@pytest.fixture()
def client(tmp_path: Path):
    storage = _make_storage(tmp_path)
    manager = _make_mock_manager()
    app.state.storage = storage
    app.state.player_manager = manager
    with TestClient(app) as c:
        c._manager = manager  # expose for test inspection
        c._storage = storage
        yield c


# ---------------------------------------------------------------------------
# Player latencies
# ---------------------------------------------------------------------------


def test_list_player_latencies_empty(client: TestClient):
    resp = client.get("/api/player-latencies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_player_latency_returns_201(client: TestClient):
    payload = {"player_mac": "AA:BB:CC:DD:EE:FF", "strategy": "fixed", "fixed_delay_ms": 1500}
    resp = client.post("/api/player-latencies", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    # MAC is stored lower-cased and stripped.
    assert body["player_mac"] == "aa:bb:cc:dd:ee:ff"
    assert body["fixed_delay_ms"] == 1500
    client._manager.refresh_probe.assert_called()


def test_patch_player_latency_404_for_unknown(client: TestClient):
    resp = client.patch("/api/player-latencies/00:11:22:33:44:55", json={"fixed_delay_ms": 999})
    assert resp.status_code == 404


def test_delete_player_latency_returns_204(client: TestClient):
    # Create an entry first.
    payload = {"player_mac": "11:22:33:44:55:66", "strategy": "fixed", "fixed_delay_ms": 2000}
    client.post("/api/player-latencies", json=payload)

    resp = client.delete("/api/player-latencies/11:22:33:44:55:66")
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_get_status_returns_correct_shape(client: TestClient):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "active_coupling_id" in body
    assert "active_coupling_name" in body
    assert "sync_master" in body
    assert "applied_delay_ms" in body
    assert "latency_warning" in body
    assert "processes" in body
    assert "squeezelite" in body["processes"]
    assert "cava" in body["processes"]
    assert "bridge_connected" in body


# ---------------------------------------------------------------------------
# Controllers
# ---------------------------------------------------------------------------


def test_list_controllers_empty(client: TestClient):
    resp = client.get("/api/controllers")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_and_get_controller(client: TestClient):
    payload = {"name": "Living Room Bridge", "host": "192.168.1.2", "type": "hue"}
    resp = client.post("/api/controllers", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Living Room Bridge"
    assert body["host"] == "192.168.1.2"
    assert body["type"] == "hue"
    controller_id = body["id"]

    resp2 = client.get(f"/api/controllers/{controller_id}")
    assert resp2.status_code == 200
    assert resp2.json()["id"] == controller_id


# ---------------------------------------------------------------------------
# VirtualPlayers
# ---------------------------------------------------------------------------


def test_create_and_list_virtual_players(client: TestClient):
    payload = {"lms_host": "10.0.0.5"}
    resp = client.post("/api/virtual-players", json=payload)
    assert resp.status_code == 201

    resp2 = client.get("/api/virtual-players")
    assert resp2.status_code == 200
    assert len(resp2.json()) == 1
    assert resp2.json()[0]["lms_host"] == "10.0.0.5"


def test_create_and_get_virtual_player(client: TestClient):
    payload = {"lms_host": "10.0.0.6", "lms_port": 9000}
    resp = client.post("/api/virtual-players", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    player_id = body["id"]
    # MAC should be auto-generated if not provided
    assert body["player_mac"] != ""
    # type defaults to LMS
    assert body["type"] == "LMS"

    resp2 = client.get(f"/api/virtual-players/{player_id}")
    assert resp2.status_code == 200
    assert resp2.json()["type"] == "LMS"


def test_virtual_player_rejects_unknown_type(client: TestClient):
    payload = {"lms_host": "10.0.0.7", "type": "WLED"}
    resp = client.post("/api/virtual-players", json=payload)
    assert resp.status_code == 422


def test_virtual_player_has_no_name_field(client: TestClient):
    payload = {"lms_host": "10.0.0.8"}
    resp = client.post("/api/virtual-players", json=payload)
    assert resp.status_code == 201
    assert "name" not in resp.json()


def test_create_virtual_player_with_follow_player_mac(client: TestClient):
    payload = {"lms_host": "10.0.0.9", "follow_player_mac": "aa:bb:cc:dd:ee:ff"}
    resp = client.post("/api/virtual-players", json=payload)
    assert resp.status_code == 201
    assert resp.json()["follow_player_mac"] == "aa:bb:cc:dd:ee:ff"


def test_patch_virtual_player_follow_player_mac(client: TestClient):
    create_resp = client.post("/api/virtual-players", json={"lms_host": "10.0.0.10"})
    player_id = create_resp.json()["id"]

    patch_resp = client.patch(
        f"/api/virtual-players/{player_id}",
        json={"follow_player_mac": "11:22:33:44:55:66"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["follow_player_mac"] == "11:22:33:44:55:66"

    get_resp = client.get(f"/api/virtual-players/{player_id}")
    assert get_resp.json()["follow_player_mac"] == "11:22:33:44:55:66"


def test_patch_virtual_player_clear_follow_player_mac(client: TestClient):
    create_resp = client.post(
        "/api/virtual-players",
        json={"lms_host": "10.0.0.11", "follow_player_mac": "aa:bb:cc:dd:ee:ff"},
    )
    player_id = create_resp.json()["id"]

    patch_resp = client.patch(
        f"/api/virtual-players/{player_id}",
        json={"follow_player_mac": ""},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["follow_player_mac"] == ""


def test_lms_players_endpoint_missing_host(client: TestClient):
    resp = client.get("/api/lms/players")
    assert resp.status_code == 422  # missing required query param


def test_lms_players_endpoint_empty_host(client: TestClient):
    resp = client.get("/api/lms/players?host=")
    assert resp.status_code == 400


def test_lms_players_endpoint_returns_list(client: TestClient, monkeypatch):
    from huesync import api as api_module

    fake_players = [
        {"playerid": "aa:bb:cc:dd:ee:ff", "name": "Sonos Living Room"},
        {"playerid": "11:22:33:44:55:66", "name": "Kitchen"},
    ]

    async def mock_to_thread(fn, *args, **kwargs):
        return fake_players

    monkeypatch.setattr(api_module.asyncio, "to_thread", mock_to_thread)

    resp = client.get("/api/lms/players?host=10.0.0.1")
    assert resp.status_code == 200
    assert resp.json() == fake_players


# ---------------------------------------------------------------------------
# Zones (was LightProviders)
# ---------------------------------------------------------------------------


def test_create_and_get_zone(client: TestClient):
    payload = {
        "name": "Living Room EA",
        "controller_id": "ctrl-1",
        "entertainment_area_id": "ea-1",
        "entertainment_area_name": "Living Room",
        "light_count": 4,
    }
    resp = client.post("/api/zones", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    zone_id = body["id"]
    assert body["name"] == "Living Room EA"
    assert body["light_count"] == 4

    resp2 = client.get(f"/api/zones/{zone_id}")
    assert resp2.status_code == 200
    assert resp2.json()["entertainment_area_name"] == "Living Room"


# ---------------------------------------------------------------------------
# Analysers
# ---------------------------------------------------------------------------


def test_create_and_get_analyser(client: TestClient):
    payload = {"name": "Fast Onset", "onset_method": "superflux", "bars": 20}
    resp = client.post("/api/analysers", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    ac_id = body["id"]
    assert body["onset_method"] == "superflux"
    assert body["bars"] == 20

    resp2 = client.get(f"/api/analysers/{ac_id}")
    assert resp2.status_code == 200
    assert resp2.json()["name"] == "Fast Onset"


# ---------------------------------------------------------------------------
# Scenes (was RenderConfigs)
# ---------------------------------------------------------------------------


def test_create_and_get_scene(client: TestClient):
    payload = {"name": "Vivid", "effect": "spectrum_rgb", "sensitivity": 1.5}
    resp = client.post("/api/scenes", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    scene_id = body["id"]
    assert body["sensitivity"] == 1.5

    resp2 = client.get(f"/api/scenes/{scene_id}")
    assert resp2.status_code == 200
    assert resp2.json()["name"] == "Vivid"


# ---------------------------------------------------------------------------
# Couplings
# ---------------------------------------------------------------------------


def _make_full_coupling(storage: Storage) -> Coupling:
    """Create and persist all entities required for a Coupling, return the Coupling."""
    player = VirtualPlayer(lms_host="10.0.0.1", player_mac="aa:bb:cc:dd:ee:ff")
    zone = Zone(name="Z", controller_id="ctrl-1", entertainment_area_id="ea-1")
    ac = Analyser(name="AC")
    scene = Scene(name="SC")
    crossfader = Crossfader(name="CF", active_scene_id=scene.id)
    storage.save_virtual_player(player)
    storage.save_zone(zone)
    storage.save_analyser(ac)
    storage.save_scene(scene)
    storage.save_crossfader(crossfader)
    coupling = Coupling(
        name="Test Coupling",
        player_id=player.id,
        zone_id=zone.id,
        analyser_id=ac.id,
        crossfader_id=crossfader.id,
    )
    storage.save_coupling(coupling)
    return coupling


def test_create_coupling(client: TestClient):
    player = VirtualPlayer(lms_host="10.0.0.1")
    zone = Zone(name="Z", controller_id="c1", entertainment_area_id="ea-1")
    ac = Analyser(name="AC")
    scene = Scene(name="SC")
    crossfader = Crossfader(name="CF", active_scene_id=scene.id)
    client._storage.save_virtual_player(player)
    client._storage.save_zone(zone)
    client._storage.save_analyser(ac)
    client._storage.save_scene(scene)
    client._storage.save_crossfader(crossfader)

    payload = {
        "name": "My Coupling",
        "player_id": player.id,
        "analyser_id": ac.id,
        "zone_id": zone.id,
        "crossfader_id": crossfader.id,
    }
    resp = client.post("/api/couplings", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "My Coupling"
    assert body["player_id"] == player.id


def test_get_coupling_not_found(client: TestClient):
    resp = client.get("/api/couplings/no-such-id")
    assert resp.status_code == 404


def test_activate_coupling_missing_entities(client: TestClient):
    """Activating a coupling with missing FK entities must return 422."""
    coupling = Coupling(
        name="Broken",
        player_id="missing",
        zone_id="missing",
        analyser_id="missing",
        crossfader_id="missing",
    )
    client._storage.save_coupling(coupling)
    client._manager.activate_coupling.side_effect = ValueError("missing Player")

    resp = client.post(f"/api/couplings/{coupling.id}/activate")
    assert resp.status_code == 422


def test_activate_coupling_success(client: TestClient):
    coupling = _make_full_coupling(client._storage)

    resp = client.post(f"/api/couplings/{coupling.id}/activate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_id"] == coupling.id
    client._manager.activate_coupling.assert_awaited_once()


def test_delete_coupling_deactivates_if_active(client: TestClient):
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    resp = client.delete(f"/api/couplings/{coupling.id}")
    assert resp.status_code == 204
    client._manager.deactivate.assert_awaited_once()
    assert client._storage.get_coupling(coupling.id) is None


def test_deactivate_coupling_endpoint(client: TestClient):
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    resp = client.post("/api/couplings/deactivate")
    assert resp.status_code == 200
    assert resp.json() == {"active_id": None}
    client._manager.deactivate.assert_awaited_once()
    assert client._storage.get_active_coupling_id() is None


def test_patch_coupling_cava_field_triggers_restart_cava(client: TestClient):
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    resp = client.patch(f"/api/couplings/{coupling.id}", json={"bars": 40})
    assert resp.status_code == 200
    client._manager.restart_cava.assert_awaited_once()
    client._manager.deactivate.assert_not_awaited()


def test_patch_coupling_deactivate_field_deactivates(client: TestClient):
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    resp = client.patch(f"/api/couplings/{coupling.id}", json={"lms_host": "10.0.0.99"})
    assert resp.status_code == 200
    client._manager.deactivate.assert_awaited_once()


def test_patch_coupling_analyser_id_restarts_cava_not_deactivate(client: TestClient):
    """Swapping analyser_id on an active coupling must restart cava and
    rebuild the onset pipeline, but must NOT call deactivate()."""
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    new_ac = Analyser(name="AC2")
    client._storage.save_analyser(new_ac)

    resp = client.patch(
        f"/api/couplings/{coupling.id}",
        json={"analyser_id": new_ac.id},
    )
    assert resp.status_code == 200
    client._manager.deactivate.assert_not_awaited()
    client._manager.restart_cava.assert_awaited_once()
    client._manager.update_onset_pipeline.assert_called_once()
    client._manager.update_render.assert_called_once()

    saved = client._storage.get_coupling(coupling.id)
    assert saved is not None and saved.analyser_id == new_ac.id


def test_patch_coupling_crossfader_id_update_render_only(client: TestClient):
    """Swapping crossfader_id must call update_render only — no cava restart,
    no deactivate."""
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    new_scene = Scene(name="SC2")
    client._storage.save_scene(new_scene)
    new_cf = Crossfader(name="CF2", active_scene_id=new_scene.id)
    client._storage.save_crossfader(new_cf)

    resp = client.patch(
        f"/api/couplings/{coupling.id}",
        json={"crossfader_id": new_cf.id},
    )
    assert resp.status_code == 200
    client._manager.deactivate.assert_not_awaited()
    client._manager.restart_cava.assert_not_awaited()
    client._manager.update_render.assert_called_once()

    saved = client._storage.get_coupling(coupling.id)
    assert saved is not None and saved.crossfader_id == new_cf.id


def test_ac_swap_session_remains_active(client: TestClient):
    """After swapping analyser_id on an active coupling the session is
    not deactivated, restart_cava + update_onset_pipeline are called once
    (the live-update path), and the profile saved to storage is rebuilt from
    the NEW Analyser's settings.

    This is the canonical regression guard for the 'frozen lights' bug fixed
    in the 2026-09-06 routing refactor: analyser_id was in
    _C_DEACTIVATE_FIELDS (wrong) instead of _C_LIVE_FK_FIELDS, and
    restart_cava() used stale session.coupling (wrong).
    """
    # AC1: default bars=30, onset_delta=0.1
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    # AC2: deliberately different settings so we can distinguish old from new
    # in the saved profile.
    ac2 = Analyser(name="AC2", bars=50, onset_delta=0.5)
    client._storage.save_analyser(ac2)

    resp = client.patch(
        f"/api/couplings/{coupling.id}",
        json={"analyser_id": ac2.id},
    )
    assert resp.status_code == 200

    # Session must still be active — deactivate() is the wrong path.
    assert client._storage.get_active_coupling_id() == coupling.id
    client._manager.deactivate.assert_not_awaited()

    # Live-update path: cava restarted (picks up new bars) and onset pipeline
    # rebuilt (picks up new onset_delta).
    client._manager.restart_cava.assert_awaited_once()
    client._manager.update_onset_pipeline.assert_called_once()

    # Coupling in storage now points to AC2.
    saved_coupling = client._storage.get_coupling(coupling.id)
    assert saved_coupling is not None
    assert saved_coupling.analyser_id == ac2.id

    # AC2's settings are now in storage — verifies _apply_coupling_action()
    # used the UPDATED coupling (new Analyser ID), not the stale in-memory one.
    saved_ac2 = client._storage.get_analyser(ac2.id)
    assert saved_ac2 is not None
    assert saved_ac2.bars == 50          # AC2, not AC1's default 30
    assert saved_ac2.onset_delta == 0.5  # AC2, not AC1's default 0.1


# ---------------------------------------------------------------------------
# Clone coupling
# ---------------------------------------------------------------------------


def test_clone_coupling_returns_new_id(client: TestClient):
    """The cloned Coupling must have a different ID than the original."""
    coupling = _make_full_coupling(client._storage)

    resp = client.post(f"/api/couplings/{coupling.id}/clone")
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != coupling.id
    assert body["name"] == f"{coupling.name} (copy)"


def test_clone_coupling_analysis_and_scene_configs_are_independent(client: TestClient):
    """Cloned Analyser and Scene have new IDs but identical field values.
    Cloned Crossfader has a new ID and points to the new Scene.
    """
    coupling = _make_full_coupling(client._storage)
    orig_ac = client._storage.get_analyser(coupling.analyser_id)
    orig_cf = client._storage.get_crossfader(coupling.crossfader_id)
    orig_scene = client._storage.get_scene(orig_cf.active_scene_id) if orig_cf else None

    resp = client.post(f"/api/couplings/{coupling.id}/clone")
    assert resp.status_code == 201
    body = resp.json()

    new_ac = client._storage.get_analyser(body["analyser_id"])
    new_cf = client._storage.get_crossfader(body["crossfader_id"])
    new_scene = client._storage.get_scene(new_cf.active_scene_id) if new_cf else None

    # New IDs — not the same sub-entity objects.
    assert new_ac is not None
    assert new_cf is not None
    assert new_scene is not None
    assert orig_ac is not None
    assert orig_scene is not None
    assert new_ac.id != orig_ac.id
    assert new_cf.id != orig_cf.id
    assert new_scene.id != orig_scene.id

    # All field values identical to the originals.
    assert new_ac.onset_method == orig_ac.onset_method
    assert new_ac.onset_delta == orig_ac.onset_delta
    assert new_ac.onset_alpha == orig_ac.onset_alpha
    assert new_ac.bars == orig_ac.bars
    assert new_ac.lower_cutoff_freq == orig_ac.lower_cutoff_freq
    assert new_ac.higher_cutoff_freq == orig_ac.higher_cutoff_freq
    assert new_scene.effect == orig_scene.effect
    assert new_scene.sensitivity == orig_scene.sensitivity
    assert new_scene.brightness_floor == orig_scene.brightness_floor
    assert new_scene.bass_hz == orig_scene.bass_hz
    assert new_scene.mid_hz == orig_scene.mid_hz
    assert new_scene.exertion_clip == orig_scene.exertion_clip

    # Player and Zone are shared (same IDs).
    assert body["player_id"] == coupling.player_id
    assert body["zone_id"] == coupling.zone_id


def test_clone_coupling_modifying_clone_does_not_affect_original(client: TestClient):
    """Mutating the clone's Analyser must not change the original's."""
    coupling = _make_full_coupling(client._storage)

    resp = client.post(f"/api/couplings/{coupling.id}/clone")
    assert resp.status_code == 201
    clone_body = resp.json()

    # Patch the clone's onset_method via the coupling PATCH endpoint.
    patch_resp = client.patch(
        f"/api/couplings/{clone_body['id']}",
        json={"onset_method": "multiband"},
    )
    assert patch_resp.status_code == 200

    # Clone's Analyser now has multiband.
    clone_ac = client._storage.get_analyser(clone_body["analyser_id"])
    assert clone_ac.onset_method == "multiband"

    # Original's Analyser still has combined (unchanged).
    orig_ac = client._storage.get_analyser(coupling.analyser_id)
    assert orig_ac.onset_method == "combined"


# ---------------------------------------------------------------------------
# WebSocket /ws/preview — status frame regression guard
# ---------------------------------------------------------------------------


def _read_ws_status(client: TestClient, max_messages: int = 40) -> dict | None:
    """Connect to /ws/preview and return the first 'status' type message."""
    with client.websocket_connect("/ws/preview") as ws:
        for _ in range(max_messages):
            msg = ws.receive_json()
            if msg.get("type") == "status":
                return msg
    return None


def test_ws_preview_status_frame_has_color_mode_and_band_hz(client: TestClient):
    """WebSocket /ws/preview status frame must include color_mode, bass_hz, mid_hz.

    Regression guard: these fields silently disappeared twice during large renames.
    When an active coupling uses spectrum_rgb, the frontend needs color_mode ==
    'spectrum_rgb' to enable the R/G/B bar colouring in SpectrumBars.
    """
    manager = client._manager
    type(manager).active_color_mode = PropertyMock(return_value="spectrum_rgb")
    type(manager).active_bass_hz = PropertyMock(return_value=250)
    type(manager).active_mid_hz = PropertyMock(return_value=2000)
    type(manager).active_coupling_id = PropertyMock(return_value="coupling-1")

    status = _read_ws_status(client)

    assert status is not None, "No status message received from /ws/preview"
    assert status.get("color_mode") == "spectrum_rgb", (
        "color_mode missing or wrong in WebSocket status frame — "
        "SpectrumBars will show all bars in accent colour instead of R/G/B"
    )
    assert status.get("bass_hz") == 250, "bass_hz missing from WebSocket status frame"
    assert status.get("mid_hz") == 2000, "mid_hz missing from WebSocket status frame"


def test_ws_preview_status_frame_null_when_no_session(client: TestClient):
    """color_mode must be null when no coupling is active."""
    status = _read_ws_status(client)

    assert status is not None, "No status message received from /ws/preview"
    assert status.get("color_mode") is None
    assert status.get("bass_hz") is None
    assert status.get("mid_hz") is None
