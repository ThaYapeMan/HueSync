"""Tests for the JSON REST API (src/huesync/api.py).

Uses FastAPI's TestClient so no real network connections are made.
PlayerManager is mocked to avoid needing actual processes or bridges.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from fastapi.testclient import TestClient

from huesync.app import app
from huesync.models import (
    Analyser,
    Coupling,
    Effect,
    EnergyProfile,
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
    type(manager).last_energy = PropertyMock(return_value=0.0)
    type(manager).last_sustained_energy = PropertyMock(return_value=None)
    type(manager).last_loudness = PropertyMock(return_value=(None, None))
    # WebSocket status properties
    type(manager).active_coupling_id = PropertyMock(return_value=None)
    type(manager).active_coupling_name = PropertyMock(return_value=None)
    type(manager).active_zone_id = PropertyMock(return_value=None)
    type(manager).active_energy_profile_id = PropertyMock(return_value=None)
    type(manager).detected_sync_master_name = PropertyMock(return_value=None)
    type(manager).active_effect = PropertyMock(return_value=None)
    type(manager).follower_warning = PropertyMock(return_value=None)
    type(manager).track_position = PropertyMock(return_value=None)
    type(manager).active_bass_hz = PropertyMock(return_value=None)
    type(manager).active_mid_hz = PropertyMock(return_value=None)
    type(manager).active_onset_method = PropertyMock(return_value=None)
    type(manager).active_lower_cutoff_freq = PropertyMock(return_value=None)
    type(manager).active_higher_cutoff_freq = PropertyMock(return_value=None)
    type(manager).follow_target_mac = PropertyMock(return_value=None)
    type(manager).follow_target_name = PropertyMock(return_value=None)
    type(manager).active_player_type = PropertyMock(return_value=None)
    type(manager).airplay_receiving = PropertyMock(return_value=None)
    # active_bars_source drives cross-mode analyser_id routing in
    # _apply_coupling_action.  Default to "cava" so tests that do not
    # explicitly set it still see the legacy FIFO restart path.
    type(manager).active_bars_source = PropertyMock(return_value="cava")
    # Async methods
    manager.activate_coupling = AsyncMock()
    manager.deactivate = AsyncMock()
    manager.close = AsyncMock()
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


def test_controller_responses_never_expose_credentials(client: TestClient):
    """GET /api/controllers and GET /api/controllers/{id} must never include
    actual app_key / client_key values, regardless of whether they are set.
    Credentials are replaced by boolean presence flags (app_key_configured /
    client_key_configured).
    """
    # Controller with both credentials set
    payload = {
        "name": "Bridge with keys",
        "host": "10.0.0.1",
        "type": "hue",
        "app_key": "secret-app-key",
        "client_key": "secret-client-key",
    }
    resp = client.post("/api/controllers", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    controller_id = body["id"]

    # POST response must not contain credential values
    assert "app_key" not in body
    assert "client_key" not in body
    assert body["app_key_configured"] is True
    assert body["client_key_configured"] is True

    # GET /api/controllers/{id} must not contain credential values
    resp2 = client.get(f"/api/controllers/{controller_id}")
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert "app_key" not in body2
    assert "client_key" not in body2
    assert body2["app_key_configured"] is True
    assert body2["client_key_configured"] is True

    # GET /api/controllers (list) must not contain credential values
    resp3 = client.get("/api/controllers")
    assert resp3.status_code == 200
    items = resp3.json()
    for item in items:
        assert "app_key" not in item
        assert "client_key" not in item

    # Controller with no credentials: flags must be False
    payload2 = {"name": "Unconfigured", "host": "10.0.0.2", "type": "hue"}
    resp4 = client.post("/api/controllers", json=payload2)
    assert resp4.status_code == 201
    body4 = resp4.json()
    assert body4["app_key_configured"] is False
    assert body4["client_key_configured"] is False

    resp5 = client.get(f"/api/controllers/{body4['id']}")
    assert resp5.json()["app_key_configured"] is False
    assert resp5.json()["client_key_configured"] is False


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


def test_create_airplay_virtual_player(client: TestClient):
    """Creating an AirPlay player must succeed with type='AirPlay' and no lms_host required."""
    payload = {"type": "AirPlay"}
    resp = client.post("/api/virtual-players", json=payload)
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["type"] == "AirPlay"

    player_id = body["id"]
    resp2 = client.get(f"/api/virtual-players/{player_id}")
    assert resp2.status_code == 200
    assert resp2.json()["type"] == "AirPlay"


def test_airplay_virtual_player_in_list(client: TestClient):
    """An AirPlay player appears in the virtual-players list with correct type."""
    client.post("/api/virtual-players", json={"type": "AirPlay", "player_name": "HueSyncAirPlay"})
    client.post("/api/virtual-players", json={"lms_host": "10.0.0.1", "player_name": "HueSyncLMS"})

    resp = client.get("/api/virtual-players")
    assert resp.status_code == 200
    types = [p["type"] for p in resp.json()]
    assert "AirPlay" in types
    assert "LMS" in types


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


def test_virtual_player_display_name_defaults_to_empty(client: TestClient):
    resp = client.post("/api/virtual-players", json={"lms_host": "10.0.0.20"})
    assert resp.status_code == 201
    assert resp.json()["display_name"] == ""


def test_virtual_player_display_name_crud(client: TestClient):
    resp = client.post(
        "/api/virtual-players",
        json={"lms_host": "10.0.0.21", "display_name": "Living Room"},
    )
    assert resp.status_code == 201
    assert resp.json()["display_name"] == "Living Room"
    player_id = resp.json()["id"]

    patch_resp = client.patch(
        f"/api/virtual-players/{player_id}", json={"display_name": "Kitchen"}
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["display_name"] == "Kitchen"

    get_resp = client.get(f"/api/virtual-players/{player_id}")
    assert get_resp.json()["display_name"] == "Kitchen"


def test_display_name_patch_inactive_player_no_deactivate(client: TestClient):
    """PATCH display_name on a player with no active coupling must not call deactivate."""
    resp = client.post(
        "/api/virtual-players", json={"lms_host": "10.0.0.22", "display_name": "Old"}
    )
    player_id = resp.json()["id"]

    patch_resp = client.patch(f"/api/virtual-players/{player_id}", json={"display_name": "New"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["display_name"] == "New"
    client._manager.deactivate.assert_not_awaited()


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


def test_lms_follow_targets_exclude_all_managed_players(client: TestClient, monkeypatch):
    from huesync import api as api_module

    for name, mac in [
        ("HueSync", "AA:BB:CC:DD:EE:01"),
        ("HueSync LMS", "aa:bb:cc:dd:ee:02"),
    ]:
        client._storage.save_virtual_player(VirtualPlayer(
            player_name=name, player_mac=mac, lms_host="other-host",
        ))
    # An unset identity must not filter unrelated discoveries.
    client._storage.save_virtual_player(VirtualPlayer(player_name="Unconfigured"))
    discovered = [
        {"playerid": "aa:bb:cc:dd:ee:01", "name": "HueSync"},
        {"playerid": "AA:BB:CC:DD:EE:02", "name": "HueSync LMS"},
        {"playerid": "11:22:33:44:55:66", "name": "Sonos Living Room"},
        # Names are not identities: an external player may have the same name.
        {"playerid": "11:22:33:44:55:77", "name": "HueSync"},
    ]
    cli = MagicMock(return_value=discovered)
    monkeypatch.setattr(api_module, "list_lms_players", cli)
    before = [p.to_dict() for p in client._storage.list_virtual_players()]

    response = client.get("/api/lms/players?host=10.0.0.1")

    assert response.status_code == 200
    assert response.json() == discovered[2:]
    cli.assert_called_once_with("10.0.0.1")
    assert [p.to_dict() for p in client._storage.list_virtual_players()] == before


def test_lms_follow_targets_all_managed_returns_empty(client: TestClient, monkeypatch):
    from huesync import api as api_module

    client._storage.save_virtual_player(VirtualPlayer(player_mac="aa:bb:cc:dd:ee:01"))
    monkeypatch.setattr(api_module, "list_lms_players", lambda host: [
        {"playerid": "aa:bb:cc:dd:ee:01", "name": "HueSync"},
    ])
    response = client.get("/api/lms/players?host=10.0.0.1")
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


def test_create_and_get_zone(client: TestClient):
    controller = client.post('/api/controllers', json={'name': 'Bridge', 'host': '192.0.2.1'})
    assert controller.status_code == 201
    payload = {
        "name": "Living Room EA",
        "controller_id": controller.json()['id'],
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
# Effects
# ---------------------------------------------------------------------------


def test_create_and_get_effect(client: TestClient):
    payload = {"name": "Vivid", "effect_type": "spectrum_rgb", "sensitivity": 1.5}
    resp = client.post("/api/effects", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    effect_id = body["id"]
    assert body["sensitivity"] == 1.5

    resp2 = client.get(f"/api/effects/{effect_id}")
    assert resp2.status_code == 200
    assert resp2.json()["name"] == "Vivid"



def _make_full_coupling(storage: Storage) -> Coupling:
    """Create and persist all entities required for a Coupling, return the Coupling."""
    player = VirtualPlayer(lms_host="10.0.0.1", player_mac="aa:bb:cc:dd:ee:ff")
    zone = Zone(name="Z", controller_id="ctrl-1", entertainment_area_id="ea-1")
    ac = Analyser(name="AC")
    effect = Effect(name="SC")
    energy_profile = EnergyProfile(name="CF", high_energy_effect_id=effect.id)
    storage.save_virtual_player(player)
    storage.save_zone(zone)
    storage.save_analyser(ac)
    storage.save_effect(effect)
    storage.save_energy_profile(energy_profile)
    coupling = Coupling(
        name="Test Coupling",
        player_id=player.id,
        zone_id=zone.id,
        analyser_id=ac.id,
        energy_profile_id=energy_profile.id,
    )
    storage.save_coupling(coupling)
    return coupling


def test_create_coupling(client: TestClient):
    player = VirtualPlayer(lms_host="10.0.0.1")
    zone = Zone(name="Z", controller_id="c1", entertainment_area_id="ea-1")
    ac = Analyser(name="AC")
    effect = Effect(name="SC")
    energy_profile = EnergyProfile(name="CF", high_energy_effect_id=effect.id)
    client._storage.save_virtual_player(player)
    client._storage.save_zone(zone)
    client._storage.save_analyser(ac)
    client._storage.save_effect(effect)
    client._storage.save_energy_profile(energy_profile)

    payload = {
        "name": "My Coupling",
        "player_id": player.id,
        "analyser_id": ac.id,
        "zone_id": zone.id,
        "energy_profile_id": energy_profile.id,
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
        energy_profile_id="missing",
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


def test_patch_coupling_energy_profile_id_update_render_only(client: TestClient):
    """Swapping energy_profile_id must call update_render only — no cava restart,
    no deactivate."""
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)

    new_effect = Effect(name="SC2")
    client._storage.save_effect(new_effect)
    new_cf = EnergyProfile(name="CF2", high_energy_effect_id=new_effect.id)
    client._storage.save_energy_profile(new_cf)

    resp = client.patch(
        f"/api/couplings/{coupling.id}",
        json={"energy_profile_id": new_cf.id},
    )
    assert resp.status_code == 200
    client._manager.deactivate.assert_not_awaited()
    client._manager.restart_cava.assert_not_awaited()
    client._manager.update_render.assert_called_once()

    saved = client._storage.get_coupling(coupling.id)
    assert saved is not None and saved.energy_profile_id == new_cf.id


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


def test_analyser_id_canonical_to_fifo_deactivates_and_reactivates(
    client: TestClient,
):
    """Changing analyser_id from a pcm_pipeline Analyser to a cava Analyser
    crosses a mode boundary that cannot be hot-swapped.  Router must call
    deactivate() then activate_coupling() (item 19).
    """
    # Old analyser: pcm_pipeline; new analyser: cava.
    coupling = _make_full_coupling(client._storage)
    old_ac = client._storage.get_analyser(coupling.analyser_id)
    old_ac.bars_source = "pcm_pipeline"
    old_ac.spectrum_backend = "v2"
    client._storage.save_analyser(old_ac)

    client._storage.set_active_coupling_id(coupling.id)
    # Simulate an active pcm_pipeline session.
    type(client._manager).active_bars_source = PropertyMock(
        return_value="pcm_pipeline"
    )

    new_ac = Analyser(name="Cava AC", bars_source="cava", spectrum_backend="v2")
    client._storage.save_analyser(new_ac)

    resp = client.patch(
        f"/api/couplings/{coupling.id}",
        json={"analyser_id": new_ac.id},
    )
    assert resp.status_code == 200
    # Cross-mode swap: full deactivate + reactivate.
    client._manager.deactivate.assert_awaited_once()
    client._manager.activate_coupling.assert_awaited_once()
    # Hot-swap paths must NOT have been used.
    client._manager.restart_cava.assert_not_awaited()


def test_analyser_id_fifo_to_canonical_deactivates_and_reactivates(
    client: TestClient,
):
    """Changing analyser_id from a cava Analyser to a pcm_pipeline Analyser
    also crosses the mode boundary and requires a full reactivate (item 19).
    """
    coupling = _make_full_coupling(client._storage)
    # Old analyser is the default cava one from _make_full_coupling.
    client._storage.set_active_coupling_id(coupling.id)
    type(client._manager).active_bars_source = PropertyMock(return_value="cava")

    new_ac = Analyser(
        name="PCM AC", bars_source="pcm_pipeline", spectrum_backend="v2",
    )
    client._storage.save_analyser(new_ac)

    resp = client.patch(
        f"/api/couplings/{coupling.id}",
        json={"analyser_id": new_ac.id},
    )
    assert resp.status_code == 200
    client._manager.deactivate.assert_awaited_once()
    client._manager.activate_coupling.assert_awaited_once()
    client._manager.restart_cava.assert_not_awaited()


def test_analyser_id_same_mode_canonical_uses_replace_pcm_analyser(
    client: TestClient,
):
    """A within-mode analyser_id swap (pcm_pipeline → pcm_pipeline) must
    route to replace_pcm_analyser (live swap), NOT to deactivate or to
    restart_cava (item 19)."""
    coupling = _make_full_coupling(client._storage)
    old_ac = client._storage.get_analyser(coupling.analyser_id)
    old_ac.bars_source = "pcm_pipeline"
    old_ac.spectrum_backend = "v2"
    client._storage.save_analyser(old_ac)

    client._storage.set_active_coupling_id(coupling.id)
    type(client._manager).active_bars_source = PropertyMock(
        return_value="pcm_pipeline"
    )

    new_ac = Analyser(
        name="PCM AC 2", bars_source="pcm_pipeline", spectrum_backend="v2",
    )
    client._storage.save_analyser(new_ac)

    resp = client.patch(
        f"/api/couplings/{coupling.id}",
        json={"analyser_id": new_ac.id},
    )
    assert resp.status_code == 200
    client._manager.deactivate.assert_not_awaited()
    client._manager.activate_coupling.assert_not_awaited()
    client._manager.restart_cava.assert_not_awaited()
    client._manager.replace_pcm_analyser.assert_called_once()


def test_restart_cava_endpoint_on_canonical_session_routes_to_pcm_path(
    client: TestClient,
):
    """POST /api/couplings/{id}/restart-cava on a pcm_pipeline session must
    route to replace_pcm_analyser rather than the FIFO/cava restart path
    (item 18).
    """
    coupling = _make_full_coupling(client._storage)
    ac = client._storage.get_analyser(coupling.analyser_id)
    ac.bars_source = "pcm_pipeline"
    ac.spectrum_backend = "v2"
    client._storage.save_analyser(ac)

    client._storage.set_active_coupling_id(coupling.id)
    type(client._manager).active_bars_source = PropertyMock(
        return_value="pcm_pipeline"
    )

    resp = client.post(f"/api/couplings/{coupling.id}/restart-cava", json={})
    assert resp.status_code == 200
    client._manager.restart_cava.assert_not_awaited()
    client._manager.replace_pcm_analyser.assert_called_once()


def test_restart_cava_endpoint_on_cava_session_routes_to_cava_restart(
    client: TestClient,
):
    """POST /api/couplings/{id}/restart-cava on a cava/FIFO session keeps
    the legacy behaviour: manager.restart_cava is called (item 18)."""
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)
    type(client._manager).active_bars_source = PropertyMock(return_value="cava")

    resp = client.post(f"/api/couplings/{coupling.id}/restart-cava", json={})
    assert resp.status_code == 200
    client._manager.restart_cava.assert_awaited_once()
    client._manager.replace_pcm_analyser.assert_not_called()


def test_restart_cava_rollback_on_pcm_runtime_failure(client: TestClient):
    """BLOCKER 4: when replace_pcm_analyser raises, staged storage writes
    must roll back so stored cutoff still matches the pre-request value.
    """
    coupling = _make_full_coupling(client._storage)
    ac = client._storage.get_analyser(coupling.analyser_id)
    ac.bars_source = "pcm_pipeline"
    ac.spectrum_backend = "v2"
    ac.lower_cutoff_freq = 50
    ac.higher_cutoff_freq = 10000
    client._storage.save_analyser(ac)
    original_lower = ac.lower_cutoff_freq
    original_higher = ac.higher_cutoff_freq

    client._storage.set_active_coupling_id(coupling.id)
    type(client._manager).active_bars_source = PropertyMock(
        return_value="pcm_pipeline"
    )
    client._manager.replace_pcm_analyser = MagicMock(
        side_effect=RuntimeError("engine unavailable")
    )

    resp = client.post(
        f"/api/couplings/{coupling.id}/restart-cava",
        json={"lower_cutoff_freq": 100, "higher_cutoff_freq": 12000},
    )
    # Runtime failed → 409 (transactional rejection).
    assert resp.status_code == 409, resp.text

    # Storage was rolled back to the original values.
    ac_after = client._storage.get_analyser(coupling.analyser_id)
    assert ac_after.lower_cutoff_freq == original_lower, (
        f"expected rollback to {original_lower}, got {ac_after.lower_cutoff_freq}"
    )
    assert ac_after.higher_cutoff_freq == original_higher, (
        f"expected rollback to {original_higher}, got {ac_after.higher_cutoff_freq}"
    )


def test_restart_cava_rollback_on_fifo_runtime_failure(client: TestClient):
    """BLOCKER 4: same rollback contract on the legacy FIFO / cava path."""
    coupling = _make_full_coupling(client._storage)
    ac = client._storage.get_analyser(coupling.analyser_id)
    ac.bars_source = "cava"
    ac.lower_cutoff_freq = 50
    ac.higher_cutoff_freq = 10000
    client._storage.save_analyser(ac)
    original_lower = ac.lower_cutoff_freq
    original_higher = ac.higher_cutoff_freq

    client._storage.set_active_coupling_id(coupling.id)
    type(client._manager).active_bars_source = PropertyMock(return_value="cava")
    client._manager.restart_cava = AsyncMock(
        side_effect=RuntimeError("cava spawn failed")
    )

    resp = client.post(
        f"/api/couplings/{coupling.id}/restart-cava",
        json={"lower_cutoff_freq": 200, "higher_cutoff_freq": 15000},
    )
    assert resp.status_code == 409, resp.text

    ac_after = client._storage.get_analyser(coupling.analyser_id)
    assert ac_after.lower_cutoff_freq == original_lower
    assert ac_after.higher_cutoff_freq == original_higher


def test_restart_cava_effect_rollback_on_pcm_runtime_failure(client: TestClient):
    """BLOCKER 4: effect (bass_hz/mid_hz) changes must also roll back."""
    coupling = _make_full_coupling(client._storage)
    ac = client._storage.get_analyser(coupling.analyser_id)
    ac.bars_source = "pcm_pipeline"
    ac.spectrum_backend = "v2"
    client._storage.save_analyser(ac)

    energy_profile = client._storage.get_energy_profile(coupling.energy_profile_id)
    effect = client._storage.get_effect(energy_profile.high_energy_effect_id)
    effect.bass_hz = 250
    effect.mid_hz = 2000
    client._storage.save_effect(effect)
    original_bass = effect.bass_hz
    original_mid = effect.mid_hz

    client._storage.set_active_coupling_id(coupling.id)
    type(client._manager).active_bars_source = PropertyMock(
        return_value="pcm_pipeline"
    )
    client._manager.replace_pcm_analyser = MagicMock(
        side_effect=RuntimeError("engine restart failed")
    )

    resp = client.post(
        f"/api/couplings/{coupling.id}/restart-cava",
        json={"bass_hz": 300, "mid_hz": 3000},
    )
    assert resp.status_code == 409

    effect_after = client._storage.get_effect(energy_profile.high_energy_effect_id)
    assert effect_after.bass_hz == original_bass, (
        f"expected rollback to bass_hz={original_bass}, got {effect_after.bass_hz}"
    )
    assert effect_after.mid_hz == original_mid


@pytest.mark.parametrize("old_mode,new_mode", [
    ("cava", "pcm_pipeline"), ("pcm_pipeline", "cava"),
])
def test_active_analyser_bars_source_change_reactivates(client: TestClient, old_mode, new_mode):
    """In-place mode edits must perform the same full restart as analyser swaps."""
    coupling = _make_full_coupling(client._storage)
    ac_id = client._storage.get_coupling(coupling.id).analyser_id
    analyser = client._storage.get_analyser(ac_id)
    analyser.bars_source = old_mode
    client._storage.save_analyser(analyser)
    client._storage.set_active_coupling_id(coupling.id)
    state = {"mode": old_mode, "active": coupling.id}
    type(client._manager).active_bars_source = PropertyMock(side_effect=lambda: state["mode"])

    async def deactivate():
        state.update(mode=None, active=None)

    async def activate(selected):
        assert state["active"] is None
        state.update(mode=client._storage.get_analyser(selected.analyser_id).bars_source,
                     active=selected.id)

    client._manager.deactivate.side_effect = deactivate
    client._manager.activate_coupling.side_effect = activate

    resp = client.patch(f"/api/analysers/{ac_id}", json={"bars_source": new_mode})
    assert resp.status_code == 200

    # Must have been deactivated.
    client._manager.deactivate.assert_awaited_once()
    client._manager.activate_coupling.assert_awaited_once_with(coupling)
    assert state == {"mode": new_mode, "active": coupling.id}

    # Live-update paths must NOT have been called.
    client._manager.restart_cava.assert_not_awaited()
    client._manager.update_onset_pipeline.assert_not_called()


def test_bars_source_roundtrip(client: TestClient):
    """bars_source is stored and returned by GET /api/analysers/{id}."""
    resp = client.post("/api/analysers", json={"name": "PCM Test", "bars_source": "pcm_pipeline"})
    assert resp.status_code == 201
    ac_id = resp.json()["id"]

    resp = client.get(f"/api/analysers/{ac_id}")
    assert resp.status_code == 200
    assert resp.json()["bars_source"] == "pcm_pipeline"

    # Default is "cava"
    resp2 = client.post("/api/analysers", json={"name": "Cava Test"})
    assert resp2.status_code == 201
    assert resp2.json()["bars_source"] == "cava"


# ---------------------------------------------------------------------------
# Clone coupling
# ---------------------------------------------------------------------------


def test_clone_coupling_returns_new_id(client: TestClient):
    """The cloned Coupling must have a different ID and a copy-suffixed name."""
    coupling = _make_full_coupling(client._storage)
    resp = client.post(f"/api/couplings/{coupling.id}/clone")
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != coupling.id
    assert body["name"] == f"{coupling.name} (copy)"


def test_clone_coupling_preserves_entity_references(client: TestClient):
    """Cloning a Coupling must preserve all FK references.

    The cloned Coupling points to the SAME Analyser, EnergyProfile, Player,
    and Zone as the original — no new sub-entities are created.
    """
    coupling = _make_full_coupling(client._storage)
    resp = client.post(f"/api/couplings/{coupling.id}/clone")
    assert resp.status_code == 201
    body = resp.json()

    assert body["analyser_id"] == coupling.analyser_id
    assert body["energy_profile_id"] == coupling.energy_profile_id
    assert body["player_id"] == coupling.player_id
    assert body["zone_id"] == coupling.zone_id


def test_clone_coupling_entity_counts_unchanged(client: TestClient):
    """Cloning a Coupling must not create any additional sub-entities."""
    coupling = _make_full_coupling(client._storage)
    n_players  = len(client._storage.list_virtual_players())
    n_zones    = len(client._storage.list_zones())
    n_analysers = len(client._storage.list_analysers())
    n_profiles  = len(client._storage.list_energy_profiles())
    n_effects   = len(client._storage.list_effects())

    resp = client.post(f"/api/couplings/{coupling.id}/clone")
    assert resp.status_code == 201

    assert len(client._storage.list_virtual_players())  == n_players
    assert len(client._storage.list_zones())             == n_zones
    assert len(client._storage.list_analysers())         == n_analysers
    assert len(client._storage.list_energy_profiles())   == n_profiles
    assert len(client._storage.list_effects())           == n_effects
    assert len(client._storage.list_couplings())         == 2  # original + clone


def test_clone_coupling_reassigning_analyser_does_not_affect_original(client: TestClient):
    """Redirecting the clone's analyser_id FK leaves the original coupling and
    the original Analyser entity both unchanged."""
    coupling = _make_full_coupling(client._storage)
    resp = client.post(f"/api/couplings/{coupling.id}/clone")
    assert resp.status_code == 201
    clone_id = resp.json()["id"]

    # Create a second Analyser and reassign the clone to it.
    r2 = client.post("/api/analysers", json={"name": "AC2", "onset_method": "multiband"})
    ac2_id = r2.json()["id"]

    patch_resp = client.patch(f"/api/couplings/{clone_id}", json={"analyser_id": ac2_id})
    assert patch_resp.status_code == 200

    # Clone now points to AC2.
    assert patch_resp.json()["analyser_id"] == ac2_id

    # Original coupling still points to the original Analyser.
    orig_resp = client.get(f"/api/couplings/{coupling.id}")
    assert orig_resp.json()["analyser_id"] == coupling.analyser_id

    # Original Analyser entity is unmodified.
    orig_ac = client._storage.get_analyser(coupling.analyser_id)
    assert orig_ac is not None
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


def test_ws_preview_status_frame_has_effect_type_and_band_hz(client: TestClient):
    """WebSocket /ws/preview status frame must include effect_type, bass_hz, mid_hz.

    Regression guard: these fields silently disappeared twice during large renames.
    When an active coupling uses spectrum_rgb, the frontend needs effect_type ==
    'spectrum_rgb' to enable the R/G/B bar colouring in SpectrumBars.
    """
    manager = client._manager
    type(manager).active_effect = PropertyMock(return_value="spectrum_rgb")
    type(manager).active_bass_hz = PropertyMock(return_value=250)
    type(manager).active_mid_hz = PropertyMock(return_value=2000)
    type(manager).active_coupling_id = PropertyMock(return_value="coupling-1")

    status = _read_ws_status(client)

    assert status is not None, "No status message received from /ws/preview"
    assert status.get("effect_type") == "spectrum_rgb", (
        "effect_type missing or wrong in WebSocket status frame — "
        "SpectrumBars will show all bars in accent colour instead of R/G/B"
    )
    assert status.get("bass_hz") == 250, "bass_hz missing from WebSocket status frame"
    assert status.get("mid_hz") == 2000, "mid_hz missing from WebSocket status frame"


def test_ws_preview_status_frame_null_when_no_session(client: TestClient):
    """effect_type must be null when no coupling is active."""
    status = _read_ws_status(client)

    assert status is not None, "No status message received from /ws/preview"
    assert status.get("effect_type") is None
    assert status.get("bass_hz") is None
    assert status.get("mid_hz") is None


# ---------------------------------------------------------------------------
# Name uniqueness validation
# ---------------------------------------------------------------------------


def test_name_duplicate_create_rejected(client: TestClient):
    """Creating two analysers with the same name (case-insensitive) must return 409."""
    client.post("/api/analysers", json={"name": "Alpha"})
    resp = client.post("/api/analysers", json={"name": "alpha"})
    assert resp.status_code == 409


def test_name_duplicate_patch_rejected(client: TestClient):
    """Patching an analyser's name to a name already used by another must return 409."""
    client.post("/api/analysers", json={"name": "Alpha"})
    r2 = client.post("/api/analysers", json={"name": "Beta"})
    ac_id = r2.json()["id"]
    resp = client.patch(f"/api/analysers/{ac_id}", json={"name": "alpha"})
    assert resp.status_code == 409


def test_name_patch_self_ok(client: TestClient):
    """Patching an entity's name to its own current name must not return 409."""
    r = client.post("/api/analysers", json={"name": "Alpha"})
    ac_id = r.json()["id"]
    resp = client.patch(f"/api/analysers/{ac_id}", json={"name": "Alpha"})
    assert resp.status_code == 200


def test_clone_generates_unique_name(client: TestClient):
    """Cloning a coupling whose default copy name is already taken generates a unique name."""
    coupling = _make_full_coupling(client._storage)
    # Pre-occupy the first candidate name so clone must pick a different one.
    client._storage.save_coupling(replace(
        coupling,
        id=str(uuid.uuid4()),
        name=f"{coupling.name} (copy)",
    ))
    resp = client.post(f"/api/couplings/{coupling.id}/clone")
    assert resp.status_code == 201
    assert resp.json()["name"] == f"{coupling.name} (copy 2)"


# ---------------------------------------------------------------------------
# Clone Effect
# ---------------------------------------------------------------------------


def test_clone_effect_returns_new_id_and_copy_name(client: TestClient):
    """POST /effects/{id}/clone → 201, new id, copy-suffixed name."""
    r = client.post("/api/effects", json={"name": "Pulse", "effect_type": "mono_pulse"})
    effect_id = r.json()["id"]

    resp = client.post(f"/api/effects/{effect_id}/clone")
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != effect_id
    assert body["name"] == "Pulse (copy)"


def test_clone_effect_copies_configuration(client: TestClient):
    """Cloned Effect has identical configuration to the original."""
    r = client.post("/api/effects", json={
        "name": "Vivid",
        "effect_type": "spectrum_rgb",
        "sensitivity": 2.5,
        "brightness_floor": 0.05,
        "bass_hz": 200,
        "mid_hz": 1800,
        "exertion_clip": 4.0,
        "onset_flash_intensity": 0.3,
    })
    orig = r.json()

    resp = client.post(f"/api/effects/{orig['id']}/clone")
    clone = resp.json()

    assert clone["effect_type"] == orig["effect_type"]
    assert clone["sensitivity"] == orig["sensitivity"]
    assert clone["brightness_floor"] == orig["brightness_floor"]
    assert clone["bass_hz"] == orig["bass_hz"]
    assert clone["mid_hz"] == orig["mid_hz"]
    assert clone["exertion_clip"] == orig["exertion_clip"]
    assert clone["onset_flash_intensity"] == orig["onset_flash_intensity"]


def test_clone_effect_entity_count(client: TestClient):
    """Cloning an Effect creates exactly one additional Effect and nothing else."""
    r = client.post("/api/effects", json={"name": "E1"})
    effect_id = r.json()["id"]

    n_before = len(client.get("/api/effects").json())
    resp = client.post(f"/api/effects/{effect_id}/clone")
    assert resp.status_code == 201
    assert len(client.get("/api/effects").json()) == n_before + 1


def test_clone_effect_not_found(client: TestClient):
    resp = client.post("/api/effects/no-such-id/clone")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Clone EnergyProfile
# ---------------------------------------------------------------------------


def test_clone_energy_profile_returns_new_id_and_copy_name(client: TestClient):
    """POST /energy-profiles/{id}/clone → 201, new id, copy-suffixed name."""
    eff = client.post("/api/effects", json={"name": "EL"}).json()
    r = client.post("/api/energy-profiles", json={
        "name": "Classic", "high_energy_effect_id": eff["id"],
    })
    ep_id = r.json()["id"]

    resp = client.post(f"/api/energy-profiles/{ep_id}/clone")
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != ep_id
    assert body["name"] == "Classic (copy)"


def test_clone_energy_profile_preserves_effect_references(client: TestClient):
    """Cloned EnergyProfile must preserve high and low effect references; no Effect clone."""
    hi = client.post("/api/effects", json={"name": "Hi"}).json()
    lo = client.post("/api/effects", json={"name": "Lo"}).json()
    r = client.post("/api/energy-profiles", json={
        "name": "EP",
        "high_energy_effect_id": hi["id"],
        "low_energy_effect_id": lo["id"],
        "blend_start": 0.2,
        "blend_end": 0.8,
        "blend_response": 0.05,
    })
    ep = r.json()

    n_effects_before = len(client.get("/api/effects").json())

    resp = client.post(f"/api/energy-profiles/{ep['id']}/clone")
    assert resp.status_code == 201
    clone = resp.json()

    assert clone["high_energy_effect_id"] == hi["id"]
    assert clone["low_energy_effect_id"] == lo["id"]
    assert clone["blend_start"] == ep["blend_start"]
    assert clone["blend_end"] == ep["blend_end"]
    assert clone["blend_response"] == ep["blend_response"]

    # No new Effects created.
    assert len(client.get("/api/effects").json()) == n_effects_before


def test_clone_energy_profile_entity_count(client: TestClient):
    """Cloning an EnergyProfile creates exactly one additional EnergyProfile."""
    eff = client.post("/api/effects", json={"name": "X"}).json()
    r = client.post("/api/energy-profiles", json={
        "name": "EP2", "high_energy_effect_id": eff["id"],
    })
    ep_id = r.json()["id"]

    n_before = len(client.get("/api/energy-profiles").json())
    resp = client.post(f"/api/energy-profiles/{ep_id}/clone")
    assert resp.status_code == 201
    assert len(client.get("/api/energy-profiles").json()) == n_before + 1


def test_clone_energy_profile_not_found(client: TestClient):
    resp = client.post("/api/energy-profiles/no-such-id/clone")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Clone Analyser
# ---------------------------------------------------------------------------


def test_clone_analyser_returns_new_id_and_copy_name(client: TestClient):
    """POST /analysers/{id}/clone → 201, new id, copy-suffixed name."""
    r = client.post("/api/analysers", json={"name": "Cava Combined"})
    ac_id = r.json()["id"]

    resp = client.post(f"/api/analysers/{ac_id}/clone")
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != ac_id
    assert body["name"] == "Cava Combined (copy)"


def test_clone_analyser_copies_configuration(client: TestClient):
    """Cloned Analyser has identical configuration to the original."""
    r = client.post("/api/analysers", json={
        "name": "Custom",
        "onset_method": "multiband",
        "bars": 20,
        "lower_cutoff_freq": 80,
        "higher_cutoff_freq": 10000,
        "onset_delta": 0.15,
        "onset_alpha": 0.85,
    })
    orig = r.json()

    resp = client.post(f"/api/analysers/{orig['id']}/clone")
    clone = resp.json()

    assert clone["onset_method"] == orig["onset_method"]
    assert clone["bars"] == orig["bars"]
    assert clone["lower_cutoff_freq"] == orig["lower_cutoff_freq"]
    assert clone["higher_cutoff_freq"] == orig["higher_cutoff_freq"]
    assert clone["onset_delta"] == orig["onset_delta"]
    assert clone["onset_alpha"] == orig["onset_alpha"]


def test_clone_analyser_entity_count(client: TestClient):
    """Cloning an Analyser creates exactly one additional Analyser and nothing else."""
    r = client.post("/api/analysers", json={"name": "A1"})
    ac_id = r.json()["id"]

    n_before = len(client.get("/api/analysers").json())
    resp = client.post(f"/api/analysers/{ac_id}/clone")
    assert resp.status_code == 201
    assert len(client.get("/api/analysers").json()) == n_before + 1


def test_clone_analyser_not_found(client: TestClient):
    resp = client.post("/api/analysers/no-such-id/clone")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 6 — Integrity checks
# ---------------------------------------------------------------------------


def test_clone_coupling_then_edit_profile_ref_does_not_affect_original(client: TestClient):
    """Clone Coupling, then reassign clone's energy_profile_id.

    Original coupling must still point to its original EnergyProfile.
    The original EnergyProfile must not have been cloned or modified.
    """
    coupling = _make_full_coupling(client._storage)
    clone_id = client.post(f"/api/couplings/{coupling.id}/clone").json()["id"]

    # Create a second EnergyProfile and redirect the clone to it.
    eff = client.post("/api/effects", json={"name": "X"}).json()
    ep2_id = client.post("/api/energy-profiles", json={
        "name": "EP2", "high_energy_effect_id": eff["id"],
    }).json()["id"]

    patch_resp = client.patch(f"/api/couplings/{clone_id}", json={"energy_profile_id": ep2_id})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["energy_profile_id"] == ep2_id

    # Original coupling still references the original EnergyProfile.
    orig_resp = client.get(f"/api/couplings/{coupling.id}")
    assert orig_resp.json()["energy_profile_id"] == coupling.energy_profile_id

    # Original EnergyProfile is not modified.
    orig_ep = client._storage.get_energy_profile(coupling.energy_profile_id)
    assert orig_ep is not None


def test_clone_energy_profile_then_edit_effect_ref_does_not_affect_original(client: TestClient):
    """Clone EnergyProfile, then change clone's high_energy_effect_id.

    Original EnergyProfile must still reference its original Effect.
    The referenced Effect must not have been cloned.
    """
    hi = client.post("/api/effects", json={"name": "Hi"}).json()
    ep_id = client.post("/api/energy-profiles", json={
        "name": "EP", "high_energy_effect_id": hi["id"],
    }).json()["id"]

    clone_id = client.post(f"/api/energy-profiles/{ep_id}/clone").json()["id"]
    n_effects = len(client.get("/api/effects").json())

    # Create a third Effect and redirect the clone.
    hi2 = client.post("/api/effects", json={"name": "Hi2"}).json()
    patch_resp = client.patch(f"/api/energy-profiles/{clone_id}", json={
        "high_energy_effect_id": hi2["id"],
    })
    assert patch_resp.status_code == 200

    # Original EP still points to Hi.
    orig_ep = client._storage.get_energy_profile(ep_id)
    assert orig_ep.high_energy_effect_id == hi["id"]

    # Effect count grew by exactly 1 (hi2), not more.
    assert len(client.get("/api/effects").json()) == n_effects + 1


def test_clone_effect_then_edit_does_not_affect_original(client: TestClient):
    """Clone Effect, edit clone's parameters.  Original Effect parameters must be unchanged."""
    r = client.post("/api/effects", json={"name": "Base", "sensitivity": 1.0})
    orig_id = r.json()["id"]

    clone_id = client.post(f"/api/effects/{orig_id}/clone").json()["id"]

    patch_resp = client.patch(f"/api/effects/{clone_id}", json={"sensitivity": 3.0})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["sensitivity"] == pytest.approx(3.0)

    # Original Effect sensitivity is unchanged.
    orig = client._storage.get_effect(orig_id)
    assert orig.sensitivity == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Analyser spectrum_backend API write path (M4)
# ---------------------------------------------------------------------------


def test_create_analyser_with_cavacore_backend_persisted(client: TestClient):
    """POST /api/analysers with spectrum_backend='cavacore' persists the value."""
    resp = client.post("/api/analysers", json={
        "name": "CavaCore AC",
        "bars_source": "pcm_pipeline",
        "spectrum_backend": "cavacore",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["spectrum_backend"] == "cavacore"
    assert body["bars_source"] == "pcm_pipeline"

    # Verify it's stored and retrievable.
    ac_id = body["id"]
    get_resp = client.get(f"/api/analysers/{ac_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["spectrum_backend"] == "cavacore"


def test_create_analyser_default_backend_is_v2(client: TestClient):
    """POST /api/analysers without spectrum_backend defaults to 'v2'."""
    resp = client.post("/api/analysers", json={"name": "Default AC"})
    assert resp.status_code == 201
    assert resp.json()["spectrum_backend"] == "v2"


def test_patch_analyser_spectrum_backend_accepted(client: TestClient):
    """PATCH /api/analysers/{id} can change spectrum_backend to cavacore."""
    create_resp = client.post("/api/analysers", json={
        "name": "Patch AC",
        "bars_source": "pcm_pipeline",
        "spectrum_backend": "v2",
    })
    ac_id = create_resp.json()["id"]

    patch_resp = client.patch(f"/api/analysers/{ac_id}", json={"spectrum_backend": "cavacore"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["spectrum_backend"] == "cavacore"


def test_create_analyser_invalid_backend_rejected(client: TestClient):
    """POST /api/analysers with unknown spectrum_backend → 422."""
    resp = client.post("/api/analysers", json={
        "name": "Bad AC",
        "bars_source": "pcm_pipeline",
        "spectrum_backend": "libfoo",
    })
    assert resp.status_code == 422


def test_create_analyser_cava_fifo_with_cavacore_rejected(client: TestClient):
    """POST /api/analysers with bars_source='cava' + spectrum_backend='cavacore' → error."""
    resp = client.post("/api/analysers", json={
        "name": "Bad Combo AC",
        "bars_source": "cava",
        "spectrum_backend": "cavacore",
    })
    assert resp.status_code in (400, 422)


def test_patch_analyser_cava_fifo_with_cavacore_rejected(client: TestClient):
    """PATCH with bars_source='cava' + spectrum_backend='cavacore' → 422."""
    create_resp = client.post("/api/analysers", json={
        "name": "Combo Patch AC",
        "bars_source": "pcm_pipeline",
        "spectrum_backend": "cavacore",
    })
    ac_id = create_resp.json()["id"]

    patch_resp = client.patch(f"/api/analysers/{ac_id}", json={"bars_source": "cava"})
    assert patch_resp.status_code == 422


def test_virtual_player_follow_mode_roundtrip_and_default(client):
    response = client.post("/api/virtual-players", json={"player_name": "Default"})
    assert response.status_code == 201
    assert response.json()["follow_mode"] == "manual"
    player_id = response.json()["id"]
    response = client.patch(f"/api/virtual-players/{player_id}", json={
        "follow_mode": "sync_group", "follow_player_mac": "aa:bb:cc:dd:ee:ff",
    })
    assert response.status_code == 200
    assert response.json()["follow_mode"] == "sync_group"
    assert client._storage.get_virtual_player(player_id).follow_mode == "sync_group"
    response = client.patch(f"/api/virtual-players/{player_id}", json={"follow_mode": "manual"})
    assert response.json()["follow_player_mac"] == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize("value", ["auto", "", None, 1])
def test_invalid_follow_mode_rejected_without_mutation(client, value):
    created = client.post("/api/virtual-players", json={"player_name": "Manual"}).json()
    before = client._storage.get_virtual_player(created["id"]).to_dict()
    response = client.patch(f"/api/virtual-players/{created['id']}", json={"follow_mode": value})
    assert response.status_code == 422
    assert client._storage.get_virtual_player(created["id"]).to_dict() == before


def test_follow_mode_change_deactivates_owning_session(client):
    coupling = _make_full_coupling(client._storage)
    client._storage.set_active_coupling_id(coupling.id)
    response = client.patch(
        f"/api/virtual-players/{coupling.player_id}", json={"follow_mode": "sync_group"},
    )
    assert response.status_code == 200
    client._manager.deactivate.assert_awaited_once()


def test_persisted_follow_mode_validation_and_missing_default():
    from huesync.schema import empty_config, validate_current

    data = empty_config()
    data["virtual_players"] = [{"id": "old"}]
    validate_current(data)
    assert VirtualPlayer.from_dict(data["virtual_players"][0]).follow_mode == "manual"
    data["virtual_players"][0]["follow_mode"] = "invalid"
    with pytest.raises(ValueError, match="follow_mode"):
        validate_current(data)


def test_ws_track_is_generic_and_rebased_for_new_client(client: TestClient):
    import time

    from huesync.track_position import TrackPosition

    snapshot = TrackPosition('Title', 'Artist', 83, 225, True, time.monotonic() - 2)
    type(client._manager).track_position = PropertyMock(return_value=snapshot)
    track = _read_ws_status(client)['track']
    assert track['title'] == 'Title' and track['artist'] == 'Artist'
    assert 85 <= track['position_s'] < 90
    assert track['duration_s'] == 225 and track['playing']
    assert 'observed_at' not in track  # no dependency on the server's clock domain
    assert set(track) == {'title', 'artist', 'position_s', 'duration_s', 'playing'}


@pytest.mark.parametrize("values, expected", [
    ((-18.4, -20.1), (-18.4, -20.1)),
    ((float("-inf"), float("-inf")), (None, None)),
    ((None, None), (None, None)),
])
def test_ws_loudness_is_valid_json(client, values, expected):
    import json

    type(client._manager).last_loudness = PropertyMock(return_value=values)
    with client.websocket_connect("/ws/preview") as ws:
        payload = ws.receive_text()
        # Reject the non-standard Infinity/NaN tokens accepted by default json.loads.
        def reject_constant(value):
            raise AssertionError(value)
        frame = json.loads(payload, parse_constant=reject_constant)
    assert frame["type"] == "frame"
    assert frame["loudness_momentary_lufs"] == expected[0]
    assert frame["loudness_short_term_lufs"] == expected[1]


@pytest.mark.parametrize("bars_source", [None, "cava", "pcm_pipeline"])
def test_ws_preview_reports_active_bars_source(client, bars_source):
    type(client._manager).active_bars_source = PropertyMock(return_value=bars_source)
    assert _read_ws_status(client)["active_bars_source"] == bars_source


@pytest.fixture()
def transport_session(client, monkeypatch):
    """Real manager/follower routing; stub only the final CLI socket exchange."""
    from huesync.lms_follower import LmsFollower
    from huesync.models import Profile
    from huesync.player_manager import ActiveSession, PlayerManager

    player = VirtualPlayer(player_mac="02:00:00:00:00:01",
                           follow_player_mac="33:33:33:33:33:33")
    client._storage.save_virtual_player(player)
    coupling = Coupling(player_id=player.id)
    client._storage.save_coupling(coupling)
    manager = PlayerManager(client._storage)
    session = ActiveSession(Profile(player_mac=player.player_mac), coupling)
    session.follower = LmsFollower("lms.local", "aa:bb:cc:dd:ee:ff", player.player_mac,
                                   cli_port=19090)
    manager._active = session
    app.state.player_manager = manager
    exchange = MagicMock(return_value="OK")
    monkeypatch.setattr("huesync.lms_follower._cli_exchange", exchange)
    return manager, session, exchange, coupling


@pytest.mark.parametrize("mode", ["manual", "sync_group"])
@pytest.mark.parametrize("action,command", [
    ("play", "play"), ("pause", "pause 1"), ("toggle", "pause"),
    ("stop", "stop"), ("next", "playlist index +1"), ("previous", "playlist index -1"),
])
def test_transport_sends_exact_command_only_to_live_follow_target(
    client, transport_session, action, command, mode,
):
    from huesync.lms_follower import LmsSyncGroupObserver

    manager, session, exchange, coupling = transport_session
    if mode == "sync_group":
        session.follower = LmsSyncGroupObserver(
            "lms.local", session.profile.player_mac, lambda: [], AsyncMock(), cli_port=19090)
        session.follower._follow_mac = "aa:bb:cc:dd:ee:ff"
    response = client.post(f"/api/couplings/{coupling.id}/transport", json={"action": action})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "target_mac": "aa:bb:cc:dd:ee:ff"}
    exchange.assert_called_once_with("lms.local", 19090, f"aa:bb:cc:dd:ee:ff {command}\n")
    # The next request uses the newly selected target, not configuration or a cached MAC.
    session.follower._follow_mac = "aa:bb:cc:dd:ee:00"
    exchange.reset_mock()
    assert client.post(f"/api/couplings/{coupling.id}/transport",
                       json={"action": action}).status_code == 200
    exchange.assert_called_once_with("lms.local", 19090, f"aa:bb:cc:dd:ee:00 {command}\n")


@pytest.mark.parametrize("invalid", ["inactive", "airplay", "missing_target", "self", "managed",
                                     "stopping", "invalid_mac", "no_follower", "other_coupling"])
def test_transport_rejects_uncontrollable_session_without_cli(client, transport_session, invalid):
    from huesync.models import VirtualPlayerType

    manager, session, exchange, coupling = transport_session
    if invalid == "inactive":
        manager._active = None
    elif invalid == "airplay":
        session.player_type = VirtualPlayerType.AIRPLAY
    elif invalid == "missing_target":
        session.follower._follow_mac = ""
    elif invalid == "self":
        session.follower._follow_mac = session.profile.player_mac
    elif invalid == "managed":
        client._storage.save_virtual_player(VirtualPlayer(player_mac="aa:bb:cc:dd:ee:ff"))
    elif invalid == "stopping":
        session.stopping = True
    elif invalid == "invalid_mac":
        session.follower._follow_mac = "aa:bb:cc:dd:ee:ff\nstop"
    elif invalid == "no_follower":
        session.follower = None
    elif invalid == "other_coupling":
        coupling = Coupling()
        client._storage.save_coupling(coupling)
    response = client.post(f"/api/couplings/{coupling.id}/transport", json={"action": "stop"})
    assert response.status_code == 409
    exchange.assert_not_called()


def test_transport_missing_coupling_invalid_action_and_cli_failure(client, transport_session):
    _, _, exchange, coupling = transport_session
    response = client.post("/api/couplings/missing/transport", json={"action": "stop"})
    assert response.status_code == 404
    assert client.post(f"/api/couplings/{coupling.id}/transport",
                       json={"action": "arbitrary"}).status_code == 422
    assert client.post(f"/api/couplings/{coupling.id}/transport",
                       json={"action": "stop", "mac": "other"}).status_code == 422
    exchange.assert_not_called()
    exchange.side_effect = OSError("offline")
    response = client.post(f"/api/couplings/{coupling.id}/transport", json={"action": "stop"})
    assert response.status_code == 502
    assert response.json()["detail"] == "LMS transport command failed"


def test_transport_status_uses_current_target_and_matching_name(client, transport_session):
    manager, session, _, _ = transport_session
    manager._detected_sync_master = session.follower.target_mac
    manager._detected_sync_master_name = "Living room"
    status = _read_ws_status(client)
    assert status["active_player_type"] == "LMS"
    assert status["follow_target_mac"] == "aa:bb:cc:dd:ee:ff"
    assert status["follow_target_name"] == "Living room"
    session.follower._follow_mac = "aa:bb:cc:dd:ee:00"
    assert manager.follow_target_name == "aa:bb:cc:dd:ee:00"  # not the stale room name
